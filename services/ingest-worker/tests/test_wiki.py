import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingest_worker.steps.integrate import Claim
from ingest_worker.steps.read import Resources
from ingest_worker.steps.wiki import PageSpec, write_index_and_log, write_page
from kc_labels import SpaceId
from kc_models import FakeModel
from kc_okf import is_bundle_path, parse
from kc_store.space import SpaceStore
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

RES = Resources.load(Path(__file__).parents[3] / "schema")
SALT = "5a" * 16
OPC = SpaceId("sp_opc")
V2023 = datetime(2023, 9, 10, 9, tzinfo=UTC)
V2025 = datetime(2025, 8, 20, 9, tzinfo=UTC)
FIG = "att_0123456789abcdef"


def claim(text: str, ref: str, when: datetime, value: float | None = None) -> Claim:
    page, n = ref.split("#")
    return Claim(text, value, None, ("concept:meef",), (ref,), ((page, 1, int(n)),), when)


def spec(**over: object) -> PageSpec:
    base: dict[str, object] = dict(
        kind="concept",
        key="concept:meef",
        concept_id="concept:meef",
        subject="MEEF",
        claims=(
            claim("hotspot MEEF 門檻 3.0", "opc_o1#2", V2023, 3.0),
            claim("hotspot MEEF 門檻 2.5", "opc_o2#2", V2025, 2.5),
        ),
        figures=((FIG, "opc_o2#2"),),
    )
    base.update(over)
    return PageSpec(**base)  # type: ignore[arg-type]


@pytest.fixture
def store() -> SpaceStore:
    return SpaceStore(OPC, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs())


def reply(body: str, **over: object) -> str:
    out = {"title": "MEEF", "description": "光罩誤差放大因子", "tags": ["opc"], "body": body}
    out.update(over)
    return json.dumps(out, ensure_ascii=False)


GOOD = (
    "## 定義\nMEEF 是光罩誤差放大因子。[^opc_o2-s2]\n\n"
    "## 本 team 實務\n現行 hotspot 門檻為 2.5。[^opc_o2-s2]\n"
    f"![規則](kc-figure://{FIG})\n\n"
    "## 版本演變\n2023 版門檻為 3.0。[^opc_o1-s2]\n"
)


def test_new_concept_page_with_host_frontmatter(store: SpaceStore) -> None:
    model = FakeModel(lambda _m, _t: reply(GOOD, kc_labels=["sp_cd"]))
    path = write_page(model, RES, store, spec(), cache_salt=SALT, model_id="qwen2.5vl:7b")
    assert path is not None and is_bundle_path(path) and path.startswith("concepts/")
    text, _ = store.wiki_get(path)  # type: ignore[misc]
    doc = parse(text)
    fm = doc.frontmatter
    assert fm["kc_labels"] == ["sp_opc"] and fm["status"] == "draft"
    assert fm["kc_concept"] == "concept:meef" and fm["type"] == "concept"
    assert fm["generated"]["by"] == "model:qwen2.5vl:7b"
    assert {s["id"] for s in fm["sources"]} == {"opc_o1-s2", "opc_o2-s2"}
    assert "kc://sp_opc/pages/opc_o2?rev=1&slide=2" in doc.body
    assert f"kc-figure://{FIG}" in doc.body
    entry = store.wiki_page_by_key("concept:meef")
    assert entry is not None and entry["path"] == path and entry["title"] == "MEEF"


def test_existing_page_is_given_to_the_model_and_updated(store: SpaceStore) -> None:
    first = write_page(
        FakeModel(lambda _m, _t: reply(GOOD)), RES, store, spec(), cache_salt=SALT, model_id="m"
    )
    model = FakeModel(lambda _m, _t: reply("補充。[^opc_o2-s2]\n" + GOOD))
    again = write_page(model, RES, store, spec(), cache_salt=SALT, model_id="m")
    assert again == first
    assert "MEEF 是光罩誤差放大因子" in model.calls[0].text()  # existing body shown
    assert "補充" in store.wiki_get(first)[0]  # type: ignore[index,arg-type]


def test_versions_and_template_are_given(store: SpaceStore) -> None:
    model = FakeModel(lambda _m, _t: reply(GOOD))
    write_page(model, RES, store, spec(), cache_salt=SALT, model_id="m")
    text = model.calls[0].text()
    assert "2023-09-10" in text and "2025-08-20" in text
    assert "版本演變" in text and "現行" in text
    assert "[^opc_o2-s2]" in text  # source ids offered for citation


def test_unknown_footnote_retried_then_page_skipped(store: SpaceStore) -> None:
    model = FakeModel(lambda _m, _t: reply("CD 實測 3.4。[^cd_d1-s4]\n"))
    assert write_page(model, RES, store, spec(), cache_salt=SALT, model_id="m") is None
    assert len(model.calls) == 2
    assert store.wiki_pages() == []


def test_extra_output_fields_are_rejected(store: SpaceStore) -> None:
    model = FakeModel(lambda _m, _t: reply(GOOD, status="stable", verified={"by": "x"}))
    path = write_page(model, RES, store, spec(), cache_salt=SALT, model_id="m")
    assert path is not None
    fm = parse(store.wiki_get(path)[0]).frontmatter  # type: ignore[index]
    assert fm["status"] == "draft" and "verified" not in fm


def test_figures_limited_to_cited_slide_attachments(store: SpaceStore) -> None:
    body = GOOD + "![偷](kc-figure://att_ffffffffffffffff)\n"
    path = write_page(
        FakeModel(lambda _m, _t: reply(body)), RES, store, spec(), cache_salt=SALT, model_id="m"
    )
    assert "att_ffff" not in store.wiki_get(path)[0]  # type: ignore[index,arg-type]


def test_links_resolve_to_existing_pages(store: SpaceStore) -> None:
    dof = write_page(
        FakeModel(lambda _m, _t: reply("DOF 定義。[^opc_o2-s2]\n", title="DOF")),
        RES, store, spec(key="concept:dof", concept_id="concept:dof", subject="DOF"),
        cache_salt=SALT, model_id="m",
    )  # fmt: skip
    body = GOOD + "\n## 相關概念\n[[DOF]]、[[不存在的頁]]。[^opc_o2-s2]\n"
    path = write_page(
        FakeModel(lambda _m, _t: reply(body)), RES, store, spec(), cache_salt=SALT, model_id="m"
    )
    text = store.wiki_get(path)[0]  # type: ignore[index,arg-type]
    assert f"[DOF](/{dof})" in text and "不存在的頁" in text and "[[" not in text


@pytest.mark.parametrize("kind", ["entity", "course", "synthesis"])
def test_other_page_kinds(store: SpaceStore, kind: str) -> None:
    s = spec(kind=kind, key=f"{kind}:x", concept_id=None, subject="主題")
    path = write_page(
        FakeModel(lambda _m, _t: reply(GOOD)), RES, store, s, cache_salt=SALT, model_id="m"
    )
    assert path is not None and path.startswith(
        {"entity": "entities/", "course": "courses/", "synthesis": "synthesis/"}[kind]
    )
    assert parse(store.wiki_get(path)[0]).frontmatter["type"] == kind  # type: ignore[index]


def test_index_and_log_are_host_generated(store: SpaceStore) -> None:
    path = write_page(
        FakeModel(lambda _m, _t: reply(GOOD)), RES, store, spec(), cache_salt=SALT, model_id="m"
    )
    write_index_and_log(store, entry="ingest | page opc_o2 rev 1 | 1 page", when=V2025)
    index = store.wiki_get("index.md")[0]  # type: ignore[index]
    assert f"[MEEF](/{path})" in index and "光罩誤差放大因子" in index
    write_index_and_log(store, entry="ingest | page opc_o1 rev 2 | 0 pages", when=V2025)
    log = store.wiki_get("log.md")[0]  # type: ignore[index]
    assert log.count("## [2025-08-20] ingest") == 2  # append-only


def test_superseded_claims_only_in_the_evolution_section(store: SpaceStore) -> None:
    old = claim("hotspot MEEF 門檻 3.0", "opc_o1#2", V2023, 3.0)
    new = claim("hotspot MEEF 門檻 2.5", "opc_o2#2", V2025, 2.5)
    s = spec(claims=(old.as_superseded(), new))
    out = {
        "title": "MEEF", "description": "d", "tags": [],
        "body": "## 本 team 實務\n現行門檻為 2.5。[^opc_o2-s2]\n",
        "evolution": "2023 版門檻為 3.0。[^opc_o1-s2]\n",
    }  # fmt: skip
    model = FakeModel(lambda _m, _t: json.dumps(out, ensure_ascii=False))
    path = write_page(model, RES, store, s, cache_salt=SALT, model_id="m")
    assert path is not None
    body = parse(store.wiki_get(path)[0]).body  # type: ignore[index]
    current, _, evolution = body.partition("## 版本演變")
    assert "2.5" in current and "3.0" not in current
    assert "3.0" in evolution
    text = model.calls[0].text()
    assert "現行" in text and "舊版" in text


def test_old_source_in_the_main_body_is_rejected(store: SpaceStore) -> None:
    old = claim("hotspot MEEF 門檻 3.0", "opc_o1#2", V2023, 3.0)
    new = claim("hotspot MEEF 門檻 2.5", "opc_o2#2", V2025, 2.5)
    out = {"title": "MEEF", "description": "d", "tags": [],
           "body": "門檻為 3.0。[^opc_o1-s2]\n", "evolution": ""}  # fmt: skip
    model = FakeModel(lambda _m, _t: json.dumps(out, ensure_ascii=False))
    s = spec(claims=(old.as_superseded(), new))
    assert write_page(model, RES, store, s, cache_salt=SALT, model_id="m") is None
    assert len(model.calls) == 2


def test_no_evolution_section_without_superseded_claims(store: SpaceStore) -> None:
    s = spec(claims=(claim("hotspot MEEF 門檻 2.5", "opc_o2#2", V2025, 2.5),))
    body = "門檻 2.5。[^opc_o2-s2]\n## 版本演變\n亂寫。[^opc_o2-s2]\n"
    out = {"title": "MEEF", "description": "d", "tags": [], "body": body, "evolution": ""}
    path = write_page(FakeModel(lambda _m, _t: json.dumps(out, ensure_ascii=False)), RES, store, s,
                      cache_salt=SALT, model_id="m")  # fmt: skip
    assert "版本演變" not in parse(store.wiki_get(path)[0]).body  # type: ignore[index,arg-type]
