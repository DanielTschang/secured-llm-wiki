"""Per-space search index (ingest step 7): one LanceDB table in the space's own lance
bucket. Vector search and full-text search (BM25) run over this space only, so term
statistics (IDF) never mix spaces (ADR-009). Rows carry their space ID and reads check
it, as defence in depth.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from kc_labels import SpaceId

__all__ = ["Chunk", "LanceIndex", "MemoryIndex", "SpaceIndex", "WrongIndexSpace"]


class WrongIndexSpace(Exception):
    """The index holds rows of another space. Abort."""


@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    kind: str  # "page" or "slide"
    ref: str  # bundle path or slide ref
    text: str = field(repr=False)
    vector: list[float] = field(repr=False)


class SpaceIndex(Protocol):
    def replace(self, chunks: list[Chunk]) -> None: ...

    def search_text(self, query: str, k: int) -> list[str]: ...

    def search_vector(self, vector: list[float], k: int) -> list[str]: ...


class LanceIndex:
    def __init__(
        self, space_id: SpaceId, uri: str, *, storage_options: dict[str, str], table: str = "chunks"
    ) -> None:
        self.space_id = space_id
        self._uri = uri
        self._options = storage_options
        self._table = table

    def _db(self) -> Any:
        import lancedb

        return lancedb.connect(self._uri, storage_options=self._options or None)  # pyright: ignore[reportUnknownMemberType]

    def replace(self, chunks: list[Chunk]) -> None:
        from lancedb.index import FTS

        rows = [
            {
                "id": c.id,
                "kind": c.kind,
                "ref": c.ref,
                "text": c.text,
                "vector": c.vector,
                "space_id": str(self.space_id),
            }
            for c in chunks
        ]
        db = self._db()
        if not rows:
            if self._table in db.table_names():
                db.drop_table(self._table)
            return
        table = db.create_table(self._table, data=rows, mode="overwrite")
        # ngram tokenizer: the texts are Chinese and English mixed.
        table.create_index("text", config=FTS(base_tokenizer="ngram"), replace=True)

    def _checked(self, rows: list[dict[str, Any]]) -> list[str]:
        if any(r["space_id"] != self.space_id for r in rows):
            raise WrongIndexSpace
        return [str(r["id"]) for r in rows]

    def _open(self) -> Any | None:
        db = self._db()
        return db.open_table(self._table) if self._table in db.table_names() else None

    def search_text(self, query: str, k: int) -> list[str]:
        table = self._open()
        if table is None:
            return []
        return self._checked(table.search(query, query_type="fts").limit(k).to_list())

    def search_vector(self, vector: list[float], k: int) -> list[str]:
        table = self._open()
        if table is None:
            return []
        return self._checked(table.search(vector).limit(k).to_list())


class MemoryIndex:
    """Brute-force stand-in for unit tests."""

    def __init__(self, space_id: SpaceId) -> None:
        self.space_id = space_id
        self.chunks: list[Chunk] = []

    def replace(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)

    def search_text(self, query: str, k: int) -> list[str]:
        def score(c: Chunk) -> int:
            return sum(query[i : i + 2] in c.text for i in range(max(1, len(query) - 1)))

        ranked = sorted((c for c in self.chunks if score(c)), key=score, reverse=True)
        return [c.id for c in ranked[:k]]

    def search_vector(self, vector: list[float], k: int) -> list[str]:
        def dist(c: Chunk) -> float:
            return sum((a - b) ** 2 for a, b in zip(c.vector, vector, strict=False))

        return [c.id for c in sorted(self.chunks, key=dist)[:k]]
