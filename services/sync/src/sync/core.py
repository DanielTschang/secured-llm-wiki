"""Sync one space: detect changed pages, store them encrypted, publish ID-only events.

Sync is the only component that reads every space, so it stays narrow: it fetches the
raw form (never the rendered one, which may have other spaces' includes expanded), stores,
and publishes. It never interprets content.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from kc_events import PageEvent
from kc_ids import AttachmentId, PageId, Revision, attachment_id_for
from kc_labels import Labels, SpaceId
from kc_obs import get_logger
from kc_platform.pages import PageMeta, PagesClient
from kc_store.space import SourcePage, SpaceStore, StaleWrite

__all__ = ["SyncReport", "sync_space"]

log = get_logger("sync")


class Outcome(Enum):
    NEW = "new"
    CHANGED = "changed"
    TOUCHED = "touched"
    UNCHANGED = "unchanged"
    QUARANTINED = "quarantined"
    SPACE_MISMATCH = "space_mismatch"
    STALE = "stale"
    INVALID_ID = "invalid_id"


@dataclass
class SyncReport:
    new: int = 0
    changed: int = 0
    touched: int = 0
    unchanged: int = 0
    quarantined: int = 0
    skipped: int = 0
    republished: int = 0
    publish_failed: int = 0


def content_hash(markdown: str, attachment_map: tuple[tuple[str, AttachmentId], ...]) -> str:
    h = hashlib.sha256(markdown.encode())
    h.update(b"\0")
    h.update(json.dumps(sorted([n, str(a)] for n, a in attachment_map)).encode())
    return h.hexdigest()


def _publish(
    store: SpaceStore,
    publish: Callable[[PageEvent], None],
    page_id: PageId,
    revision: Revision,
    report: SyncReport,
) -> bool:
    try:
        publish(PageEvent(store.space_id, page_id, revision))
    except Exception as e:
        # The stored page stays unpublished; the next run republishes it.
        report.publish_failed += 1
        log.error("publish_failed", space_id=store.space_id, page_id=page_id, error=e)
        return False
    store.mark_published(page_id, revision)
    return True


def _sync_page(
    space: SpaceId, meta: PageMeta, pages: PagesClient, store: SpaceStore
) -> tuple[Outcome, Revision | None]:
    page_id = PageId(meta.page_id)
    if meta.restricted:
        # Page-level restrictions are not modelled (ADR-001): isolate, never fetch content.
        store.quarantine(page_id)
        return Outcome.QUARANTINED, None

    current = store.get_source_page(page_id)
    if current is not None and current.updated_date == meta.updated_date:
        return Outcome.UNCHANGED, None

    raw = pages.get_page(page_id)
    if raw.space_id != space or raw.page_id != page_id:
        # Moved between listing and fetch; moves are handled in M6. Store nothing.
        return Outcome.SPACE_MISMATCH, None

    blobs: dict[AttachmentId, bytes] = {}
    amap: list[tuple[str, AttachmentId]] = []
    for name in raw.attachment_names:
        data = pages.get_attachment(page_id, name)
        att = attachment_id_for(data)
        blobs[att] = data
        amap.append((name, att))
    attachment_map = tuple(amap)
    digest = content_hash(raw.markdown, attachment_map)

    if current is not None and current.content_hash == digest:
        store.touch_updated_date(page_id, current.revision, meta.updated_date)
        return Outcome.TOUCHED, None

    revision = Revision(1 if current is None else int(current.revision) + 1)
    store.put_source_page(
        SourcePage(
            page_id=page_id,
            space_id=space,
            revision=revision,
            updated_date=raw.updated_date,
            content_hash=digest,
            title=raw.title,
            parent_id=raw.parent_id,
            attachment_ids=tuple(sorted(set(blobs))),
            labels=Labels.of([space]),
            attachment_map=attachment_map,
        ),
        raw.markdown,
        blobs,
    )
    return (Outcome.NEW if current is None else Outcome.CHANGED), revision


def sync_space(
    space_id: str,
    pages: PagesClient,
    store: SpaceStore,
    publish: Callable[[PageEvent], None],
) -> SyncReport:
    space = SpaceId(space_id)
    if store.space_id != space:
        raise ValueError("store bound to a different space")
    report = SyncReport()
    for meta in pages.get_pages(space):
        try:
            page_id = PageId(meta.page_id)
        except ValueError:
            report.skipped += 1
            log.warning("page_skipped", space_id=space, reason=Outcome.INVALID_ID)
            continue
        try:
            outcome, revision = _sync_page(space, meta, pages, store)
        except StaleWrite:
            outcome, revision = Outcome.STALE, None

        match outcome:
            case Outcome.NEW:
                report.new += 1
            case Outcome.CHANGED:
                report.changed += 1
            case Outcome.TOUCHED:
                report.touched += 1
            case Outcome.UNCHANGED:
                report.unchanged += 1
            case Outcome.QUARANTINED:
                report.quarantined += 1
            case _:
                report.skipped += 1

        if revision is not None:
            _publish(store, publish, page_id, revision, report)
        elif outcome is not Outcome.QUARANTINED:
            pending = store.unpublished_revision(page_id)
            if pending is not None and _publish(store, publish, page_id, pending, report):
                report.republished += 1

        log.info(
            "page_synced",
            space_id=space,
            page_id=page_id,
            revision=int(revision or 0),
            outcome=outcome,
        )
    return report
