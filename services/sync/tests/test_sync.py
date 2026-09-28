import base64

from kc_ids import PageId, Revision
from sync.core import sync_space

from .conftest import Harness

O1 = PageId("opc_o1")


def run(h: Harness, space: str = "sp_opc"):
    return sync_space(space, h.pages, h.store(space), h.publish)


def test_first_sync_stores_and_publishes(h: Harness) -> None:
    report = run(h)
    assert report.new == 2
    assert sorted((e.page_id, e.revision) for e in h.events) == [("opc_o1", 1), ("opc_o2", 1)]
    page = h.store("sp_opc").get_source_page(O1)
    assert page is not None and page.revision == 1 and len(page.attachment_ids) == 3
    assert {n for n, _ in page.attachment_map} == {
        "o1_residual.png",
        "o1_rules_2023.png",
        "opc_schematic.png",
    }


def test_unchanged_updated_date_is_not_fetched(h: Harness) -> None:
    run(h)
    h.pages.fetched.clear()
    h.events.clear()
    report = run(h)
    assert report.unchanged == 2
    assert h.pages.fetched == []
    assert h.events == []


def test_updated_date_bump_without_content_change(h: Harness) -> None:
    run(h)
    h.events.clear()
    h.edit(page_id="opc_o1", updated_date="2026-01-01T00:00:00Z")
    report = run(h)
    assert report.touched == 1
    assert h.events == []
    page = h.store("sp_opc").get_source_page(O1)
    assert page is not None and page.revision == 1 and page.updated_date.year == 2026


def test_markdown_change_bumps_revision(h: Harness) -> None:
    run(h)
    h.events.clear()
    h.edit(page_id="opc_o1", markdown="# new", updated_date="2026-01-01T00:00:00Z")
    report = run(h)
    assert report.changed == 1
    assert [(e.page_id, e.revision) for e in h.events] == [("opc_o1", 2)]
    assert h.store("sp_opc").read_markdown(O1, Revision(2)) == "# new"
    assert h.store("sp_opc").read_markdown(O1, Revision(1)).startswith("# OPC")


def test_attachment_only_change_bumps_revision(h: Harness) -> None:
    run(h)
    h.events.clear()
    h.edit(
        page_id="opc_o1",
        updated_date="2026-01-01T00:00:00Z",
        attachment={
            "name": "o1_residual.png",
            "content_b64": base64.b64encode(b"new png").decode(),
        },
    )
    run(h)
    assert [(e.page_id, e.revision) for e in h.events] == [("opc_o1", 2)]


def test_restricted_page_quarantined_not_fetched(h: Harness) -> None:
    h.platform.post("/dev/restrict", json={"page_id": "opc_o1", "restricted": True})
    report = run(h)
    assert report.quarantined == 1
    assert "opc_o1" not in h.pages.fetched
    assert [e.page_id for e in h.events] == ["opc_o2"]
    s = h.store("sp_opc")
    assert s.is_quarantined(O1) and s.get_source_page(O1) is None


def test_lost_event_is_republished(h: Harness) -> None:
    h.fail_publish = True
    report = run(h)
    assert report.publish_failed == 2 and h.events == []
    h.fail_publish = False
    report = run(h)
    assert report.republished == 2
    assert sorted(e.page_id for e in h.events) == ["opc_o1", "opc_o2"]
    h.events.clear()
    run(h)
    assert h.events == []


def test_spaces_are_isolated(h: Harness) -> None:
    run(h, "sp_opc")
    run(h, "sp_cd")
    assert {e.space_id for e in h.events} == {"sp_opc", "sp_cd"}
    assert h.store("sp_cd").get_source_page(O1) is None
    for e in h.events:
        assert e.subject == f"kc.page.{e.space_id}"
