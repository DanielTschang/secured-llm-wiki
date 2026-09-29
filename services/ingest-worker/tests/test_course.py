from pathlib import Path

import pytest
from testset import MANIFEST, load_page

from ingest_worker.pipeline import ingest_page
from ingest_worker.steps.parse import parse_page
from ingest_worker.steps.read import Resources
from kc_graph import SpaceGraph
from kc_graph.testing import MemoryGraph
from kc_ids import PageId, Revision
from kc_okf import is_bundle_path, parse
from kc_store.context import SpaceContext
from kc_store.index import MemoryIndex
from kc_store.space import SpaceStore
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

from .fake_pipeline import fake_pipeline_model

RES = Resources.load(Path(__file__).parents[3] / "schema")


def figure_counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for meta in MANIFEST["pages"]:
        page, md, _ = load_page(meta["page_id"])
        out |= {s.slide_ref: len(s.figures) for s in parse_page(page, md)}
    return out


def context(*page_ids: str, docs: MemoryDocs | None = None, blobs: MemoryBlobs | None = None):
    page, _, _ = load_page(page_ids[0])
    space = page.space_id
    store = SpaceStore(
        space, keys=FakeKeyService(), docs=docs or MemoryDocs(), blobs=blobs or MemoryBlobs()
    )
    for pid in page_ids:
        p, md, att = load_page(pid)
        store.put_source_page(p, md, att)
    graph = MemoryGraph()
    return SpaceContext(space, store, SpaceGraph(space, graph), MemoryIndex(space)), graph


@pytest.fixture
def opc():
    return context("opc_o1", "opc_o2")


def run(ctx: SpaceContext, model, *page_ids: str) -> None:
    for pid in page_ids:
        ingest_page(ctx, model, model, RES, PageId(pid), Revision(1), model_id="fake")


def test_course_produces_all_page_kinds_grounded_and_indexed(opc) -> None:
    ctx, graph = opc
    model = fake_pipeline_model(figure_counts())
    run(ctx, model, "opc_o1", "opc_o2")
    kinds = {p["kind"] for p in ctx.store.wiki_pages()}
    assert kinds == {"concept", "entity", "course", "synthesis"}  # 2 versions -> synthesis
    for p in ctx.store.wiki_pages():
        assert is_bundle_path(p["path"])
        doc = parse(ctx.store.wiki_get(p["path"])[0])  # type: ignore[index]
        assert doc.frontmatter["status"] == "stable"
        assert doc.frontmatter["kc_labels"] == ["sp_opc"]
    assert ctx.store.wiki_get("index.md") is not None and ctx.store.wiki_get("log.md") is not None
    assert ctx.store.ingest_run(PageId("opc_o2")) == (1, "wiki_done")
    # concepts aligned: MEEF -> canonical, the unknown term -> a local concept
    meef = ctx.store.wiki_page_by_key("concept:meef")
    assert meef is not None and meef["concept_id"] == "concept:meef"
    # index and graph built for this space
    assert ctx.index is not None and ctx.index.search_vector([0.0] * 16, 3)  # type: ignore[union-attr]
    assert graph.edges and graph.page_props


def test_claims_are_labelled_and_have_host_provenance(opc) -> None:
    ctx, _ = opc
    run(ctx, fake_pipeline_model(figure_counts()), "opc_o1", "opc_o2")
    claims = ctx.store.claims_for_concept("concept:meef")
    assert claims and all(c["labels"] == ["sp_opc"] for c in claims)
    for c in claims:
        for prov in c["provenance"]:
            assert prov["page_id"] in {"opc_o1", "opc_o2"}
            assert prov["revision"] == 1 and prov["slide_no"] >= 1


def test_other_space_content_never_reaches_model_or_wiki() -> None:
    cd, _ = context("cd_d1")
    opc, _ = context("opc_o1", "opc_o2")
    run(cd, fake_pipeline_model(figure_counts()), "cd_d1")
    model = fake_pipeline_model(figure_counts())
    run(opc, model, "opc_o1", "opc_o2")
    assert model.calls  # positive control
    for call in model.calls:
        assert "R-CT-114" not in call.text() and "cd_d1" not in call.text()
    for p in opc.store.wiki_pages():
        text = opc.store.wiki_get(p["path"])[0]  # type: ignore[index]
        assert "R-CT-114" not in text and "cd_d1" not in text and "sp_cd" not in text
    assert all("cd_d1" not in t for t in model.embedded)
