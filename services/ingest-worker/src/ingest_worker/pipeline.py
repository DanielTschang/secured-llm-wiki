"""Ingest steps. Reach data only through SpaceContext (ADR-006); never open connections.

M1 has a stub step that proves the task can read its own space and write fenced results.
The real steps (ADR-005, architecture §7) replace it from M2.
"""

from kc_graph import StaleGraphWrite
from kc_ids import PageId, Revision
from kc_store.context import SpaceContext
from kc_store.space import StaleWrite

__all__ = ["Quarantined", "Stale", "stub_ingest"]


class Stale(Exception):
    """A newer revision exists or this revision was already ingested; nothing to do."""


class Quarantined(Exception):
    """The page is quarantined (page-level restriction, ADR-001); never ingest it."""


def stub_ingest(ctx: SpaceContext, page_id: PageId, revision: Revision) -> None:
    # Checked before reading any content, even for pages stored before the restriction.
    if ctx.store.is_quarantined(page_id):
        raise Quarantined
    page = ctx.store.get_source_page(page_id)
    if page is None or page.revision < revision:
        raise LookupError("source page revision not stored yet")
    if page.revision > revision:
        raise Stale
    ctx.assert_single_space([page.labels])

    ctx.store.read_markdown(page_id, revision)
    for att in page.attachment_ids:
        ctx.store.read_attachment(page_id, revision, att)

    try:
        ctx.graph.upsert_source_page(page_id, revision, page.labels)
        ctx.store.record_ingest_run(page_id, revision, "stub_done")
    except (StaleWrite, StaleGraphWrite) as e:
        raise Stale from e
