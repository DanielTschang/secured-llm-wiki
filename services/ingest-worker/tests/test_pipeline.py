from datetime import UTC, datetime

import pytest

from ingest_worker.pipeline import Stale, stub_ingest
from kc_graph import SpaceGraph
from kc_graph.testing import MemoryGraph
from kc_ids import PageId, Revision, attachment_id_for
from kc_labels import Labels, SpaceId
from kc_store.context import SpaceContext
from kc_store.space import SourcePage, SpaceStore, WrongSpace
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

OPC = SpaceId("sp_opc")
PID = PageId("opc_o1")
PNG = b"\x89PNG"


@pytest.fixture
def ctx() -> SpaceContext:
    store = SpaceStore(OPC, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs())
    graph = SpaceGraph(OPC, MemoryGraph())
    att = attachment_id_for(PNG)
    store.put_source_page(
        SourcePage(
            page_id=PID,
            space_id=OPC,
            revision=Revision(2),
            updated_date=datetime(2025, 1, 1, tzinfo=UTC),
            content_hash="0" * 64,
            title="t",
            parent_id=None,
            attachment_ids=(att,),
            labels=Labels.of([OPC]),
            attachment_map=(("x.png", att),),
        ),
        "# md",
        {att: PNG},
    )
    return SpaceContext(OPC, store, graph)


def test_stub_ingest_records_run_and_graph(ctx: SpaceContext) -> None:
    stub_ingest(ctx, PID, Revision(2))
    assert ctx.store.ingest_run(PID) == (2, "stub_done")
    assert ctx.graph.source_page_revision(PID) == 2


def test_event_older_than_stored_revision_is_stale(ctx: SpaceContext) -> None:
    with pytest.raises(Stale):
        stub_ingest(ctx, PID, Revision(1))


def test_replayed_event_is_stale(ctx: SpaceContext) -> None:
    stub_ingest(ctx, PID, Revision(2))
    with pytest.raises(Stale):
        stub_ingest(ctx, PID, Revision(2))


def test_event_ahead_of_store_fails(ctx: SpaceContext) -> None:
    with pytest.raises(LookupError):
        stub_ingest(ctx, PID, Revision(3))


def test_missing_page_fails(ctx: SpaceContext) -> None:
    with pytest.raises(LookupError):
        stub_ingest(ctx, PageId("nope"), Revision(1))


def test_foreign_labels_abort(ctx: SpaceContext) -> None:
    with pytest.raises(WrongSpace):
        ctx.assert_single_space([Labels.of(["sp_opc", "sp_cd"])])
