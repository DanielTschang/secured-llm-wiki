"""Development bootstrap for the local kind cluster (idempotent).

Configures Vault (the stand-in for the out-of-cluster KMS), per-space MinIO users and
buckets, Neo4j passwords, and the NATS stream. Generated secrets are written only to Vault;
Helm receives hashes only (deploy/.generated/values.yaml).

    uv run python scripts/dev_bootstrap.py infra   # after helm phase 1
    uv run python scripts/dev_bootstrap.py post    # after helm phase 2 (NATS up)

Reaches the cluster only through `kubectl --context <ctx> port-forward`, after the guard.
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import bcrypt
import httpx
import yaml
from minio import Minio
from minio.credentials import StaticProvider
from minio.minioadmin import MinioAdmin
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError

REPO = Path(__file__).parents[1]
GUARD = REPO / "scripts/require-local-context.sh"
VALUES = REPO / "deploy/helm/kc/values.yaml"
GENERATED = REPO / "deploy/.generated/values.yaml"
NAMESPACE = "kc"
STREAM = "KC_PAGES"
SVC = f"{NAMESPACE}.svc.cluster.local"


def dns(space: str) -> str:
    return space.replace("_", "-")


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


# --- cluster access -----------------------------------------------------------


def guard(ctx: str) -> None:
    if subprocess.run([str(GUARD), ctx], check=False).returncode != 0:
        sys.exit("refusing to bootstrap a non-local cluster")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def port_forward(ctx: str, service: str, remote: int) -> Iterator[int]:
    local = free_port()
    proc = subprocess.Popen(
        ["kubectl", "--context", ctx, "-n", NAMESPACE, "port-forward",
         f"svc/{service}", f"{local}:{remote}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        for _ in range(100):
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", local), 0.2):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(f"port-forward to {service} did not come up")
        yield local
    finally:
        proc.terminate()
        proc.wait()


def spaces() -> list[str]:
    return [s["id"] for s in yaml.safe_load(VALUES.read_text())["spaces"]]


def dev_values() -> dict[str, str]:
    return yaml.safe_load(VALUES.read_text())["dev"]


# --- Vault ----------------------------------------------------------------------


class Vault:
    def __init__(self, port: int, token: str) -> None:
        self.http = httpx.Client(
            base_url=f"http://127.0.0.1:{port}/v1/", headers={"X-Vault-Token": token}, timeout=30
        )

    def req(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        resp = self.http.request(method, path, json=body)
        if resp.status_code >= 400 and resp.status_code != 404:
            raise RuntimeError(f"vault {method} {path}: {resp.status_code} {resp.text}")
        return resp

    def ensure_mount(self, path: str, kind: str, options: dict[str, str] | None = None) -> None:
        if f"{path}/" not in self.req("GET", "sys/mounts").json()["data"]:
            self.req("POST", f"sys/mounts/{path}", {"type": kind, "options": options or {}})

    def ensure_auth(self, path: str, kind: str) -> None:
        if f"{path}/" not in self.req("GET", "sys/auth").json()["data"]:
            self.req("POST", f"sys/auth/{path}", {"type": kind})

    def kv_get(self, path: str) -> dict[str, Any] | None:
        resp = self.req("GET", f"kv/data/{path}")
        return None if resp.status_code == 404 else resp.json()["data"]["data"]

    def kv_put(self, path: str, data: dict[str, Any]) -> None:
        self.req("POST", f"kv/data/{path}", {"data": data})

    def policy(self, name: str, hcl: str) -> None:
        self.req("PUT", f"sys/policies/acl/{name}", {"policy": hcl})


def space_policy(space: str) -> str:
    return f"""
path "transit/datakey/plaintext/{space}" {{ capabilities = ["update"] }}
path "transit/decrypt/{space}" {{ capabilities = ["update"] }}
path "database/creds/{space}" {{ capabilities = ["read"] }}
path "kv/data/spaces/{space}/*" {{ capabilities = ["read"] }}
"""


def configure_vault(v: Vault, all_spaces: list[str]) -> None:
    v.ensure_mount("transit", "transit")
    v.ensure_mount("kv", "kv", {"version": "2"})
    v.ensure_mount("database", "database")

    for s in all_spaces:
        if v.req("GET", f"transit/keys/{s}").status_code == 404:
            v.req("POST", f"transit/keys/{s}", {"type": "aes256-gcm96", "exportable": False})

    # MongoDB: Vault holds the root credential and rotates it away from the dev value.
    if v.req("GET", "database/config/mongo").status_code == 404:
        v.req(
            "POST",
            "database/config/mongo",
            {
                "plugin_name": "mongodb-database-plugin",
                "connection_url": f"mongodb://{{{{username}}}}:{{{{password}}}}@kc-mongodb.{SVC}:27017/admin",
                "username": "kc-root",
                "password": dev_values()["mongoInitialRootPassword"],
                "allowed_roles": [*all_spaces],
            },
        )
        v.req("POST", "database/rotate-root/mongo")
        log("mongo root handed to vault and rotated")
    else:
        v.req("POST", "database/config/mongo", {"allowed_roles": [*all_spaces]})
    for s in all_spaces:
        v.req(
            "POST",
            f"database/roles/{s}",
            {
                "db_name": "mongo",
                "creation_statements": json.dumps(
                    {"db": "admin", "roles": [{"role": "readWrite", "db": f"kc_{s}"}]}
                ),
                "default_ttl": "15m",
                "max_ttl": "1h",
            },
        )

    for s in all_spaces:
        v.policy(f"space-{s}", space_policy(s))
        # Child tokens for one ingest task: only this space's policy, short-lived, orphan.
        v.req(
            "POST",
            f"auth/token/roles/space-{s}",
            {
                "allowed_policies": [f"space-{s}"],
                "orphan": True,
                "renewable": False,
                "token_ttl": "5m",
                "token_max_ttl": "10m",
                "token_no_default_policy": True,
            },
        )
    v.policy(
        "sync",
        "".join(space_policy(s) for s in all_spaces)
        + '\npath "kv/data/platform/svc_sync" { capabilities = ["read"] }'
        + '\npath "kv/data/nats/sync" { capabilities = ["read"] }\n',
    )
    # The broker can mint single-space child tokens and read its NATS login, nothing else.
    v.policy(
        "ingest-broker",
        "".join(
            f'path "auth/token/create/space-{s}" {{ capabilities = ["update"] }}\n'
            for s in all_spaces
        )
        + 'path "kv/data/nats/ingest" { capabilities = ["read"] }\n',
    )

    v.ensure_auth("kubernetes", "kubernetes")
    v.req(
        "POST", "auth/kubernetes/config", {"kubernetes_host": "https://kubernetes.default.svc:443"}
    )
    for role, sa, policy, ttl in [
        ("sync", "kc-sync", "sync", "15m"),
        ("ingest-worker", "kc-ingest-worker", "ingest-broker", "1h"),
    ]:
        v.req(
            "POST",
            f"auth/kubernetes/role/{role}",
            {
                "bound_service_account_names": [sa],
                "bound_service_account_namespaces": [NAMESPACE],
                "audience": "vault",
                "token_policies": [policy],
                "token_ttl": ttl,
                "token_no_default_policy": True,
            },
        )
    log("vault configured")


# --- MinIO ----------------------------------------------------------------------


def bucket_policy(space: str) -> dict[str, Any]:
    buckets = [f"kc-{dns(space)}-raw", f"kc-{dns(space)}-lance"]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:*"],
                "Resource": [
                    r for b in buckets for r in (f"arn:aws:s3:::{b}", f"arn:aws:s3:::{b}/*")
                ],
            }
        ],
    }


def configure_minio(v: Vault, port: int, all_spaces: list[str]) -> None:
    dev = dev_values()
    root = StaticProvider(dev["minioRootUser"], dev["minioRootPassword"])
    s3 = Minio(f"127.0.0.1:{port}", credentials=root, secure=False)
    admin = MinioAdmin(endpoint=f"127.0.0.1:{port}", credentials=root, secure=False)
    for s in all_spaces:
        for suffix in ("raw", "lance"):
            b = f"kc-{dns(s)}-{suffix}"
            if not s3.bucket_exists(b):
                s3.make_bucket(b)
        admin.policy_add(f"space-{dns(s)}", policy=bucket_policy(s))
        existing = v.kv_get(f"spaces/{s}/s3")
        access = f"space-{dns(s)}"
        secret = existing["secret_key"] if existing else secrets.token_urlsafe(32)
        admin.user_add(access, secret)
        try:
            admin.attach_policy([f"space-{dns(s)}"], user=access)
        except Exception as e:  # already attached on reruns
            if "already" not in str(e).lower():
                raise
        v.kv_put(
            f"spaces/{s}/s3",
            {
                "endpoint": f"http://kc-minio.{SVC}:9000",
                "access_key": access,
                "secret_key": secret,
                "raw_bucket": f"kc-{dns(s)}-raw",
                "lance_bucket": f"kc-{dns(s)}-lance",
            },
        )
    log("minio buckets, users and policies configured")


# --- Neo4j ----------------------------------------------------------------------


def configure_neo4j(v: Vault, ctx: str, all_spaces: list[str]) -> None:
    for s in all_spaces:
        name = f"kc-neo4j-{dns(s)}"
        existing = v.kv_get(f"spaces/{s}/neo4j")
        with port_forward(ctx, name, 7687) as port:
            uri = f"bolt://127.0.0.1:{port}"
            if existing:
                with GraphDatabase.driver(uri, auth=("neo4j", existing["password"])) as d:
                    d.verify_connectivity()
                continue
            new = secrets.token_urlsafe(32)
            initial = dev_values()["neo4jInitialPassword"]
            with GraphDatabase.driver(uri, auth=("neo4j", initial)) as d:
                d.execute_query(
                    "ALTER CURRENT USER SET PASSWORD FROM $old TO $new",
                    old=initial, new=new, database_="system",
                )  # fmt: skip
            v.kv_put(
                f"spaces/{s}/neo4j",
                {"uri": f"bolt://{name}.{SVC}:7687", "username": "neo4j", "password": new},
            )
            try:
                with GraphDatabase.driver(uri, auth=("neo4j", initial)) as d:
                    d.verify_connectivity()
                raise RuntimeError("neo4j initial password still valid after rotation")
            except AuthError:
                pass
    log("neo4j passwords rotated into vault")


# --- platform client and NATS logins -----------------------------------------------


def secret_with_bcrypt(v: Vault, path: str) -> dict[str, str]:
    existing = v.kv_get(path)
    if existing:
        return existing
    pw = secrets.token_urlsafe(32)
    data = {"password": pw, "bcrypt": bcrypt.hashpw(pw.encode(), bcrypt.gensalt(11)).decode()}
    v.kv_put(path, data)
    return data


def configure_logins(v: Vault) -> dict[str, Any]:
    platform = v.kv_get("platform/svc_sync")
    if platform is None:
        platform = {"client_id": "svc_sync", "client_secret": secrets.token_urlsafe(32)}
        v.kv_put("platform/svc_sync", platform)
    nats = {u: secret_with_bcrypt(v, f"nats/{u}") for u in ("sync", "ingest", "admin")}
    return {
        "apps": {"enabled": True},
        "mockPlatform": {
            "svcSyncSecretSha256": hashlib.sha256(platform["client_secret"].encode()).hexdigest()
        },
        "nats": {f"{u}PasswordBcrypt": nats[u]["bcrypt"] for u in nats},
    }


# --- phases ---------------------------------------------------------------------


def phase_infra(ctx: str) -> None:
    all_spaces = spaces()
    with port_forward(ctx, "kc-vault", 8200) as vp:
        v = Vault(vp, dev_values()["vaultRootToken"])
        configure_vault(v, all_spaces)
        with port_forward(ctx, "kc-minio", 9000) as mp:
            configure_minio(v, mp, all_spaces)
        configure_neo4j(v, ctx, all_spaces)
        generated = configure_logins(v)
    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    GENERATED.write_text(yaml.safe_dump(generated))
    log(f"wrote {GENERATED.relative_to(REPO)} (hashes only)")


async def configure_nats(port: int, admin_password: str, all_spaces: list[str]) -> None:
    import nats
    from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StorageType, StreamConfig

    nc = await nats.connect(f"nats://127.0.0.1:{port}", user="admin", password=admin_password)
    js = nc.jetstream()
    config = StreamConfig(
        name=STREAM,
        subjects=["kc.page.>"],
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        max_msg_size=512,  # IDs only; anything larger is refused by the server
    )
    try:
        await js.stream_info(STREAM)
        await js.update_stream(config)
    except nats.js.errors.NotFoundError:
        await js.add_stream(config)
    for s in all_spaces:
        await js.add_consumer(
            STREAM,
            ConsumerConfig(
                durable_name=f"ingest-{dns(s)}",
                filter_subject=f"kc.page.{s}",
                ack_policy=AckPolicy.EXPLICIT,
                max_ack_pending=1,  # one ingest at a time per space
                ack_wait=600,
                max_deliver=5,
            ),
        )
    await nc.close()


def phase_post(ctx: str) -> None:
    with port_forward(ctx, "kc-vault", 8200) as vp:
        v = Vault(vp, dev_values()["vaultRootToken"])
        admin = v.kv_get("nats/admin")
        if admin is None:
            sys.exit("run the infra phase first")
    with port_forward(ctx, "kc-nats", 4222) as np:
        asyncio.run(configure_nats(np, admin["password"], spaces()))
    log("nats stream and per-space consumers configured")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["infra", "post"])
    parser.add_argument("--context", default="kind-kc")
    args = parser.parse_args()
    guard(args.context)
    {"infra": phase_infra, "post": phase_post}[args.phase](args.context)


if __name__ == "__main__":
    main()
