import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingest_worker.steps.align import align_concepts
from ingest_worker.steps.integrate import CourseSlide, integrate_course
from ingest_worker.steps.read import Resources
from kc_ids import PageId, Revision
from kc_models import FakeModel

RES = Resources.load(Path(__file__).parents[3] / "schema")
SALT = "5a" * 16


def slides() -> list[CourseSlide]:
    def s(ref: str, point: str, when: str) -> CourseSlide:
        page, n = ref.split("#")
        return CourseSlide(
            slide_ref=ref,
            page_id=PageId(page),
            page_revision=Revision(1),
            slide_no=int(n),
            course_version=datetime.fromisoformat(when).astimezone(UTC),
            note={"point": point, "figures": [], "claims": [point], "concepts": ["MEEF"]},
        )

    return [
        s("opc_o1#2", "hotspot MEEF 門檻 3.0", "2023-09-10T09:00:00Z"),
        s("opc_o2#2", "hotspot MEEF 門檻 2.5", "2025-08-20T09:00:00Z"),
    ]


def digest_json(**over: object) -> str:
    body = {
        "concepts": ["MEEF", "hotspot"],
        "claims": [
            {
                "text": "MEEF 門檻 2.5",
                "value": 2.5,
                "unit": None,
                "concepts": ["MEEF"],
                "sources": ["opc_o2#2"],
            },
            {
                "text": "MEEF 門檻 3.0",
                "value": 3.0,
                "unit": None,
                "concepts": ["MEEF"],
                "sources": ["opc_o1#2"],
            },
            {
                "text": "偷來的",
                "value": 3.4,
                "unit": None,
                "concepts": ["MEEF"],
                "sources": ["cd_d1#4"],
            },
            {"text": "沒有來源", "value": None, "unit": None, "concepts": [], "sources": []},
        ],
        "relations": [{"source": "hotspot", "target": "MEEF", "kind": "related"}],
        "entities": [{"name": "OPC 模型", "kind": "software"}],
        "summary": ["門檻由 3.0 收緊為 2.5"],
    }
    body.update(over)
    return json.dumps(body, ensure_ascii=False)


def test_claims_citing_outside_the_course_are_dropped_and_provenance_is_hosts() -> None:
    model = FakeModel(lambda _m, _t: digest_json())
    d = integrate_course(model, "OPC 課程", slides(), cache_salt=SALT)
    assert d is not None
    texts = [c.text for c in d.claims]
    assert texts == ["MEEF 門檻 2.5", "MEEF 門檻 3.0"]
    c = d.claims[0]
    assert c.provenance == (("opc_o2", 1, 2),)
    assert c.course_version == datetime(2025, 8, 20, 9, tzinfo=UTC)


def test_versions_are_given_to_the_model() -> None:
    model = FakeModel(lambda _m, _t: digest_json())
    integrate_course(model, "OPC 課程", slides(), cache_salt=SALT)
    text = model.calls[0].text()
    assert "2023-09-10" in text and "2025-08-20" in text
    assert model.calls[0].messages[0].parts[0].text == f"[{SALT}]"  # type: ignore[union-attr]


def test_invalid_output_twice_is_none() -> None:
    model = FakeModel(lambda _m, _t: "nope")
    assert integrate_course(model, "c", slides(), cache_salt=SALT) is None
    assert len(model.calls) == 2


def test_alias_match_is_deterministic_and_width_insensitive() -> None:
    model = FakeModel(lambda _m, _t: pytest.fail("no model call for known aliases"))  # type: ignore[arg-type]
    made: list[str] = []
    got = align_concepts(
        model, RES, ["MEEF", "光罩誤差放大因子", "ＭＥＥＦ", " meef ", "concept:dof", "DOF"],
        local_concept=lambda n: made.append(n) or "local:x",  # type: ignore[func-returns-value]
        cache_salt=SALT,
    )  # fmt: skip
    assert set(got.values()) == {"concept:meef", "concept:dof"}
    assert made == []


def test_model_may_only_choose_glossary_ids_or_none() -> None:
    reply = json.dumps(
        {
            "mappings": [
                {"name": "製程窗口寬度", "concept_id": "concept:process_window"},
                {"name": "KESTREL 規則", "concept_id": "none"},
                {"name": "奇怪的東西", "concept_id": "concept:invented_by_model"},
            ]
        },
        ensure_ascii=False,
    )
    model = FakeModel(lambda _m, _t: reply)
    made: list[str] = []

    def local(name: str) -> str:
        made.append(name)
        return f"local:sp_opc:{len(made):026d}"

    got = align_concepts(
        model,
        RES,
        ["製程窗口寬度", "KESTREL 規則", "奇怪的東西"],
        local_concept=local,
        cache_salt=SALT,
    )
    assert got["製程窗口寬度"] == "concept:process_window"
    assert got["KESTREL 規則"].startswith("local:sp_opc:")
    assert got["奇怪的東西"].startswith("local:sp_opc:")  # an invented ID is never accepted
    schema = model.calls[0].json_schema
    assert schema is not None
    enum = schema["properties"]["mappings"]["items"]["properties"]["concept_id"]["enum"]
    assert "none" in enum and "concept:meef" in enum


def test_glossary_file_is_never_written() -> None:
    path = Path(__file__).parents[3] / "schema/glossary/canonical_terms.json"
    before = path.read_bytes()
    model = FakeModel(lambda _m, _t: '{"mappings": []}')
    align_concepts(model, RES, ["全新概念"], local_concept=lambda _n: "local:x", cache_salt=SALT)
    assert path.read_bytes() == before


def test_terms_absent_from_the_evidence_are_dropped() -> None:
    """Only concepts whose name or a glossary alias appears in the slides are kept."""
    model = FakeModel(
        lambda _m, _t: (
            '{"mappings": [{"name": "製程窗口寬度", "concept_id": "concept:process_window"}]}'
        )
    )
    made: list[str] = []
    evidence = "MEEF = 晶圓 CD 變化量 ÷ 光罩 CD 變化量。pitch 越小 MEEF 越大。"
    got = align_concepts(
        model, RES,
        ["MEEF", "concept:serif", "Bossung curve", "製程窗口寬度", "憑空的新詞"],
        local_concept=lambda n: made.append(n) or f"local:sp_common:{n}",  # type: ignore[func-returns-value]
        cache_salt=SALT, evidence=evidence,
    )  # fmt: skip
    assert got == {"MEEF": "concept:meef"}
    assert made == []  # no local concept for a term that is not on the slides


def test_alias_in_the_evidence_is_enough() -> None:
    model = FakeModel(lambda _m, _t: '{"mappings": []}')
    got = align_concepts(
        model, RES, ["concept:meef"], local_concept=lambda _n: "x", cache_salt=SALT,
        evidence="contact layer 的光罩誤差放大因子實測偏高",
    )  # fmt: skip
    assert set(got.values()) == {"concept:meef"}


def test_glossary_aliases_found_in_the_evidence_are_added() -> None:
    """Deterministic: a public glossary alias that appears in the slides is a concept even
    if the model did not list it."""
    model = FakeModel(lambda _m, _t: '{"mappings": []}')
    got = align_concepts(
        model, RES, [], local_concept=lambda _n: "x", cache_salt=SALT,
        evidence="## 4. Contact layer 的光罩誤差放大因子\n可用的 DOF 由窗口決定。",
    )  # fmt: skip
    assert set(got.values()) == {"concept:meef", "concept:dof"}
    assert model.calls == []


def test_latin_aliases_need_word_boundaries() -> None:
    model = FakeModel(lambda _m, _t: '{"mappings": []}')
    got = align_concepts(
        model, RES, [], local_concept=lambda _n: "x", cache_salt=SALT,
        evidence="PROOF 與 DOFFSET 都不是 DOF 的縮寫",  # only the standalone DOF counts
    )  # fmt: skip
    assert set(got.values()) == {"concept:dof"}


def test_figure_readings_count_as_evidence() -> None:
    from ingest_worker.steps.align import figure_evidence

    ev = figure_evidence(
        [{"type": "overlay_vector_map", "reads": {"pattern": "magnification", "max_nm": 6.0}},
         {"type": "schematic", "reads": {"features": ["serif", "hammerhead"]}}]
    )  # fmt: skip
    model = FakeModel(lambda _m, _t: '{"mappings": []}')
    got = align_concepts(
        model, RES, [], local_concept=lambda _n: "x", cache_salt=SALT, evidence="\n".join(ev)
    )
    assert {"concept:hammerhead", "concept:serif"} <= set(got.values())
