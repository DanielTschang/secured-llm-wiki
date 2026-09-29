import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from testset import load_page

from ingest_worker.steps.parse import parse_page
from ingest_worker.steps.read import CourseContext, Resources, read_slide
from kc_labels import Labels
from kc_models import FakeModel, ImagePart, Message, TextPart

SCHEMA = Path(__file__).parents[3] / "schema"
RES = Resources.load(SCHEMA)


def ctx(prev: str | None = None) -> CourseContext:
    return CourseContext(course_title="課名", previous_point=prev, terms=("MEEF",))


def note_json(figures: list[dict[str, object]], **extra: object) -> str:
    return json.dumps(
        {
            "point": "重點",
            "figures": figures,
            "claims": ["c"],
            "concepts": ["concept:meef"],
            **extra,
        }
    )


def meef_fig() -> dict[str, object]:
    return {
        "type": "meef_plot",
        "reads": {"meef_pitch_90": 2.8},
        "numbers_from_figure": True,
        "confidence": 0.8,
    }


def script(classify: str, read: str | list[str]):
    reads = [read] if isinstance(read, str) else list(read)

    def respond(messages: Sequence[Message], tag: str) -> str:
        if tag.endswith(":classify"):
            return classify
        return reads.pop(0) if len(reads) > 1 else reads[0]

    return respond


def slide_and_images(page_id: str, index: int):
    page, md, blobs = load_page(page_id)
    return parse_page(page, md)[index], blobs


def test_figure_slide_is_classified_then_read_with_guides() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    note = read_slide(model, RES, slide, images, ctx())
    assert note.status == "ok"
    assert [c.tag for c in model.calls] == ["common_c1#4:classify", "common_c1#4:read"]
    system = model.calls[1].messages[0]
    guide_text = "\n".join(p.text for p in system.parts if isinstance(p, TextPart))
    assert "meef_pitch_<nm 數字>" in guide_text  # the meef_plot guide was applied
    assert "bossung" not in guide_text.lower()  # and only the guides that apply
    assert note.body is not None and note.body.figures[0].reads["meef_pitch_90"] == 2.8


def test_text_and_images_stay_interleaved() -> None:
    slide, images = slide_and_images("common_c1", 3)  # text, image, text
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    read_slide(model, RES, slide, images, ctx())
    user = model.calls[1].messages[-1].parts
    kinds = [type(p).__name__ for p in user]
    first_image = kinds.index("ImagePart")
    assert "MEEF = 晶圓" in "".join(p.text for p in user[:first_image] if isinstance(p, TextPart))
    assert "pitch 越小" in "".join(p.text for p in user[first_image:] if isinstance(p, TextPart))
    assert [p.data for p in user if isinstance(p, ImagePart)] == [images[slide.figures[0]]]


def test_text_only_slide_is_one_call_with_no_figures() -> None:
    slide, images = slide_and_images("cd_d1", 4)  # the settings table
    model = FakeModel(script("unused", note_json([])))
    note = read_slide(model, RES, slide, images, ctx())
    assert note.status == "ok"
    assert [c.tag for c in model.calls] == ["cd_d1#5:read"]


def test_course_context_is_given() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    read_slide(model, RES, slide, images, ctx(prev="前一張的重點"))
    assert "課名" in model.calls[1].text() and "前一張的重點" in model.calls[1].text()


def test_invalid_output_retried_once_then_ok() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(
        script('{"figure_types": ["meef_plot"]}', ["not json", note_json([meef_fig()])])
    )
    assert read_slide(model, RES, slide, images, ctx()).status == "ok"
    assert [c.tag for c in model.calls].count("common_c1#4:read") == 2


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        note_json(
            [meef_fig()], labels=["sp_cd"]
        ),  # the model may not set labels (or anything else)
        note_json([]),  # figure count must match the slide's figures
        note_json([{**meef_fig(), "confidence": 7}]),
    ],
    ids=["garbage", "extra-field", "figure-count", "out-of-range"],
)
def test_invalid_output_twice_is_failed_not_guessed(bad: str) -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', bad))
    note = read_slide(model, RES, slide, images, ctx())
    assert note.status == "failed" and note.body is None
    assert note.labels == Labels.of(["sp_common"])


def test_classification_must_match_figure_count() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot", "sem"]}', note_json([meef_fig()])))
    assert read_slide(model, RES, slide, images, ctx()).status == "failed"


def test_labels_come_from_the_slide_not_the_model() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    note = read_slide(model, RES, slide, images, ctx())
    assert note.labels == slide.labels
    assert note.slide_ref == "common_c1#4" and note.page_revision == slide.page_revision
