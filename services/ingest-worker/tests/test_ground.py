import json

import pytest

from ingest_worker.steps.ground import SlideMaterial, ground_page, split_sentences
from kc_labels import SpaceId
from kc_models import FakeModel
from kc_okf import Document, HostFields, Source, build_frontmatter, clean_body, parse, render
from kc_store.space import SpaceStore
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

SALT = "5a" * 16
OPC = SpaceId("sp_opc")
PATH = "concepts/01J9Z3X8Q5W6E7R8T9Y0V1H2K3.md"
SOURCES = (
    Source("opc_o2-s2", "kc://sp_opc/pages/opc_o2?rev=1&slide=2", "2025-08-20T09:00:00Z"),
    Source("opc_o1-s2", "kc://sp_opc/pages/opc_o1?rev=1&slide=2", "2023-09-10T09:00:00Z"),
)
BODY = (
    "## 本 team 實務\n"
    "現行 hotspot 門檻為 2.5。[^opc_o2-s2] 這代表規則變嚴格。[^opc_o2-s2]\n"
    "模型名稱是 ZZZ-9。[^opc_o2-s2]\n"
    "沒有來源的一句。\n\n"
    "## 版本演變\n2023 版門檻為 3.0。[^opc_o1-s2]\n"
)
MATERIAL = {
    "opc_o2-s2": SlideMaterial("今年起 MEEF 門檻收緊", (b"png-o2",)),
    "opc_o1-s2": SlideMaterial("Hotspot 檢查規則", (b"png-o1",)),
    "opc_o2-s3": SlideMaterial("無關投影片 KESTREL-7", (b"png-unrelated",)),
}


@pytest.fixture
def store() -> SpaceStore:
    s = SpaceStore(OPC, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs())
    host = HostFields(
        "concept", SOURCES, "model:m", "2026-09-29T00:00:00Z", "draft", ("sp_opc",), "concept:meef"
    )
    fm = build_frontmatter(host, {"title": "MEEF", "description": "d", "tags": []})
    body = clean_body(BODY, SOURCES, frozenset(), lambda _n: None)
    s.wiki_put(PATH, render(Document(fm, body)), None, s.labels)
    s.upsert_wiki_page(
        PATH,
        {"kind": "concept", "key": "concept:meef", "title": "MEEF", "status": "draft"},
        s.labels,
    )
    return s


def verdicts(*v: str) -> str:
    return json.dumps({"verdicts": [{"n": i + 1, "verdict": x} for i, x in enumerate(v)]})


def test_split_keeps_only_cited_sentences_as_checkable() -> None:
    sentences = split_sentences(BODY)
    assert [s.text for s in sentences if s.cited] == [
        "現行 hotspot 門檻為 2.5。[^opc_o2-s2]",
        "這代表規則變嚴格。[^opc_o2-s2]",
        "模型名稱是 ZZZ-9。[^opc_o2-s2]",
        "2023 版門檻為 3.0。[^opc_o1-s2]",
    ]


def test_verdicts_applied_and_page_becomes_stable(store: SpaceStore) -> None:
    model = FakeModel(lambda _m, _t: verdicts("supported", "inference", "unsupported", "supported"))
    assert ground_page(model, store, PATH, MATERIAL, cache_salt=SALT) == "stable"
    doc = parse(store.wiki_get(PATH)[0])  # type: ignore[index]
    assert "現行 hotspot 門檻為 2.5。" in doc.body
    assert "這代表規則變嚴格（推論）。" in doc.body
    assert "ZZZ-9" not in doc.body  # unsupported: removed
    assert "沒有來源" not in doc.body  # uncited: removed
    assert "2023 版門檻為 3.0" in doc.body
    assert doc.frontmatter["status"] == "stable"
    assert doc.frontmatter["verified"]["by"] == "kc-grounding/1"
    assert store.wiki_page_by_key("concept:meef")["status"] == "stable"  # type: ignore[index]


def test_missing_verdict_is_treated_as_unsupported(store: SpaceStore) -> None:
    model = FakeModel(lambda _m, _t: verdicts("supported"))
    ground_page(model, store, PATH, MATERIAL, cache_salt=SALT)
    body = parse(store.wiki_get(PATH)[0]).body  # type: ignore[index]
    assert "2.5" in body and "2023 版" not in body


def test_invalid_output_leaves_the_page_draft(store: SpaceStore) -> None:
    before = store.wiki_get(PATH)[0]  # type: ignore[index]
    model = FakeModel(lambda _m, _t: "garbage")
    assert ground_page(model, store, PATH, MATERIAL, cache_salt=SALT) == "draft"
    assert store.wiki_get(PATH)[0] == before  # type: ignore[index]


def test_only_cited_slides_are_shown_with_their_images(store: SpaceStore) -> None:
    model = FakeModel(lambda _m, _t: verdicts("supported", "supported", "supported", "supported"))
    ground_page(model, store, PATH, MATERIAL, cache_salt=SALT)
    call = model.calls[0]
    assert "今年起 MEEF 門檻收緊" in call.text() and "Hotspot 檢查規則" in call.text()
    assert "KESTREL-7" not in call.text()
    assert sorted(call.images()) == [b"png-o1", b"png-o2"]
    assert call.messages[0].parts[0].text == f"[{SALT}]"  # type: ignore[union-attr]
