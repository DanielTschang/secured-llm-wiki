import pytest

from kc_graph import SpaceGraph, StaleGraphWrite, WrongSpaceGraph
from kc_graph.testing import MemoryGraph
from kc_ids import PageId, Revision
from kc_labels import Labels, SpaceId

OPC = SpaceId("sp_opc")


def test_fenced_upsert() -> None:
    g = SpaceGraph(OPC, MemoryGraph())
    g.upsert_source_page(PageId("opc_o1"), Revision(2), Labels.of([OPC]))
    assert g.source_page_revision(PageId("opc_o1")) == 2
    for stale in (1, 2):
        with pytest.raises(StaleGraphWrite):
            g.upsert_source_page(PageId("opc_o1"), Revision(stale), Labels.of([OPC]))
    g.upsert_source_page(PageId("opc_o1"), Revision(3), Labels.of([OPC]))
    assert g.source_page_revision(PageId("opc_o1")) == 3


@pytest.mark.parametrize("labels", [["sp_cd"], ["sp_opc", "sp_cd"]])
def test_foreign_labels_abort(labels: list[str]) -> None:
    with pytest.raises(WrongSpaceGraph):
        SpaceGraph(OPC, MemoryGraph()).upsert_source_page(
            PageId("opc_o1"), Revision(1), Labels.of(labels)
        )
