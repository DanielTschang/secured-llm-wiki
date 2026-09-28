"""Per-space graph (one Neo4j instance per space, ADR-009). Graph statistics are only
ever computed inside one space's instance."""

from typing import Protocol

from kc_ids import PageId, Revision
from kc_labels import Labels, SpaceId

__all__ = ["GraphBackend", "SpaceGraph", "StaleGraphWrite", "WrongSpaceGraph"]


class StaleGraphWrite(Exception):
    """The graph already holds this revision or a newer one."""


class WrongSpaceGraph(Exception):
    """Labels do not match this graph's space. Abort, never fix up."""


class GraphBackend(Protocol):
    def upsert_source_page_if_newer(self, page_id: str, revision: int) -> bool: ...

    def source_page_revision(self, page_id: str) -> int | None: ...


class SpaceGraph:
    def __init__(self, space_id: SpaceId, backend: GraphBackend) -> None:
        self.space_id = space_id
        self.labels = Labels.of([space_id])
        self._backend = backend

    def upsert_source_page(self, page_id: PageId, revision: Revision, labels: Labels) -> None:
        if labels != self.labels:
            raise WrongSpaceGraph
        if not self._backend.upsert_source_page_if_newer(page_id, int(revision)):
            raise StaleGraphWrite

    def source_page_revision(self, page_id: PageId) -> int | None:
        return self._backend.source_page_revision(page_id)
