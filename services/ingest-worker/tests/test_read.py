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

SALT = "5a" * 16


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
    note = read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    assert note.status == "ok"
    assert [c.tag for c in model.calls] == ["common_c1#4:classify", "common_c1#4:read"]
    system = model.calls[1].messages[0]
    guide_text = "\n".join(p.text for p in system.parts if isinstance(p, TextPart))
    assert "meef_pitch_<nm 數字>" in guide_text  # the meef_plot guide was applied
    assert "_best_focus_um" not in guide_text  # the bossung_curve guide was not included
    assert note.body is not None and note.body.figures[0].reads["meef_pitch_90"] == 2.8


def test_text_and_images_stay_interleaved() -> None:
    slide, images = slide_and_images("common_c1", 3)  # text, image, text
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    user = model.calls[1].messages[-1].parts
    kinds = [type(p).__name__ for p in user]
    first_image = kinds.index("ImagePart")
    assert "MEEF = 晶圓" in "".join(p.text for p in user[:first_image] if isinstance(p, TextPart))
    assert "pitch 越小" in "".join(p.text for p in user[first_image:] if isinstance(p, TextPart))
    assert [p.data for p in user if isinstance(p, ImagePart)] == [images[slide.figures[0]]]


def test_text_only_slide_is_one_call_with_no_figures() -> None:
    slide, images = slide_and_images("cd_d1", 4)  # the settings table
    model = FakeModel(script("unused", note_json([])))
    note = read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    assert note.status == "ok"
    assert [c.tag for c in model.calls] == ["cd_d1#5:read"]


def test_course_context_is_given() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    read_slide(model, RES, slide, images, ctx(prev="前一張的重點"), cache_salt=SALT)
    assert "課名" in model.calls[1].text() and "前一張的重點" in model.calls[1].text()


def test_invalid_output_retried_once_then_ok() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(
        script('{"figure_types": ["meef_plot"]}', ["not json", note_json([meef_fig()])])
    )
    assert read_slide(model, RES, slide, images, ctx(), cache_salt=SALT).status == "ok"
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
    note = read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    assert note.status == "failed" and note.body is None
    assert note.labels == Labels.of(["sp_common"])


def test_classification_must_match_figure_count() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot", "sem"]}', note_json([meef_fig()])))
    assert read_slide(model, RES, slide, images, ctx(), cache_salt=SALT).status == "failed"


def test_labels_come_from_the_slide_not_the_model() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    note = read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    assert note.labels == slide.labels
    assert note.slide_ref == "common_c1#4" and note.page_revision == slide.page_revision


def test_reading_schema_is_constrained_per_figure_type() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    schema = model.calls[1].json_schema
    assert schema is not None
    figures = schema["properties"]["figures"]
    assert figures["minItems"] == figures["maxItems"] == 1
    fig = figures["prefixItems"][0]
    assert fig["properties"]["type"] == {"const": "meef_plot"}
    assert fig["properties"]["reads"]["additionalProperties"] == {"type": "number"}
    assert fig["additionalProperties"] is False


def test_categorical_reads_are_closed_sets() -> None:
    from ingest_worker.steps.read import READ_SCHEMAS

    assert "magnification" in READ_SCHEMAS["overlay_vector_map"]["properties"]["pattern"]["enum"]
    assert READ_SCHEMAS["sem"]["additionalProperties"]["enum"][0] == "normal"
    assert "serif" in READ_SCHEMAS["schematic"]["properties"]["features"]["items"]["enum"]


def test_text_only_schema_allows_no_figures() -> None:
    slide, images = slide_and_images("cd_d1", 4)
    model = FakeModel(script("unused", note_json([])))
    read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    schema = model.calls[0].json_schema
    assert schema is not None and schema["properties"]["figures"]["maxItems"] == 0


def test_classification_prompt_describes_each_type() -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    prompt = model.calls[0].text()
    assert "screenshot" in prompt and "規則表" in prompt  # screenshots of rule tables
    assert "sem" in prompt and "電子顯微鏡" in prompt


def test_schema_has_no_boolean_subschemas() -> None:
    """Ollama's grammar converter rejects boolean schemas such as "items": false."""
    from ingest_worker.steps.read import FIGURE_TYPES, note_schema

    def walk(node: object, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if (
                    k in {"items", "additionalProperties"}
                    and isinstance(v, bool)
                    and v is not False
                ):
                    raise AssertionError(k)
                if k == "items":
                    assert isinstance(v, dict), f"{key}.items must be an object"
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)

    walk(note_schema(list(FIGURE_TYPES)))
    walk(note_schema([]))


def test_host_normalises_transcribed_figures_and_stray_keys() -> None:
    """Screenshots, tables, SEM and schematics are transcribed or described, never estimated,
    so numbers_from_figure is false by definition; field names leaking into reads are dropped."""
    slide, images = slide_and_images("opc_o2", 1)  # the rule-table screenshot
    fig = {
        "type": "screenshot",
        "reads": {"meef_threshold": 2.5, "numbers_from_figure": 1, "confidence": 1},
        "numbers_from_figure": True,
        "confidence": 0.9,
    }
    model = FakeModel(script('{"figure_types": ["screenshot"]}', note_json([fig])))
    note = read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    assert note.body is not None
    got = note.body.figures[0]
    assert got.numbers_from_figure is False
    assert got.reads == {"meef_threshold": 2.5}


def test_every_model_call_starts_with_the_space_cache_salt() -> None:
    """Prompts of different spaces diverge at their first tokens, so a shared model server
    can never serve one space's cached prefix to another."""
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    read_slide(model, RES, slide, images, ctx(), cache_salt="a1" * 16)
    for call in model.calls:
        first = call.messages[0].parts[0]
        assert isinstance(first, TextPart) and first.text == "[" + "a1" * 16 + "]"


@pytest.mark.parametrize("salt", ["", "short", "g" * 32, "A" * 32])
def test_cache_salt_must_be_32_lowercase_hex(salt: str) -> None:
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    with pytest.raises(ValueError, match="cache salt"):
        read_slide(model, RES, slide, images, ctx(), cache_salt=salt)
    assert model.calls == []


def test_reading_prompt_does_not_list_the_glossary() -> None:
    """A 7B model echoed the whole glossary into `concepts`; alignment is step 4's job."""
    slide, images = slide_and_images("common_c1", 3)
    model = FakeModel(script('{"figure_types": ["meef_plot"]}', note_json([meef_fig()])))
    read_slide(model, RES, slide, images, ctx(), cache_salt=SALT)
    text = model.calls[1].text()
    assert "concept:bossung_curve" not in text and "標準術語表" not in text
    assert "投影片中實際出現" in text
