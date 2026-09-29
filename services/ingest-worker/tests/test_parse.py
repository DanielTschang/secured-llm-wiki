import pytest
from testset import GOLD, MANIFEST, load_page

from ingest_worker.steps.parse import ImageSegment, IncludeRef, TextSegment, course_id, parse_page
from kc_labels import Labels

PAGES = [p["page_id"] for p in MANIFEST["pages"]]


@pytest.mark.parametrize("page_id", PAGES)
def test_slide_count_and_refs(page_id: str) -> None:
    page, md, _ = load_page(page_id)
    slides = parse_page(page, md)
    meta = next(p for p in MANIFEST["pages"] if p["page_id"] == page_id)
    assert len(slides) == meta["slide_count"]
    assert [s.slide_ref for s in slides] == [f"{page_id}#{n}" for n in range(1, len(slides) + 1)]
    for s in slides:
        assert s.labels == Labels.of([page.space_id])
        assert s.page_revision == page.revision


def test_every_gold_slide_exists() -> None:
    refs = {s.slide_ref for p in PAGES for s in parse_page(*load_page(p)[:2])}
    assert set(GOLD) <= refs


def test_images_map_to_attachment_ids_in_order() -> None:
    page, md, _ = load_page("common_c1")
    slide = parse_page(page, md)[3]  # "## 4. MEEF 的定義": text, image, text
    kinds = [type(seg) for seg in slide.segments]
    assert kinds == [TextSegment, ImageSegment, TextSegment]
    names = dict(page.attachment_map)
    assert slide.figures == (names["c1_meef.png"],)
    assert "MEEF = 晶圓 CD 變化量" in slide.text
    assert "pitch 越小" in slide.text


def test_include_is_a_reference_and_not_expanded() -> None:
    page, md, _ = load_page("opc_o2")
    slide = parse_page(page, md)[3]
    assert slide.includes == (IncludeRef(page_id="cd_d1", section="4"),)
    assert "{{include" not in slide.text
    assert "cd_d1" not in slide.text  # the model sees a neutral marker only
    assert "量測 13 個" not in slide.text  # cd_d1 slide 4 content never appears


def test_platform_links_keep_text_only() -> None:
    page, md, _ = load_page("opc_o2")
    slide = parse_page(page, md)[3]
    assert "CD 團隊的 contact 量測頁" in slide.text
    assert "platform://" not in slide.text


def test_table_kept_as_text() -> None:
    page, md, _ = load_page("cd_d1")
    slide = parse_page(page, md)[4]
    assert "| CD-SEM recipe | R-CT-114 |" in slide.text
    assert slide.figures == ()


def test_titles() -> None:
    page, md, _ = load_page("common_c1")
    assert parse_page(page, md)[1].title == "Bossung curve"


def test_unknown_image_is_not_resolved() -> None:
    page, md, _ = load_page("common_c1")
    slides = parse_page(page, md.replace("c1_bossung.png", "../../sp_cd/attachments/x.png"))
    assert slides[1].figures == ()
    assert "../../sp_cd" not in slides[1].text


def test_course_grouping_by_parent() -> None:
    o1, _, _ = load_page("opc_o1")
    o2, _, _ = load_page("opc_o2")
    assert course_id(o1) == course_id(o2) == "opc_courses"
