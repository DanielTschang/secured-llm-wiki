"""In-memory GraphBackend for unit tests."""

from typing import Any

from kc_graph import Edge, Similar

__all__ = ["MemoryGraph"]


class MemoryGraph:
    def __init__(self) -> None:
        self.pages: dict[str, int] = {}
        self.wiki_pages: list[dict[str, Any]] = []
        self.edges: list[Edge] = []
        self.page_props: dict[str, dict[str, float | int]] = {}
        self.similar: list[Similar] = []

    def upsert_source_page_if_newer(self, page_id: str, revision: int) -> bool:
        if self.pages.get(page_id, 0) >= revision:
            return False
        self.pages[page_id] = revision
        return True

    def source_page_revision(self, page_id: str) -> int | None:
        return self.pages.get(page_id)

    def replace_wiki_graph(self, pages: list[dict[str, Any]], edges: list[Edge]) -> None:
        self.wiki_pages, self.edges = list(pages), list(edges)
        self.page_props, self.similar = {}, []

    def wiki_edges(self) -> list[Edge]:
        return list(self.edges)

    def write_metrics(
        self, props: dict[str, dict[str, float | int]], similar: list[Similar]
    ) -> None:
        self.page_props, self.similar = dict(props), list(similar)
