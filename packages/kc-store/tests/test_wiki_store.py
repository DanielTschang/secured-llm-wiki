from datetime import UTC, datetime

import pytest

from kc_ids import PageId, Revision
from kc_labels import Labels, SpaceId
from kc_store.space import SourcePage, SpaceStore, StaleWrite, WrongSpace
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

OPC = SpaceId("sp_opc")
LAB = Labels.of([OPC])
ULID = "01J9Z3X8Q5W6E7R8T9Y0V1H2K3"


@pytest.fixture
def s() -> SpaceStore:
    return SpaceStore(OPC, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs())


def test_wiki_pages_use_etag_fencing(s: SpaceStore) -> None:
    path = f"concepts/{ULID}.md"
    assert s.wiki_get(path) is None
    s.wiki_put(path, "v1", None, LAB)
    text, etag = s.wiki_get(path)  # type: ignore[misc]
    assert text == "v1"
    with pytest.raises(StaleWrite):
        s.wiki_put(path, "v1b", None, LAB)  # create over an existing page
    s.wiki_put(path, "v2", etag, LAB)
    with pytest.raises(StaleWrite):
        s.wiki_put(path, "v3", etag, LAB)  # stale etag
    assert s.wiki_get(path)[0] == "v2"  # type: ignore[index]


@pytest.mark.parametrize("path", ["concepts/MEEF.md", "../x.md", "wiki/index.md", "other/x.md"])
def test_wiki_paths_must_be_bundle_paths(s: SpaceStore, path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        s.wiki_put(path, "x", None, LAB)


def test_wiki_writes_check_labels(s: SpaceStore) -> None:
    with pytest.raises(WrongSpace):
        s.wiki_put(f"concepts/{ULID}.md", "x", None, Labels.of(["sp_opc", "sp_cd"]))


def test_claims_replaced_per_course(s: SpaceStore) -> None:
    c = {"text": "門檻 2.5", "value": 2.5, "unit": None, "concept_ids": ["concept:meef"],
         "provenance": [{"page_id": "opc_o2", "revision": 1, "slide_no": 2}]}  # fmt: skip
    s.replace_course_claims("opc_courses", [c, {**c, "text": "門檻 3.0", "value": 3.0}], LAB)
    assert len(s.course_claims("opc_courses")) == 2
    s.replace_course_claims("opc_courses", [c], LAB)
    claims = s.course_claims("opc_courses")
    assert [x["text"] for x in claims] == ["門檻 2.5"]
    assert claims[0]["labels"] == ["sp_opc"]
    assert s.claims_for_concept("concept:meef")[0]["text"] == "門檻 2.5"


def test_local_concepts_reused_by_normalised_name(s: SpaceStore) -> None:
    a = s.local_concept("Line-End Pullback")
    assert a.startswith("local:sp_opc:") and len(a) == len("local:sp_opc:") + 26
    assert s.local_concept("  line-end   pullback ") == a
    assert s.local_concept("另一個") != a
    assert s.local_concept_name(a) == "Line-End Pullback"


def test_wiki_index_documents(s: SpaceStore) -> None:
    s.upsert_wiki_page(f"concepts/{ULID}.md", {"kind": "concept", "concept_id": "concept:meef",
                       "title": "MEEF", "status": "draft"}, LAB)  # fmt: skip
    assert s.wiki_page_for_concept("concept:meef")["path"] == f"concepts/{ULID}.md"  # type: ignore[index]
    assert [p["title"] for p in s.wiki_pages()] == ["MEEF"]


def test_list_source_pages_by_course(s: SpaceStore) -> None:
    for pid, parent in (("opc_o1", "opc_courses"), ("opc_o2", "opc_courses"), ("x1", "other")):
        s.put_source_page(
            SourcePage(
                PageId(pid),
                OPC,
                Revision(1),
                datetime(2025, 1, 1, tzinfo=UTC),
                "0" * 64,
                "t",
                parent,
                (),
                LAB,
            ),
            "md",
            {},
        )
    assert [p.page_id for p in s.list_source_pages("opc_courses")] == ["opc_o1", "opc_o2"]
