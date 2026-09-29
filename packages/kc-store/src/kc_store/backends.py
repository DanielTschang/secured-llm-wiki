"""Storage primitives SpaceStore is built on. Each instance is bound to one space's
database or bucket by its credentials; nothing here chooses a space."""

from typing import Any, Protocol

__all__ = ["BlobStore", "DocStore"]

type Doc = dict[str, Any]


class DocStore(Protocol):
    def get(self, collection: str, doc_id: str) -> Doc | None: ...

    def replace_if_revision_below(self, collection: str, doc_id: str, doc: Doc) -> bool:
        """Insert, or replace when the stored revision is lower than doc["revision"]."""
        ...

    def update_if_revision_equals(
        self, collection: str, doc_id: str, revision: int, fields: Doc
    ) -> bool: ...

    def upsert(self, collection: str, doc_id: str, doc: Doc) -> None: ...

    def find(self, collection: str, equals: Doc) -> list[Doc]:
        """Documents whose fields equal `equals` (a field holding a list matches if it
        contains the value), in _id order."""
        ...

    def delete(self, collection: str, doc_id: str) -> None: ...


class BlobStore(Protocol):
    def put_if_absent(self, key: str, data: bytes) -> bool:
        """Write only if the key does not exist; objects are never overwritten."""
        ...

    def get(self, key: str) -> bytes | None: ...

    def get_with_etag(self, key: str) -> tuple[bytes, str] | None: ...

    def put_if_match(self, key: str, data: bytes, etag: str | None) -> bool:
        """Write if the object's current ETag is `etag` (None: only if it does not exist)."""
        ...
