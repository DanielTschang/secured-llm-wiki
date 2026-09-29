import tempfile

import pytest

from kc_labels import SpaceId
from kc_store.index import Chunk, LanceIndex, MemoryIndex, WrongIndexSpace

OPC = SpaceId("sp_opc")


def chunks() -> list[Chunk]:
    return [
        Chunk("p1", "page", "concepts/A.md", "光罩誤差放大因子 MEEF 門檻 2.5", [1.0, 0.0, 0.0]),
        Chunk("p2", "page", "concepts/B.md", "製程窗口與焦深 DOF", [0.0, 1.0, 0.0]),
        Chunk("s1", "slide", "opc_o2#2", "hotspot 規則 MEEF", [0.9, 0.1, 0.0]),
    ]


@pytest.fixture(params=["memory", "lance"])
def index(request: pytest.FixtureRequest):
    if request.param == "memory":
        return MemoryIndex(OPC)
    return LanceIndex(OPC, tempfile.mkdtemp(), storage_options={})


def test_text_and_vector_search(index) -> None:
    index.replace(chunks())
    assert index.search_text("誤差放大", 2)[0] == "p1"
    assert index.search_vector([1.0, 0.0, 0.0], 2) == ["p1", "s1"]


def test_replace_rebuilds_the_table(index) -> None:
    index.replace(chunks())
    index.replace(chunks()[:1])
    assert index.search_vector([0.0, 1.0, 0.0], 5) == ["p1"]


def test_rows_of_another_space_are_refused_on_read() -> None:
    root = tempfile.mkdtemp()
    LanceIndex(SpaceId("sp_cd"), root, storage_options={}).replace(chunks())
    with pytest.raises(WrongIndexSpace):
        LanceIndex(OPC, root, storage_options={}).search_text("MEEF", 3)
