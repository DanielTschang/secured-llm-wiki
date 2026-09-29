"""End to end in the local cluster: sync -> encrypted storage -> ID-only events -> ingest
subprocesses. Checks the M1 criteria against the real deployment."""

import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import boto3
import httpx
import pytest
from pymongo import MongoClient

from tests.leak.harness import assert_no_content, assert_no_foreign_content, load_manifest
from tests.support.cluster import NAMESPACE, kubectl, local_cluster_ok, port_forward

pytestmark = pytest.mark.infra

SPACES = ["sp_common", "sp_opc", "sp_cd"]
ROOT_TOKEN = "root"  # Vault dev mode
MINIO_ROOT = ("kc-minio-root", "kc-dev-minio-root")


@pytest.fixture(scope="module")
def ports() -> Iterator[dict[str, int]]:
    if not local_cluster_ok():
        if os.environ.get("KC_REQUIRE_INFRA") == "1":
            pytest.fail("local cluster not available")
        pytest.skip("local cluster not available (make kind-up)")
    with ExitStack() as stack:
        yield {
            "vault": stack.enter_context(port_forward("kc-vault", 8200)),
            "mongo": stack.enter_context(port_forward("kc-mongodb", 27017)),
            "minio": stack.enter_context(port_forward("kc-minio", 9000)),
        }


def space_db(ports: dict[str, int], space: str) -> Any:
    """Read access to one space's database with that space's own dynamic credentials."""
    creds = httpx.get(
        f"http://127.0.0.1:{ports['vault']}/v1/database/creds/{space}",
        headers={"X-Vault-Token": ROOT_TOKEN},
    ).json()["data"]
    client: MongoClient[dict[str, Any]] = MongoClient(
        f"mongodb://127.0.0.1:{ports['mongo']}/",
        username=creds["username"], password=creds["password"], authSource="admin",
        directConnection=True,
    )  # fmt: skip
    return client[f"kc_{space}"]


def run_sync() -> str:
    name = f"kc-sync-e2e-{uuid.uuid4().hex[:6]}"
    kubectl("-n", NAMESPACE, "create", "job", "--from=cronjob/kc-sync", name)
    kubectl("-n", NAMESPACE, "wait", "--for=condition=complete", "--timeout=300s", f"job/{name}")
    return kubectl("-n", NAMESPACE, "logs", f"job/{name}")


def space_reports(logs: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in logs.splitlines():
        if '"space_synced"' in line:
            rec = json.loads(line[line.index("{") :])
            out[rec["space_id"]] = rec
    return out


def wait_ingested(
    ports: dict[str, int], expected: dict[str, set[str]]
) -> dict[str, dict[str, int]]:
    """Wait until every page's ingest run matches its stored revision."""
    # Real model calls for steps 1-7 (three spaces share one on-host model server).
    deadline = time.monotonic() + 3600
    while True:
        revisions: dict[str, dict[str, int]] = {}
        done = True
        for space, page_ids in expected.items():
            db = space_db(ports, space)
            pages = {d["_id"]: d["revision"] for d in db["source_pages"].find({}, {"revision": 1})}
            runs = {d["_id"]: d for d in db["ingest_runs"].find()}
            revisions[space] = pages
            for pid in page_ids:
                run = runs.get(pid)
                if (
                    pid not in pages
                    or run is None
                    or run["revision"] != pages[pid]
                    or run["status"] not in {"wiki_done", "wiki_partial", "read_partial"}
                ):
                    done = False
        if done:
            return revisions
        if time.monotonic() > deadline:
            raise AssertionError(f"ingest did not complete: {revisions}")
        time.sleep(3)


def expected_pages() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {s: set() for s in SPACES}
    for p in load_manifest()["pages"]:
        out[p["space_id"]].add(p["page_id"])
    return out


def replay_task(ports: dict[str, int]) -> None:
    """Force one real ingest task: replay an already-ingested event as the sync user, and
    wait until the broker reports it finished (it ends as stale, doing no work)."""
    import asyncio

    import nats

    from kc_events import PageEvent

    http = httpx.Client(
        base_url=f"http://127.0.0.1:{ports['vault']}/v1/", headers={"X-Vault-Token": ROOT_TOKEN}
    )

    def finished() -> int:
        logs = kubectl("-n", NAMESPACE, "logs", "deploy/kc-ingest-worker", "-c", "broker")
        return logs.count('"task_finished", "space_id": "sp_opc", "page_id": "opc_o1"')

    before = finished()
    nats_password = http.get("kv/data/nats/sync").json()["data"]["data"]["password"]
    event = PageEvent.of(space_id="sp_opc", page_id="opc_o1", revision=1)

    async def replay(port: int) -> None:
        nc = await nats.connect(f"nats://127.0.0.1:{port}", user="sync", password=nats_password)
        try:
            await nc.jetstream().publish(event.subject, event.to_bytes(), timeout=5)
        finally:
            await nc.close()

    with port_forward("kc-nats", 4222) as nats_port:
        asyncio.run(replay(nats_port))
    deadline = time.monotonic() + 120
    while finished() == before:
        assert time.monotonic() < deadline, "replayed task did not run"
        time.sleep(2)


def test_sync_then_ingest_then_idempotent_resync(ports: dict[str, int]) -> None:
    expected = expected_pages()
    run_sync()
    before = wait_ingested(ports, expected)
    for space, pages in expected.items():
        assert set(before[space]) >= pages, f"{space} is missing pages"
        # Each space database holds only its own pages.
        assert set(before[space]) - pages <= {p for p in before[space] if p.startswith("it_")}

    reports = space_reports(run_sync())
    assert set(reports) == set(SPACES)
    for rec in reports.values():
        assert rec["new"] == 0 and rec["changed"] == 0, rec
        assert rec["publish_failed"] == 0, rec
    after = wait_ingested(ports, expected)
    assert after == before  # unchanged updated_date: no new revisions


def _all_objects(s3: Any, bucket: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        out += page.get("Contents", [])
    return out


def test_each_space_bucket_holds_only_its_own_content(ports: dict[str, int]) -> None:
    """ADR-012: objects are plaintext, so check the real invariant directly: raw pages,
    attachments, the OKF bundle (wiki/) and the LanceDB index of each space."""
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{ports['minio']}",
        aws_access_key_id=MINIO_ROOT[0],
        aws_secret_access_key=MINIO_ROOT[1],
        region_name="us-east-1",
    )
    seen = 0
    own: dict[str, str] = {}
    for space in SPACES:
        dns = space.replace("_", "-")
        for bucket in (f"kc-{dns}-raw", f"kc-{dns}-lance"):
            for obj in _all_objects(s3, bucket):
                key = obj.get("Key", "")
                if key.startswith(("pages/it_", "probe/")):
                    continue  # integration-test objects
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                text = body.decode("utf-8", errors="ignore")
                assert_no_foreign_content(text, space)
                own[space] = own.get(space, "") + text
                seen += 1
    assert seen >= 5 + 12  # 5 pages + their attachments at least
    # Positive controls: the comparison really sees content (it is plaintext, ADR-012).
    assert "KESTREL" in own["sp_opc"] and "R-CT-114" in own["sp_cd"]


def test_every_space_has_its_own_wiki_bundle(ports: dict[str, int]) -> None:
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{ports['minio']}",
        aws_access_key_id=MINIO_ROOT[0],
        aws_secret_access_key=MINIO_ROOT[1],
        region_name="us-east-1",
    )
    wait_ingested(ports, expected_pages())
    for space in SPACES:
        keys = [o.get("Key", "") for o in _all_objects(s3, f"kc-{space.replace('_', '-')}-raw")]
        wiki = [k for k in keys if k.startswith("wiki/")]
        assert "wiki/index.md" in wiki and "wiki/log.md" in wiki, space
        pages = [k for k in wiki if k not in ("wiki/index.md", "wiki/log.md")]
        assert pages, f"{space} has no wiki pages"
        for k in pages:  # opaque paths only (invariant 7)
            assert re.fullmatch(
                r"wiki/(concepts|entities|courses|synthesis)/[0-9A-HJKMNP-TV-Z]{26}\.md", k
            ), k


def test_each_space_graph_holds_only_its_own_nodes(ports: dict[str, int]) -> None:
    from neo4j import GraphDatabase

    wait_ingested(ports, expected_pages())
    http = httpx.Client(
        base_url=f"http://127.0.0.1:{ports['vault']}/v1/", headers={"X-Vault-Token": ROOT_TOKEN}
    )
    for space in SPACES:
        creds = http.get(f"kv/data/spaces/{space}/neo4j").json()["data"]["data"]
        with port_forward(f"kc-neo4j-{space.replace('_', '-')}", 7687) as port:  # noqa: SIM117
            with GraphDatabase.driver(
                f"bolt://127.0.0.1:{port}", auth=(creds["username"], creds["password"])
            ) as driver:
                records, _, _ = driver.execute_query(
                    "MATCH (n) RETURN coalesce(n.key, n.page_id, '') AS k, labels(n) AS l"
                )
        keys = [r["k"] for r in records]
        assert any(r["l"] == ["Wiki"] for r in records), f"{space} has no wiki graph"
        for other in SPACES:
            if other != space:
                assert not any(other in k for k in keys), f"{other} id in {space} graph"
        pages = {p["page_id"] for p in load_manifest()["pages"] if p["space_id"] != space}
        assert not any(k in pages for k in keys), f"another space's page in {space} graph"


def test_no_content_in_any_pod_log(ports: dict[str, int]) -> None:
    replay_task(ports)  # the positive control below must not depend on test order
    pods = kubectl(
        "-n", NAMESPACE, "get", "pods", "-o", "jsonpath={.items[*].metadata.name}"
    ).split()
    logs = "\n".join(
        kubectl("-n", NAMESPACE, "logs", pod, "--all-containers", "--tail=-1", check=False)
        for pod in pods
    )
    assert "task_finished" in logs and "space_synced" in logs  # positive control
    assert_no_content(logs)
    for p in load_manifest()["pages"]:
        for a in p["attachments"]:
            assert a.rsplit("/", 1)[1] not in logs, "attachment filename in pod logs"


def test_each_space_database_holds_only_its_own_content(ports: dict[str, int]) -> None:
    from bson import json_util

    seen = 0
    for space in SPACES:
        db = space_db(ports, space)
        dump = ""
        for coll in db.list_collection_names():
            for doc in db[coll].find():
                text = json_util.dumps(doc, ensure_ascii=False)
                assert_no_foreign_content(text, space)
                dump += text
                seen += 1
        # Positive control: this space's own titles are found in its own dump.
        for p in load_manifest()["pages"]:
            if p["space_id"] == space:
                assert p["title"] in dump, f"{space} dump lacks its own title"
    assert seen >= 10


def test_no_task_token_outlives_its_task(ports: dict[str, int]) -> None:
    """Force a real task, then check that no single-space token minted since is still
    valid: the task revoked its own."""
    http = httpx.Client(
        base_url=f"http://127.0.0.1:{ports['vault']}/v1/", headers={"X-Vault-Token": ROOT_TOKEN}
    )
    wait_ingested(ports, expected_pages())
    # Vault's own clock, so host/cluster skew cannot hide tokens.
    marker = http.post("auth/token/create", json={"ttl": "1m", "policies": ["default"]}).json()
    t0 = http.post("auth/token/lookup", json={"token": marker["auth"]["client_token"]}).json()[
        "data"
    ]["creation_time"]
    http.post("auth/token/revoke", json={"token": marker["auth"]["client_token"]})

    replay_task(ports)  # positive control: a task really ran and minted a token

    live = []
    for acc in http.request("LIST", "auth/token/accessors").json()["data"]["keys"]:
        info = http.post("auth/token/lookup-accessor", json={"accessor": acc})
        if info.status_code != 200:
            continue
        data = info.json()["data"]
        if data["creation_time"] >= t0 and any(
            p.startswith("space-") for p in data.get("policies") or []
        ):
            live.append((data["policies"], data["ttl"]))
    assert live == [], live


def test_every_slide_has_a_note_labelled_with_its_space(ports: dict[str, int]) -> None:
    """M2: after ingest, each page has one slide note per slide, in its own space only."""
    wait_ingested(ports, expected_pages())
    notes_seen = ok = 0
    for meta in load_manifest()["pages"]:
        db = space_db(ports, meta["space_id"])
        notes = list(db["slide_notes"].find({"page_id": meta["page_id"]}))
        assert len(notes) == meta["slide_count"], meta["page_id"]
        for n in notes:
            assert n["labels"] == [meta["space_id"]]
            assert n["_id"].startswith(meta["page_id"] + "#")
            ok += n["status"] == "ok"
        notes_seen += len(notes)
    assert notes_seen == sum(p["slide_count"] for p in load_manifest()["pages"])
    assert ok >= notes_seen * 0.8, f"only {ok}/{notes_seen} notes ok"
