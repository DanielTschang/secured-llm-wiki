"""Adapters against the real services in the local kind cluster (via port-forward).

Run with `make integration` after `make kind-up`.
"""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime

import boto3
import httpx
import pytest

from kc_graph import StaleGraphWrite
from kc_ids import PageId, Revision
from kc_labels import Labels, SpaceId
from kc_store.context import Endpoints, SpaceContext, open_space
from kc_store.space import SourcePage, StaleWrite
from kc_store.testing import fake_attachment_id
from kc_store.vault import VaultClient, VaultError
from tests.support.cluster import local_cluster_ok, port_forward

pytestmark = pytest.mark.integration

OPC = SpaceId("sp_opc")
CD = SpaceId("sp_cd")
ROOT_TOKEN = "root"  # Vault dev mode (deploy/helm/kc/values.yaml)
MINIO_ROOT = ("kc-minio-root", "kc-dev-minio-root")


@pytest.fixture(scope="module")
def ports() -> Iterator[dict[str, int]]:
    if not local_cluster_ok():
        pytest.skip("local kind cluster not available (make kind-up)")
    with ExitStack() as stack:
        yield {
            name: stack.enter_context(port_forward(svc, port))
            for name, svc, port in [
                ("vault", "kc-vault", 8200),
                ("minio", "kc-minio", 9000),
                ("mongo", "kc-mongodb", 27017),
                ("neo4j_opc", "kc-neo4j-sp-opc", 7687),
                ("neo4j_cd", "kc-neo4j-sp-cd", 7687),
            ]
        }


MINTED: list[VaultClient] = []


def task_vault(ports: dict[str, int], space: SpaceId) -> VaultClient:
    """A single-space task token, minted the way the ingest broker does it. Revoked at the
    end of the module, like a real task revokes its token."""
    http = httpx.Client(base_url=f"http://127.0.0.1:{ports['vault']}")
    root = VaultClient(http, ROOT_TOKEN)
    task = root.with_token(root.child_token(f"space-{space}"))
    MINTED.append(task)
    return task


@pytest.fixture(scope="module", autouse=True)
def revoke_minted(ports: dict[str, int]) -> Iterator[None]:  # revoke before port-forward closes
    yield
    for task in MINTED:
        task.revoke_self()
    MINTED.clear()


def endpoints(ports: dict[str, int], neo4j: str) -> Endpoints:
    return Endpoints(
        mongo=f"127.0.0.1:{ports['mongo']}",
        s3=f"http://127.0.0.1:{ports['minio']}",
        neo4j=f"bolt://127.0.0.1:{ports[neo4j]}",
    )


@pytest.fixture(scope="module")
def opc(ports: dict[str, int]) -> Iterator[SpaceContext]:
    with open_space(OPC, task_vault(ports, OPC), endpoints(ports, "neo4j_opc")) as ctx:
        yield ctx


def page(page_id: PageId, rev: int, attachments: tuple[bytes, ...] = ()) -> SourcePage:
    return SourcePage(
        page_id=page_id,
        space_id=OPC,
        revision=Revision(rev),
        updated_date=datetime(2025, 8, 20, 9, tzinfo=UTC),
        content_hash="0" * 64,
        title="OPC 實務入門（2025 版）",
        parent_id="opc_courses",
        attachment_ids=tuple(fake_attachment_id(a) for a in attachments),
        labels=Labels.of([OPC]),
    )


def new_page_id() -> PageId:
    return PageId(f"it_{uuid.uuid4().hex[:12]}")


def test_roundtrip_in_own_bucket_only(opc: SpaceContext, ports: dict[str, int]) -> None:
    pid = new_page_id()
    png = b"\x89PNG integration KESTREL"
    opc.store.put_source_page(
        page(pid, 1, (png,)), "# KESTREL-7 設定", {fake_attachment_id(png): png}
    )
    assert opc.store.read_markdown(pid, Revision(1)) == "# KESTREL-7 設定"
    assert opc.store.read_attachment(pid, Revision(1), fake_attachment_id(png)) == png
    got = opc.store.get_source_page(pid)
    assert got is not None and got.title == "OPC 實務入門（2025 版）"

    # As MinIO root: the objects live in sp_opc's bucket, under ID-only keys.
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{ports['minio']}",
        aws_access_key_id=MINIO_ROOT[0],
        aws_secret_access_key=MINIO_ROOT[1],
        region_name="us-east-1",
    )
    listing = s3.list_objects_v2(Bucket="kc-sp-opc-raw", Prefix=f"pages/{pid}/")
    keys = [o.get("Key", "") for o in listing.get("Contents", [])]
    assert len(keys) == 2
    assert all(k.startswith(f"pages/{pid}/1/") for k in keys)
    for other in ("kc-sp-cd-raw", "kc-sp-common-raw"):
        assert not s3.list_objects_v2(Bucket=other, Prefix=f"pages/{pid}/").get("Contents")


def test_fencing_at_real_stores(opc: SpaceContext) -> None:
    pid = new_page_id()
    opc.store.put_source_page(page(pid, 2), "v2", {})
    with pytest.raises(StaleWrite):
        opc.store.put_source_page(page(pid, 1), "v1", {})
    with pytest.raises(StaleWrite):
        opc.store.put_source_page(page(pid, 2), "again", {})
    assert opc.store.read_markdown(pid, Revision(2)) == "v2"

    opc.graph.upsert_source_page(pid, Revision(2), opc.labels)
    with pytest.raises(StaleGraphWrite):
        opc.graph.upsert_source_page(pid, Revision(2), opc.labels)
    assert opc.graph.source_page_revision(pid) == 2


def test_blob_write_once_at_minio(opc: SpaceContext) -> None:
    # Bypass the Mongo pre-check: the bucket itself must refuse to overwrite.
    blobs = opc.store._blobs  # pyright: ignore[reportPrivateUsage]
    key = f"pages/{new_page_id()}/1/page.md"
    assert blobs.put_if_absent(key, b"first")
    assert not blobs.put_if_absent(key, b"second")
    assert blobs.get(key) == b"first"


def test_task_token_is_single_space(ports: dict[str, int]) -> None:
    v = task_vault(ports, OPC)
    for call in (
        lambda: v.database_creds(CD),
        lambda: v.kv(f"spaces/{CD}/s3"),
        lambda: v.kv(f"spaces/{CD}/neo4j"),
        lambda: v.hmac(CD, "AAAA"),
    ):
        with pytest.raises(VaultError) as exc:
            call()
        assert exc.value.status == 403
    # Positive control: its own space works.
    v.database_creds(OPC)
    v.hmac(OPC, "AAAA")
