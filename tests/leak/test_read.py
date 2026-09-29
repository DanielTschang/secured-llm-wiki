"""Step 2 must only ever show the model one space's content (plus the public glossary)."""

import json
from pathlib import Path

from testset import load_page

from ingest_worker.steps.parse import parse_page
from ingest_worker.steps.read import CourseContext, Resources, read_slide
from kc_models import FakeModel
from tests.leak.harness import Canary

SCHEMA = Path(__file__).parents[2] / "schema"
CD_SLIDE_4 = "量測 13 個 mask CD 偏移條件後擬合"


def test_opc_slides_never_show_cd_content_to_the_model(canaries: list[Canary]) -> None:
    page, md, blobs = load_page("opc_o2")
    _, _, cd_blobs = load_page("cd_d1")
    res = Resources.load(SCHEMA)
    note = json.dumps({"point": "p", "figures": [], "claims": [], "concepts": []})
    model = FakeModel(
        lambda _m, tag: '{"figure_types": ["other"]}' if tag.endswith("classify") else note
    )
    for slide in parse_page(page, md):
        read_slide(model, res, slide, blobs, CourseContext("c", None, ()))
    assert model.calls, "positive control: the model was called"
    for call in model.calls:
        text = call.text()
        assert CD_SLIDE_4 not in text and "cd_d1" not in text
        for c in canaries:
            if c.source_space == "sp_cd":
                assert c.text not in text
        for image in call.images():
            assert image not in cd_blobs.values()
            assert image in blobs.values()
