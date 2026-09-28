"""Runs inside the cluster as the ingest-worker ServiceAccount (broker Vault role).

M1 criterion: a task subprocess for sp_opc, holding sp_opc's child token, cannot read
sp_cd's buckets, database, graph or keys. Every denial has a positive control on sp_opc.
"""

import uuid
from pathlib import Path

import boto3
import httpx
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from kc_labels import SpaceId
from kc_store.context import open_space
from kc_store.vault import VaultClient, VaultError

pytestmark = pytest.mark.in_cluster

OPC, CD = SpaceId("sp_opc"), SpaceId("sp_cd")
SA_TOKEN = Path("/var/run/secrets/kc/vault-token")


@pytest.fixture(scope="module")
def http() -> httpx.Client:
    return httpx.Client(base_url="http://kc-vault:8200", timeout=30)


@pytest.fixture(scope="module")
def broker(http: httpx.Client) -> VaultClient:
    return VaultClient.kubernetes_login(http, "ingest-worker", SA_TOKEN.read_text().strip())


@pytest.fixture(scope="module")
def opc(broker: VaultClient) -> VaultClient:
    return broker.with_token(broker.child_token(f"space-{OPC}"))


def denied(call: object) -> None:
    with pytest.raises(VaultError) as exc:
        call()  # type: ignore[operator]
    assert exc.value.status == 403


# --- Vault --------------------------------------------------------------------------


def test_task_token_cannot_use_other_space(opc: VaultClient) -> None:
    denied(lambda: opc.database_creds(CD))
    denied(lambda: opc.kv(f"spaces/{CD}/s3"))
    denied(lambda: opc.kv(f"spaces/{CD}/neo4j"))
    denied(lambda: opc.datakey(CD))
    denied(lambda: opc.decrypt(CD, "vault:v1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"))
    denied(lambda: opc.child_token(f"space-{CD}"))
    # Positive control: its own space works end to end.
    opc.database_creds(OPC)
    opc.kv(f"spaces/{OPC}/s3")
    plaintext, wrapped = opc.datakey(OPC)
    assert opc.decrypt(OPC, wrapped) == plaintext


def test_broker_token_reads_no_space_data(broker: VaultClient, http: httpx.Client) -> None:
    for space in (OPC, CD):
        denied(lambda s=space: broker.database_creds(s))
        denied(lambda s=space: broker.kv(f"spaces/{s}/s3"))
        denied(lambda s=space: broker.datakey(s))
    # It cannot mint a token with arbitrary policies either.
    resp = http.post(
        "/v1/auth/token/create",
        json={"policies": ["sync"]},
        headers={"X-Vault-Token": broker._token},  # pyright: ignore[reportPrivateUsage]
    )
    assert resp.status_code == 403
    assert broker.child_token(f"space-{OPC}")  # positive control


# --- object store ----------------------------------------------------------------------


def s3_for(vault: VaultClient, space: SpaceId):
    cfg = vault.kv(f"spaces/{space}/s3")
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


@pytest.mark.parametrize("bucket", ["kc-sp-cd-raw", "kc-sp-cd-lance"])
def test_other_space_buckets_denied(opc: VaultClient, bucket: str) -> None:
    s3 = s3_for(opc, OPC)
    for call in (
        lambda: s3.list_objects_v2(Bucket=bucket),
        lambda: s3.get_object(Bucket=bucket, Key="pages/cd_d1/1/page.md"),
        lambda: s3.put_object(Bucket=bucket, Key=f"probe/{uuid.uuid4().hex}", Body=b"x"),
    ):
        with pytest.raises(ClientError) as exc:
            call()
        assert exc.value.response.get("Error", {}).get("Code") == "AccessDenied"


def test_own_buckets_allowed(opc: VaultClient) -> None:
    s3 = s3_for(opc, OPC)
    s3.list_objects_v2(Bucket="kc-sp-opc-raw")
    key = f"probe/{uuid.uuid4().hex}"
    s3.put_object(Bucket="kc-sp-opc-lance", Key=key, Body=b"x")
    s3.delete_object(Bucket="kc-sp-opc-lance", Key=key)


# --- MongoDB -------------------------------------------------------------------------


def test_other_space_database_denied(opc: VaultClient) -> None:
    user, password = opc.database_creds(OPC)
    client: MongoClient[dict[str, object]] = MongoClient(
        "mongodb://kc-mongodb:27017/", username=user, password=password, authSource="admin"
    )
    with pytest.raises(OperationFailure):
        client[f"kc_{CD}"]["source_pages"].find_one()
    with pytest.raises(OperationFailure):
        client[f"kc_{CD}"]["probe"].insert_one({"x": 1})
    client[f"kc_{OPC}"]["source_pages"].find_one()  # positive control
    client.close()


# --- Neo4j ---------------------------------------------------------------------------


def test_other_space_graph_denied(opc: VaultClient) -> None:
    creds = opc.kv(f"spaces/{OPC}/neo4j")
    with (
        GraphDatabase.driver(
            "bolt://kc-neo4j-sp-cd:7687", auth=(creds["username"], creds["password"])
        ) as d,
        pytest.raises(AuthError),
    ):
        d.verify_connectivity()
    with GraphDatabase.driver(creds["uri"], auth=(creds["username"], creds["password"])) as d:
        d.verify_connectivity()  # positive control


# --- SpaceContext --------------------------------------------------------------------


def test_open_space_for_other_space_fails(opc: VaultClient) -> None:
    with pytest.raises(VaultError):
        open_space(CD, opc)
    with open_space(OPC, opc) as ctx:  # positive control
        assert ctx.space_id == OPC
