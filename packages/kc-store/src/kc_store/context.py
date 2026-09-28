"""SpaceContext: the only way ingest code reaches data. Bound to exactly one space;
built from that space's Vault credentials, so it cannot reach another space even by bug."""

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from neo4j import Driver, GraphDatabase
from pymongo import MongoClient

from kc_graph import SpaceGraph
from kc_graph.neo4j import Neo4jGraph
from kc_labels import Labels, SpaceId
from kc_store.mongo import MongoDocs
from kc_store.s3 import S3Blobs
from kc_store.space import SpaceStore, WrongSpace
from kc_store.vault import VaultClient, VaultKeyService

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

__all__ = ["Endpoints", "SpaceContext", "open_space", "open_store"]


@dataclass(frozen=True, slots=True)
class Endpoints:
    """Where services live. Defaults are the in-cluster names; tests override via env."""

    mongo: str = field(default_factory=lambda: os.environ.get("KC_MONGO_HOST", "kc-mongodb:27017"))
    s3: str | None = field(default_factory=lambda: os.environ.get("KC_S3_ENDPOINT"))
    neo4j: str | None = field(default_factory=lambda: os.environ.get("KC_NEO4J_URI"))


@dataclass(frozen=True, slots=True)
class SpaceContext:
    space_id: SpaceId
    store: SpaceStore
    graph: SpaceGraph
    _closers: tuple[Any, ...] = field(default=(), repr=False)

    @property
    def labels(self) -> Labels:
        return self.store.labels

    def assert_single_space(self, inputs: Iterable[Labels]) -> None:
        """Every input to an ingest step must carry exactly this space's labels."""
        for labels in inputs:
            if labels != self.labels:
                raise WrongSpace

    def close(self) -> None:
        for c in self._closers:
            c.close()

    def __enter__(self) -> SpaceContext:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def open_store(
    space_id: SpaceId, vault: VaultClient, endpoints: Endpoints | None = None
) -> tuple[SpaceStore, MongoClient[dict[str, Any]]]:
    """One space's document and object store, from that space's Vault credentials.
    Returns the Mongo client so the caller can close it."""
    ep = endpoints or Endpoints()
    user, password = vault.database_creds(space_id)
    mongo: MongoClient[dict[str, Any]] = MongoClient(
        f"mongodb://{ep.mongo}/", username=user, password=password, authSource="admin",
        serverSelectionTimeoutMS=10_000,
    )  # fmt: skip
    s3_cfg = vault.kv(f"spaces/{space_id}/s3")
    s3: S3Client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
        "s3",
        endpoint_url=ep.s3 or s3_cfg["endpoint"],
        aws_access_key_id=s3_cfg["access_key"],
        aws_secret_access_key=s3_cfg["secret_key"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 2}),
    )
    store = SpaceStore(
        space_id,
        keys=VaultKeyService(vault),
        docs=MongoDocs(mongo[f"kc_{space_id}"]),
        blobs=S3Blobs(s3, s3_cfg["raw_bucket"]),
    )
    return store, mongo


def open_space(
    space_id: SpaceId, vault: VaultClient, endpoints: Endpoints | None = None
) -> SpaceContext:
    ep = endpoints or Endpoints()
    store, mongo = open_store(space_id, vault, ep)
    neo = vault.kv(f"spaces/{space_id}/neo4j")
    driver: Driver = GraphDatabase.driver(  # pyright: ignore[reportUnknownMemberType]
        ep.neo4j or neo["uri"], auth=(neo["username"], neo["password"])
    )
    graph_backend = Neo4jGraph(driver)
    return SpaceContext(
        space_id, store, SpaceGraph(space_id, graph_backend), (mongo, graph_backend)
    )
