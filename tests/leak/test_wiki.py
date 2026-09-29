"""Ingest steps 3-7 keep one space's wiki to that space and keep content out of logs."""

import importlib.util
import logging
from pathlib import Path

import pytest
from testset import MANIFEST, load_page

from ingest_worker.pipeline import ingest_page
from ingest_worker.steps.parse import parse_page
from ingest_worker.steps.read import Resources
from kc_graph import SpaceGraph
from kc_graph.testing import MemoryGraph
from kc_ids import PageId, Revision
from kc_obs import configure_logging
from kc_okf import is_bundle_path
from kc_store.context import SpaceContext
from kc_store.index import MemoryIndex
from kc_store.space import SpaceStore
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs
from tests.leak.harness import Canary, assert_no_content

REPO = Path(__file__).parents[2]
CD_SLIDE_4 = "量測 13 個 mask CD 偏移條件後擬合"


def _fake_model():
    spec = importlib.util.spec_from_file_location(
        "fake_pipeline", REPO / "services/ingest-worker/tests/fake_pipeline.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    counts: dict[str, int] = {}
    for meta in MANIFEST["pages"]:
        page, md, _ = load_page(meta["page_id"])
        counts |= {s.slide_ref: len(s.figures) for s in parse_page(page, md)}
    return module.fake_pipeline_model(counts)


def test_opc_wiki_holds_only_opc_content_and_logs_hold_none(
    canaries: list[Canary], captured_logs: pytest.LogCaptureFixture
) -> None:
    configure_logging()
    captured_logs.set_level(logging.DEBUG)
    page, _, _ = load_page("opc_o1")
    store = SpaceStore(page.space_id, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs())
    for pid in ("opc_o1", "opc_o2"):
        p, md, att = load_page(pid)
        store.put_source_page(p, md, att)
    ctx = SpaceContext(
        page.space_id, store, SpaceGraph(page.space_id, MemoryGraph()), MemoryIndex(page.space_id)
    )
    model = _fake_model()
    for pid in ("opc_o1", "opc_o2"):
        ingest_page(
            ctx, model, model, Resources.load(REPO / "schema"), PageId(pid), Revision(1),
            model_id="fake",
        )  # fmt: skip

    pages = store.wiki_pages()
    assert pages  # positive control
    for p in pages:
        assert is_bundle_path(p["path"])
        text = store.wiki_get(p["path"])[0]  # type: ignore[index]
        assert CD_SLIDE_4 not in text and "cd_d1" not in text
        for c in canaries:
            if c.source_space != "sp_opc" and c.context is None:
                assert c.text not in text, f"{c.source_space} canary in sp_opc wiki"
    assert_no_content(captured_logs.text)
