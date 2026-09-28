"""Sync must not pull other spaces' content into a space's storage, and must emit only IDs.

M1 criterion: includes are not expanded.
"""

import json
import logging
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from kc_events import PageEvent
from kc_ids import PageId, Revision
from kc_labels import SpaceId
from kc_obs import configure_logging
from kc_platform.pages import PagesClient
from kc_store.space import SpaceStore
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs
from sync.core import sync_space
from tests.leak.harness import Canary, assert_no_content, load_manifest

# Unique text of cd_d1 slide 4, which opc_o2 includes via {{include page="cd_d1" section="4"}}.
CD_SLIDE_4 = "量測 13 個 mask CD 偏移條件後擬合"


def all_plaintext(store: SpaceStore, blobs: MemoryBlobs) -> str:
    """Decrypt everything a space's store holds (markdown, attachments, titles)."""
    out: list[str] = []
    for key in blobs.dump():
        page_id, rev = PageId(key.split("/")[1]), Revision(int(key.split("/")[2]))
        page = store.get_source_page(page_id)
        assert page is not None
        out.append(page.title)
        if key.endswith("page.md"):
            out.append(store.read_markdown(page_id, rev))
        else:
            att = key.rsplit("/", 1)[1]
            out.append(repr(store.read_attachment(page_id, rev, att)))  # type: ignore[arg-type]
    return "\n".join(out)


@pytest.fixture
def synced(platform: TestClient, token_for: Callable[[str], str]):
    pages = PagesClient(platform, service_token=token_for("svc_sync"))
    keys = FakeKeyService()
    events: list[PageEvent] = []
    stores: dict[str, tuple[SpaceStore, MemoryBlobs]] = {}
    for space in ("sp_common", "sp_opc", "sp_cd"):
        blobs = MemoryBlobs()
        store = SpaceStore(SpaceId(space), keys=keys, docs=MemoryDocs(), blobs=blobs)
        sync_space(space, pages, store, events.append)
        stores[space] = (store, blobs)
    return stores, events


def test_include_not_expanded(
    platform: TestClient, token_for: Callable[[str], str], synced, canaries: list[Canary]
) -> None:
    stores, _ = synced
    # Positive control: the rendered form really does pull cd_d1 into opc_o2.
    h = {"Authorization": f"Bearer {token_for('svc_sync')}"}
    rendered = platform.get("/api/pages/opc_o2?format=rendered", headers=h).json()["markdown"]
    assert CD_SLIDE_4 in rendered

    store, blobs = stores["sp_opc"]
    opc_text = all_plaintext(store, blobs)
    assert '{{include page="cd_d1" section="4"}}' in opc_text
    assert CD_SLIDE_4 not in opc_text
    for c in canaries:
        if c.source_space != "sp_opc":
            assert c.text not in opc_text, f"{c.source_space} canary stored in sp_opc"


def test_each_space_holds_only_its_own_pages(synced) -> None:
    stores, _ = synced
    expected = {
        "sp_common": {"common_c1", "common_c2"},
        "sp_opc": {"opc_o1", "opc_o2"},
        "sp_cd": {"cd_d1"},
    }
    for space, (_, blobs) in stores.items():
        assert {k.split("/")[1] for k in blobs.dump()} == expected[space]


def test_events_carry_only_ids(synced) -> None:
    _, events = synced
    assert len(events) == 5
    for e in events:
        payload = json.loads(e.to_bytes())
        assert set(payload) == {"space_id", "page_id", "revision"}
        assert_no_content(e.to_bytes().decode())


def test_sync_logs_contain_no_content(
    platform: TestClient, token_for: Callable[[str], str], captured_logs: pytest.LogCaptureFixture
) -> None:
    configure_logging()  # what sync's entrypoint does first
    captured_logs.set_level(logging.DEBUG)
    pages = PagesClient(platform, service_token=token_for("svc_sync"))
    keys = FakeKeyService()
    for space in ("sp_common", "sp_opc", "sp_cd"):
        store = SpaceStore(SpaceId(space), keys=keys, docs=MemoryDocs(), blobs=MemoryBlobs())
        sync_space(space, pages, store, lambda _e: None)
    assert any(r.name == "kc.sync" for r in captured_logs.records)
    assert_no_content(captured_logs.text)
    for p in load_manifest()["pages"]:
        for a in p["attachments"]:
            assert a.rsplit("/", 1)[1] not in captured_logs.text, "attachment filename logged"
