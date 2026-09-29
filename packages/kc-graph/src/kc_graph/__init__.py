"""Per-space graph (one Neo4j instance per space, ADR-009). Graph statistics are only
ever computed inside one space's instance."""

from typing import Any, Protocol

from kc_ids import PageId, Revision
from kc_labels import Labels, SpaceId

__all__ = ["Edge", "GraphBackend", "Similar", "SpaceGraph", "StaleGraphWrite", "WrongSpaceGraph"]


class StaleGraphWrite(Exception):
    """The graph already holds this revision or a newer one."""


class WrongSpaceGraph(Exception):
    """Labels do not match this graph's space. Abort, never fix up."""


# (source kind, source id, relation, target kind, target id); kinds: page, concept, slide
type Edge = tuple[str, str, str, str, str]
type Similar = tuple[str, str, float]


class GraphBackend(Protocol):
    def upsert_source_page_if_newer(self, page_id: str, revision: int) -> bool: ...

    def source_page_revision(self, page_id: str) -> int | None: ...

    def replace_wiki_graph(self, pages: list[dict[str, Any]], edges: list[Edge]) -> None: ...

    def wiki_edges(self) -> list[Edge]: ...

    def write_metrics(
        self, props: dict[str, dict[str, float | int]], similar: list[Similar]
    ) -> None: ...


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

    def replace_wiki_graph(
        self, pages: list[dict[str, Any]], edges: list[Edge], labels: Labels
    ) -> None:
        """The wiki part of the graph is an index of the bundle: rebuilt, not patched."""
        if labels != self.labels:
            raise WrongSpaceGraph
        self._backend.replace_wiki_graph(pages, edges)

    def wiki_edges(self) -> list[Edge]:
        return self._backend.wiki_edges()

    def write_metrics(
        self, props: dict[str, dict[str, float | int]], similar: list[Similar], labels: Labels
    ) -> None:
        if labels != self.labels:
            raise WrongSpaceGraph
        self._backend.write_metrics(props, similar)
