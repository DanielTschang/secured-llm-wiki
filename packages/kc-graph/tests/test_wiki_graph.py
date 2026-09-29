import pytest

from kc_graph import SpaceGraph, WrongSpaceGraph
from kc_graph.metrics import graph_metrics
from kc_graph.testing import MemoryGraph
from kc_labels import Labels, SpaceId

OPC = SpaceId("sp_opc")
PAGES = [
    {"path": "concepts/A.md", "kind": "concept", "concept_id": "concept:meef"},
    {"path": "concepts/B.md", "kind": "concept", "concept_id": "concept:dof"},
    {"path": "courses/C.md", "kind": "course", "concept_id": None},
    {"path": "entities/D.md", "kind": "entity", "concept_id": None},
]
EDGES = [
    ("page", "concepts/A.md", "LINKS_TO", "page", "concepts/B.md"),
    ("page", "courses/C.md", "LINKS_TO", "page", "concepts/A.md"),
    ("page", "courses/C.md", "LINKS_TO", "page", "concepts/B.md"),
    ("page", "concepts/A.md", "CITES", "slide", "opc_o2-s2"),
    ("page", "courses/C.md", "CITES", "slide", "opc_o2-s2"),
    ("page", "entities/D.md", "CITES", "slide", "opc_o2-s2"),  # no link to A
]


def test_replace_and_read_wiki_graph() -> None:
    g = SpaceGraph(OPC, MemoryGraph())
    g.replace_wiki_graph(PAGES, EDGES, Labels.of([OPC]))
    assert sorted(g.wiki_edges()) == sorted(EDGES)
    g.replace_wiki_graph(PAGES[:1], [], Labels.of([OPC]))
    assert g.wiki_edges() == []


def test_foreign_labels_abort() -> None:
    with pytest.raises(WrongSpaceGraph):
        SpaceGraph(OPC, MemoryGraph()).replace_wiki_graph(PAGES, EDGES, Labels.of(["sp_cd"]))


def test_metrics_from_one_space_graph() -> None:
    props, similar = graph_metrics(EDGES)
    assert props["courses/C.md"]["degree"] == 3
    assert props["concepts/A.md"]["degree"] == 3
    assert {p["community"] for p in props.values()} <= set(range(len(props)))
    # Adamic-Adar suggests links between pages that are not already linked.
    pairs = {frozenset((a, b)) for a, b, _ in similar}
    assert frozenset(("concepts/A.md", "entities/D.md")) in pairs
    assert frozenset(("concepts/A.md", "courses/C.md")) not in pairs  # already linked
    assert all(score > 0 for *_, score in similar)
    # only page nodes get metrics and similarity
    assert all(k.endswith(".md") for k in props)


def test_metrics_written_back() -> None:
    backend = MemoryGraph()
    g = SpaceGraph(OPC, backend)
    g.replace_wiki_graph(PAGES, EDGES, Labels.of([OPC]))
    props, similar = graph_metrics(g.wiki_edges())
    g.write_metrics(props, similar, Labels.of([OPC]))
    assert backend.page_props["courses/C.md"]["degree"] == 3
    assert backend.similar
