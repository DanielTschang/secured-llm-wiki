"""In-memory GraphBackend for unit tests."""

__all__ = ["MemoryGraph"]


class MemoryGraph:
    def __init__(self) -> None:
        self.pages: dict[str, int] = {}

    def upsert_source_page_if_newer(self, page_id: str, revision: int) -> bool:
        if self.pages.get(page_id, 0) >= revision:
            return False
        self.pages[page_id] = revision
        return True

    def source_page_revision(self, page_id: str) -> int | None:
        return self.pages.get(page_id)
