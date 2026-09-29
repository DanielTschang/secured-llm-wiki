from pathlib import Path

import pytest
from testset import load_page

from ingest_worker.pipeline import Quarantined, Stale, ingest_page
from ingest_worker.steps.read import Resources
from kc_graph import SpaceGraph
from kc_graph.testing import MemoryGraph
from kc_ids import PageId, Revision
from kc_labels import Labels, SpaceId
from kc_models import FakeModel
from kc_store.context import SpaceContext
from kc_store.index import MemoryIndex
from kc_store.space import SpaceStore, WrongSpace
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

from .fake_pipeline import fake_pipeline_model
from .test_course import figure_counts

RES = Resources.load(Path(__file__).parents[3] / "schema")
PID = PageId("common_c1")


def model() -> FakeModel:
    return fake_pipeline_model(figure_counts())


@pytest.fixture
def ctx() -> SpaceContext:
    page, md, blobs = load_page("common_c1")
    store = SpaceStore(page.space_id, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs())
    store.put_source_page(page, md, blobs)
    return SpaceContext(
        page.space_id, store, SpaceGraph(page.space_id, MemoryGraph()), MemoryIndex(page.space_id)
    )


def test_ingest_page_stores_a_note_per_slide(ctx: SpaceContext) -> None:
    fake = model()
    ingest_page(ctx, fake, fake, RES, PID, Revision(1), model_id="fake")
    notes = ctx.store.slide_notes(PID, Revision(1))
    assert [n["slide_ref"] for n in notes] == [f"common_c1#{i}" for i in range(1, 6)]
    assert all(n["status"] == "ok" and n["labels"] == ["sp_common"] for n in notes)
    assert ctx.store.ingest_run(PID) == (1, "wiki_done")
    assert ctx.graph.source_page_revision(PID) == 1


def test_previous_point_is_carried_forward(ctx: SpaceContext) -> None:
    fake = model()
    ingest_page(ctx, fake, fake, RES, PID, Revision(1), model_id="fake")
    read_calls = [c for c in fake.calls if c.tag.endswith(":read")]
    assert "重點 common_c1#1" in read_calls[1].text()


def test_partial_failures_are_recorded(ctx: SpaceContext) -> None:
    fake = FakeModel(lambda _m, tag: "garbage")
    ingest_page(ctx, fake, fake, RES, PID, Revision(1), model_id="fake")
    assert {n["status"] for n in ctx.store.slide_notes(PID, Revision(1))} == {"failed"}
    assert ctx.store.ingest_run(PID) == (1, "read_partial")


def test_replayed_event_is_stale(ctx: SpaceContext) -> None:
    ingest_page(ctx, model(), model(), RES, PID, Revision(1), model_id="fake")
    with pytest.raises(Stale):
        ingest_page(ctx, model(), model(), RES, PID, Revision(1), model_id="fake")


def test_event_ahead_of_store_fails(ctx: SpaceContext) -> None:
    with pytest.raises(LookupError):
        ingest_page(ctx, model(), model(), RES, PID, Revision(2), model_id="fake")


def test_quarantined_page_is_not_ingested(ctx: SpaceContext) -> None:
    fake = model()
    ctx.store.quarantine(PID)
    with pytest.raises(Quarantined):
        ingest_page(ctx, fake, fake, RES, PID, Revision(1), model_id="fake")
    assert fake.calls == []  # nothing was read, nothing reached the model
    assert ctx.store.slide_notes(PID, Revision(1)) == []


def test_foreign_labels_abort(ctx: SpaceContext) -> None:
    with pytest.raises(WrongSpace):
        ctx.assert_single_space([Labels.of(["sp_common", "sp_cd"])])


def test_cache_salt_is_keyed_per_space(ctx: SpaceContext) -> None:
    fake = model()
    ingest_page(ctx, fake, fake, RES, PID, Revision(1), model_id="fake")
    salts = {c.messages[0].parts[0].text for c in fake.calls}  # type: ignore[union-attr]
    assert len(salts) == 1
    salt = salts.pop()
    assert salt == "[" + ctx.store.keyed_digest(b"kc/model-cache-salt/v1")[:32] + "]"
    other = SpaceStore(
        SpaceId("sp_cd"), keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs()
    )
    assert other.keyed_digest(b"kc/model-cache-salt/v1")[:32] not in salt


def test_two_spaces_in_one_store_abort_and_never_mix() -> None:
    """Defence in depth: if another space's documents ever appeared in this space's store
    (in deployment they cannot: separate databases), ingest aborts on the label check
    instead of mixing them, and nothing from the other space reached the model before."""
    docs, blobs, keys = MemoryDocs(), MemoryBlobs(), FakeKeyService()
    cd_page, cd_md, cd_blobs = load_page("cd_d1")
    SpaceStore(cd_page.space_id, keys=keys, docs=docs, blobs=blobs).put_source_page(
        cd_page, cd_md, cd_blobs
    )
    opc_page, opc_md, opc_blobs = load_page("opc_o2")
    opc_store = SpaceStore(opc_page.space_id, keys=keys, docs=docs, blobs=blobs)
    opc_store.put_source_page(opc_page, opc_md, opc_blobs)
    opc = SpaceContext(
        opc_page.space_id,
        opc_store,
        SpaceGraph(opc_page.space_id, MemoryGraph()),
        MemoryIndex(opc_page.space_id),
    )
    fake = model()
    with pytest.raises(WrongSpace):
        ingest_page(opc, fake, fake, RES, PageId("opc_o2"), Revision(1), model_id="fake")
    assert fake.calls  # positive control
    for call in fake.calls:
        assert "R-CT-114" not in call.text() and "量測 13 個" not in call.text()
        for image in call.images():
            assert image in opc_blobs.values() and image not in cd_blobs.values()
