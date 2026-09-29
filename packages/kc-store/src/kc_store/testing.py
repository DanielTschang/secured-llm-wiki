"""In-memory fakes for unit tests. Not for production use."""

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kc_ids import AttachmentId, attachment_id_from_digest
from kc_labels import SpaceId
from kc_store.keys import KeyServiceError

__all__ = ["FakeKeyService", "MemoryBlobs", "MemoryDocs", "fake_attachment_id"]


def fake_attachment_id(data: bytes) -> AttachmentId:
    """Deterministic attachment ID for tests that do not care how IDs are keyed."""
    return attachment_id_from_digest(hashlib.sha256(data).hexdigest())


class FakeKeyService:
    """One master key per space, like Vault transit; can be restricted to some spaces."""

    def __init__(
        self, masters: dict[str, bytes] | None = None, allowed: frozenset[str] | None = None
    ):
        self._masters = masters if masters is not None else {}
        self._allowed = allowed
        self.data_keys_issued = 0

    def restricted_to(self, *spaces: SpaceId) -> FakeKeyService:
        return FakeKeyService(self._masters, frozenset(spaces))

    def _master(self, space: SpaceId) -> bytes:
        if self._allowed is not None and space not in self._allowed:
            raise KeyServiceError("permission denied")
        return self._masters.setdefault(space, AESGCM.generate_key(bit_length=256))

    def new_data_key(self, space_id: SpaceId) -> tuple[bytes, str]:
        self.data_keys_issued += 1
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        wrapped = nonce + AESGCM(self._master(space_id)).encrypt(nonce, dek, space_id.encode())
        return dek, "fake:v1:" + base64.b64encode(wrapped).decode()

    def hmac(self, space_id: SpaceId, data: bytes) -> bytes:
        return hmac.new(self._master(space_id), data, hashlib.sha256).digest()

    def unwrap(self, space_id: SpaceId, wrapped: str) -> bytes:
        raw = base64.b64decode(wrapped.removeprefix("fake:v1:"))
        try:
            return AESGCM(self._master(space_id)).decrypt(raw[:12], raw[12:], space_id.encode())
        except InvalidTag as e:
            raise KeyServiceError("unwrap failed") from e


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
