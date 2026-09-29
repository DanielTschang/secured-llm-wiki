"""GraphBackend on one space's Neo4j instance."""

from typing import Any

from neo4j import Driver

from kc_graph import Edge, Similar

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

    def replace_wiki_graph(self, pages: list[dict[str, Any]], edges: list[Edge]) -> None:
        with self._driver.session() as session:  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            session.execute_write(_replace_wiki, pages, edges)

    def wiki_edges(self) -> list[Edge]:
        records, _, _ = self._driver.execute_query(
            "MATCH (a:Wiki)-[r:REL]->(b:Wiki) "
            "RETURN a.kind AS ak, a.key AS a, r.type AS rel, b.kind AS bk, b.key AS b"
        )
        return [(r["ak"], r["a"], r["rel"], r["bk"], r["b"]) for r in records]

    def write_metrics(
        self, props: dict[str, dict[str, float | int]], similar: list[Similar]
    ) -> None:
        rows = [{"key": k, **v} for k, v in props.items()]
        self._driver.execute_query(
            "UNWIND $rows AS row MATCH (p:Wiki {kind: 'page', key: row.key}) "
            "SET p.degree = row.degree, p.community = row.community",
            rows=rows,
        )
        self._driver.execute_query("MATCH (:Wiki)-[s:SIMILAR]-(:Wiki) DELETE s")
        self._driver.execute_query(
            "UNWIND $rows AS row MATCH (a:Wiki {kind: 'page', key: row.a}) "
            "MATCH (b:Wiki {kind: 'page', key: row.b}) MERGE (a)-[s:SIMILAR]->(b) "
            "SET s.adamic_adar = row.score",
            rows=[{"a": a, "b": b, "score": sc} for a, b, sc in similar],
        )

    def close(self) -> None:
        self._driver.close()


def _replace_wiki(tx: Any, pages: list[dict[str, Any]], edges: list[Edge]) -> None:
    """Wiki nodes share the :Wiki label keyed by (kind, key); relation names are stored as
    a property because Cypher cannot parameterise relationship types."""
    tx.run("MATCH (n:Wiki) DETACH DELETE n")
    tx.run(
        "UNWIND $pages AS p MERGE (n:Wiki {kind: 'page', key: p.path}) "
        "SET n.page_kind = p.kind, n.concept_id = p.concept_id",
        pages=pages,
    )
    tx.run(
        "UNWIND $edges AS e "
        "MERGE (a:Wiki {kind: e.ak, key: e.a}) MERGE (b:Wiki {kind: e.bk, key: e.b}) "
        "MERGE (a)-[:REL {type: e.rel}]->(b)",
        edges=[{"ak": ak, "a": a, "rel": rel, "bk": bk, "b": b} for ak, a, rel, bk, b in edges],
    )
