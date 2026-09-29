import json
from pathlib import Path

import pytest
from testset import load_page

from ingest_worker.pipeline import Quarantined, Stale, ingest_page
from ingest_worker.steps.read import Resources
from kc_graph import SpaceGraph
from kc_graph.testing import MemoryGraph
from kc_ids import PageId, Revision
from kc_labels import Labels
from kc_models import FakeModel
from kc_store.context import SpaceContext
from kc_store.space import SpaceStore, WrongSpace
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

RES = Resources.load(Path(__file__).parents[3] / "schema")
PID = PageId("common_c1")


def model() -> FakeModel:
    def respond(messages: object, tag: str) -> str:
        if tag.endswith(":classify"):
            return '{"figure_types": ["other"]}'
        n_figs = 1 if tag.startswith(("common_c1#2", "common_c1#3", "common_c1#4")) else 0
        figs = [
            {"type": "other", "reads": {}, "numbers_from_figure": False, "confidence": 0.5}
        ] * n_figs
        return json.dumps(
            {"point": f"point of {tag}", "figures": figs, "claims": [], "concepts": ["c"]}
        )

    return FakeModel(respond)


@pytest.fixture
def ctx() -> SpaceContext:
    page, md, blobs = load_page("common_c1")
    store = SpaceStore(page.space_id, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs())
    store.put_source_page(page, md, blobs)
    return SpaceContext(page.space_id, store, SpaceGraph(page.space_id, MemoryGraph()))


def test_ingest_page_stores_a_note_per_slide(ctx: SpaceContext) -> None:
    fake = model()
    ingest_page(ctx, fake, RES, PID, Revision(1))
    notes = ctx.store.slide_notes(PID)
    assert [n["slide_ref"] for n in notes] == [f"common_c1#{i}" for i in range(1, 6)]
    assert all(n["status"] == "ok" and n["labels"] == ["sp_common"] for n in notes)
    assert ctx.store.ingest_run(PID) == (1, "read_done")
    assert ctx.graph.source_page_revision(PID) == 1


def test_previous_point_is_carried_forward(ctx: SpaceContext) -> None:
    fake = model()
    ingest_page(ctx, fake, RES, PID, Revision(1))
    read_calls = [c for c in fake.calls if c.tag.endswith(":read")]
    assert "point of common_c1#1:read" in read_calls[1].text()


def test_partial_failures_are_recorded(ctx: SpaceContext) -> None:
    fake = FakeModel(lambda _m, tag: "garbage")
    ingest_page(ctx, fake, RES, PID, Revision(1))
    assert {n["status"] for n in ctx.store.slide_notes(PID)} == {"failed"}
    assert ctx.store.ingest_run(PID) == (1, "read_partial")


def test_replayed_event_is_stale(ctx: SpaceContext) -> None:
    ingest_page(ctx, model(), RES, PID, Revision(1))
    with pytest.raises(Stale):
        ingest_page(ctx, model(), RES, PID, Revision(1))


def test_event_ahead_of_store_fails(ctx: SpaceContext) -> None:
    with pytest.raises(LookupError):
        ingest_page(ctx, model(), RES, PID, Revision(2))


def test_quarantined_page_is_not_ingested(ctx: SpaceContext) -> None:
    fake = model()
    ctx.store.quarantine(PID)
    with pytest.raises(Quarantined):
        ingest_page(ctx, fake, RES, PID, Revision(1))
    assert fake.calls == []  # nothing was read, nothing reached the model
    assert ctx.store.slide_notes(PID) == []


def test_foreign_labels_abort(ctx: SpaceContext) -> None:
    with pytest.raises(WrongSpace):
        ctx.assert_single_space([Labels.of(["sp_common", "sp_cd"])])
