"""In-memory fakes for unit tests. Not for production use."""

import hashlib
import hmac
import os

from kc_ids import AttachmentId, attachment_id_from_digest
from kc_labels import SpaceId

__all__ = ["FakeKeyService", "MemoryBlobs", "MemoryDocs", "fake_attachment_id"]


def fake_attachment_id(data: bytes) -> AttachmentId:
    """Deterministic attachment ID for tests that do not care how IDs are keyed."""
    return attachment_id_from_digest(hashlib.sha256(data).hexdigest())


class FakeKeyService:
    """One HMAC key per space, like Vault transit."""

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def hmac(self, space_id: SpaceId, data: bytes) -> bytes:
        key = self._keys.setdefault(space_id, os.urandom(32))
        return hmac.new(key, data, hashlib.sha256).digest()


class MemoryDocs:
    """In-memory DocStore with the same fencing semantics as MongoDocs."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, object]]] = {}

    def dump(self) -> dict[str, list[dict[str, object]]]:
        return {c: [{"_id": k, **v} for k, v in d.items()] for c, d in self._data.items()}

    def get(self, collection: str, doc_id: str) -> dict[str, object] | None:
        doc = self._data.get(collection, {}).get(doc_id)
        return None if doc is None else {"_id": doc_id, **doc}

    def replace_if_revision_below(
        self, collection: str, doc_id: str, doc: dict[str, object]
    ) -> bool:
        coll = self._data.setdefault(collection, {})
        cur = coll.get(doc_id)
        if cur is not None and int(cur["revision"]) >= int(doc["revision"]):  # type: ignore[arg-type]
            return False
        coll[doc_id] = dict(doc)
        return True

    def update_if_revision_equals(
        self, collection: str, doc_id: str, revision: int, fields: dict[str, object]
    ) -> bool:
        cur = self._data.get(collection, {}).get(doc_id)
        if cur is None or cur["revision"] != revision:
            return False
        cur.update(fields)
        return True

    def upsert(self, collection: str, doc_id: str, doc: dict[str, object]) -> None:
        self._data.setdefault(collection, {})[doc_id] = dict(doc)


class MemoryBlobs:
    """In-memory BlobStore; objects are write-once like S3Blobs with If-None-Match."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def dump(self) -> dict[str, bytes]:
        return dict(self._data)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        if key in self._data:
            return False
        self._data[key] = data
        return True

    def get(self, key: str) -> bytes | None:
        return self._data.get(key)
