"""Ingest pipeline for one page of one space. Reaches data only through SpaceContext
(ADR-006) and the model only through kc_models; never opens connections itself.

M2: step 1 (parse slides) and step 2 (read slides). Steps 3-7 follow in M3.
"""

from ingest_worker.steps.parse import course_id, parse_page
from ingest_worker.steps.read import CourseContext, Resources, as_json, read_slide
from kc_graph import StaleGraphWrite
from kc_ids import PageId, Revision
from kc_models import VisionModel
from kc_store.context import SpaceContext
from kc_store.space import StaleWrite

__all__ = ["Quarantined", "Stale", "ingest_page"]


class Stale(Exception):
    """A newer revision exists or this revision was already ingested; nothing to do."""


class Quarantined(Exception):
    """The page is quarantined (page-level restriction, ADR-001); never ingest it."""


def ingest_page(
    ctx: SpaceContext, model: VisionModel, res: Resources, page_id: PageId, revision: Revision
) -> None:
    # Checked before reading any content, even for pages stored before the restriction.
    if ctx.store.is_quarantined(page_id):
        raise Quarantined
    page = ctx.store.get_source_page(page_id)
    if page is None or page.revision < revision:
        raise LookupError("source page revision not stored yet")
    if page.revision > revision:
        raise Stale
    run = ctx.store.ingest_run(page_id)
    if run is not None and run[0] >= revision:
        raise Stale

    markdown = ctx.store.read_markdown(page_id, revision)
    slides = parse_page(page, markdown)
    # Every model input is this space's content (plus the public guides and glossary).
    ctx.assert_single_space([page.labels, *(s.labels for s in slides)])
    images = {att: ctx.store.read_attachment(page_id, revision, att) for att in page.attachment_ids}

    previous: str | None = None
    terms: list[str] = []
    failed = 0
    try:
        for slide in slides:
            note = read_slide(
                model, res, slide, images, CourseContext(page.title, previous, tuple(terms))
            )
            ctx.store.put_slide_note(
                note.slide_ref,
                page_id,
                revision,
                note.labels,
                note.status,
                as_json(note.body) if note.body else None,
            )
            if note.body is None:
                failed += 1
                continue
            previous = note.body.point
            terms.extend(c for c in note.body.concepts if c not in terms)
        ctx.graph.upsert_source_page(page_id, revision, page.labels)
        ctx.store.record_ingest_run(page_id, revision, "read_partial" if failed else "read_done")
    except (StaleWrite, StaleGraphWrite) as e:
        raise Stale from e
    _ = course_id(page)  # course grouping is used from step 3 (M3)
