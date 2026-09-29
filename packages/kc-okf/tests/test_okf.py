import pytest

from kc_okf import (
    HOST_FIELDS,
    Document,
    HostFields,
    Source,
    UnknownSource,
    build_frontmatter,
    clean_body,
    is_bundle_path,
    new_ulid,
    page_path,
    parse,
    render,
)

SOURCES = (
    Source("opc_o2-s2", "kc://sp_opc/pages/opc_o2?rev=1&slide=2", "2025-08-20T09:00:00Z"),
    Source("opc_o1-s2", "kc://sp_opc/pages/opc_o1?rev=1&slide=2", "2023-09-10T09:00:00Z"),
)


def host(**over: object) -> HostFields:
    base: dict[str, object] = dict(
        type="concept",
        sources=SOURCES,
        generated_by="model:qwen2.5vl:7b",
        generated_at="2026-09-29T00:00:00Z",
        status="draft",
        kc_labels=("sp_opc",),
        kc_concept="concept:meef",
    )
    base.update(over)
    return HostFields(**base)  # type: ignore[arg-type]


def test_roundtrip() -> None:
    fm = build_frontmatter(host(), {"title": "MEEF", "description": "d", "tags": ["opc"]})
    doc = Document(fm, "MEEF 門檻為 2.5。[^opc_o2-s2]\n")
    assert parse(render(doc)) == doc


def test_model_cannot_set_host_fields() -> None:
    llm = {
        "title": "MEEF",
        "description": "d",
        "tags": ["opc"],
        "kc_labels": ["sp_cd"],
        "status": "stable",
        "verified": {"by": "human:x"},
        "sources": [],
        "type": "entity",
        "kc_concept": "concept:other",
        "extra": "x",
    }
    fm = build_frontmatter(host(), llm)
    assert fm["kc_labels"] == ["sp_opc"]
    assert fm["status"] == "draft" and "verified" not in fm
    assert fm["type"] == "concept" and fm["kc_concept"] == "concept:meef"
    assert [s["id"] for s in fm["sources"]] == ["opc_o2-s2", "opc_o1-s2"]
    assert "extra" not in fm
    assert set(fm) <= set(HOST_FIELDS) | {"title", "description", "tags"}


@pytest.mark.parametrize("bad", [{"title": 3}, {"tags": "opc"}, {"tags": [1]}, {"title": ""}])
def test_llm_fields_are_validated(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        build_frontmatter(host(), {"title": "t", "description": "d", "tags": [], **bad})


def test_verified_only_with_stable() -> None:
    fm = build_frontmatter(
        host(status="stable", verified_by="kc-grounding/1", verified_at="2026-09-29T01:00:00Z"),
        {"title": "t", "description": "d", "tags": []},
    )
    assert fm["verified"] == {"by": "kc-grounding/1", "at": "2026-09-29T01:00:00Z"}
    with pytest.raises(ValueError):
        host(status="stable")  # stable requires verification
    with pytest.raises(ValueError):
        host(verified_by="human:someone", verified_at="x", status="stable")  # ADR-004


def test_unknown_footnote_rejected() -> None:
    with pytest.raises(UnknownSource):
        clean_body("說法。[^cd_d1-s4]", SOURCES, figures=frozenset(), resolve=lambda _n: None)


def test_footnote_definitions_from_the_model_are_replaced() -> None:
    body = clean_body(
        "門檻 2.5。[^opc_o2-s2]\n\n[^opc_o2-s2]: https://evil.example/",
        SOURCES,
        frozenset(),
        lambda _n: None,
    )
    assert "evil" not in body
    assert "[^opc_o2-s2]: kc://sp_opc/pages/opc_o2?rev=1&slide=2" in body


def test_images_only_from_allowed_figures() -> None:
    body = clean_body(
        "a ![ok](kc-figure://att_0123456789abcdef) b ![x](kc-figure://att_ffffffffffffffff)"
        " c ![y](https://cdn.example/x.png) d ![z](attachments/o2.png)[^opc_o2-s2]",
        SOURCES,
        figures=frozenset({"att_0123456789abcdef"}),
        resolve=lambda _n: None,
    )
    assert "kc-figure://att_0123456789abcdef" in body
    for gone in ("att_ffff", "cdn.example", "attachments/"):
        assert gone not in body


def test_links_resolve_only_inside_the_bundle() -> None:
    target = page_path("concept", "01J9Z3X8Q5W6E7R8T9Y0V1H2K3")
    body = clean_body(
        "見 [[MEEF]]、[[不存在]]、[外部](https://example.com)、[平台](platform://pages/cd_d1)"
        "、[本地](/concepts/01J9Z3X8Q5W6E7R8T9Y0V1H2K3.md)。[^opc_o2-s2]",
        SOURCES,
        frozenset(),
        resolve=lambda name: target if name == "MEEF" else None,
    )
    assert f"[MEEF](/{target})" in body
    assert "不存在" in body and "[[" not in body
    assert "example.com" not in body and "platform://" not in body
    assert "外部" in body and "平台" in body
    assert f"(/{target})" in body


def test_paths_are_opaque() -> None:
    u = new_ulid()
    assert len(u) == 26
    assert page_path("concept", u) == f"concepts/{u}.md"
    assert page_path("course", u).startswith("courses/")
    assert is_bundle_path(f"entities/{u}.md") and is_bundle_path("index.md")
    for bad in ("concepts/MEEF.md", "../x.md", f"concepts/{u}.md/../x", "other/x.md"):
        assert not is_bundle_path(bad)
