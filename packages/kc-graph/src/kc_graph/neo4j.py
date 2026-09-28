"""GraphBackend on one space's Neo4j instance."""

from neo4j import Driver

__all__ = ["Neo4jGraph"]

_UPSERT = """
MERGE (p:SourcePage {page_id: $page_id})
WITH p, coalesce(p.revision, 0) AS current
FOREACH (_ IN CASE WHEN current < $revision THEN [1] ELSE [] END | SET p.revision = $revision)
RETURN current < $revision AS applied
"""


class Neo4jGraph:
    def __init__(self, driver: Driver) -> None:
        self._driver = driver
        self._driver.execute_query(
            "CREATE CONSTRAINT source_page_id IF NOT EXISTS "
            "FOR (p:SourcePage) REQUIRE p.page_id IS UNIQUE"
        )

    def upsert_source_page_if_newer(self, page_id: str, revision: int) -> bool:
        records, _, _ = self._driver.execute_query(_UPSERT, page_id=page_id, revision=revision)
        return bool(records[0]["applied"])

    def source_page_revision(self, page_id: str) -> int | None:
        records, _, _ = self._driver.execute_query(
            "MATCH (p:SourcePage {page_id: $page_id}) RETURN p.revision AS r", page_id=page_id
        )
        return None if not records else int(records[0]["r"])

    def close(self) -> None:
        self._driver.close()
