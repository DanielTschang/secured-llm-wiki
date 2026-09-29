"""Ingest pipeline for one page of one space. Reaches data only through SpaceContext
(ADR-006) and the model only through kc_models; never opens connections itself.

Steps 1-2 per page (parse, read slides); steps 3-7 per course (steps/course.py).
"""

from ingest_worker.steps.course import build_course
from ingest_worker.steps.parse import parse_page
from ingest_worker.steps.read import CourseContext, Resources, as_json, read_slide
from kc_graph import StaleGraphWrite
from kc_ids import PageId, Revision
from kc_models import EmbeddingModel, VisionModel
from kc_store.context import SpaceContext
from kc_store.space import StaleWrite

__all__ = ["CACHE_SALT_LABEL", "Quarantined", "Stale", "ingest_page"]

CACHE_SALT_LABEL = b"kc/model-cache-salt/v1"


class Stale(Exception):
    """A newer revision exists or this revision was already ingested; nothing to do."""


class Quarantined(Exception):
    """The page is quarantined (page-level restriction, ADR-001); never ingest it."""


def ingest_page(
    ctx: SpaceContext,
    model: VisionModel,
    embedder: EmbeddingModel,
    res: Resources,
    page_id: PageId,
    revision: Revision,
    *,
    model_id: str,
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

    # Per-space secret prefix for every prompt: no cache sharing across spaces (ADR-013).
    cache_salt = ctx.store.keyed_digest(CACHE_SALT_LABEL)[:32]
    previous: str | None = None
    terms: list[str] = []
    failed = 0
    try:
        for slide in slides:
            note = read_slide(
                model,
                res,
                slide,
                images,
                CourseContext(page.title, previous, tuple(terms)),
                cache_salt=cache_salt,
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
        # Steps 3-7 for the course this page belongs to (same space only).
        wiki_ok = build_course(
            ctx, model, embedder, res, page, cache_salt=cache_salt, model_id=model_id
        )
        status = "read_partial" if failed else ("wiki_done" if wiki_ok else "wiki_partial")
        ctx.store.record_ingest_run(page_id, revision, status)
    except (StaleWrite, StaleGraphWrite) as e:
        raise Stale from e
