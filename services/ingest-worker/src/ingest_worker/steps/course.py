"""Ingest steps 3-7 for the course a page belongs to (one space only).

integrate the course's slide notes -> align concepts -> store claims -> write the
affected concept, entity, course and synthesis pages -> ground them -> regenerate
index.md/log.md -> rebuild this space's search index and wiki graph.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from ingest_worker.steps.align import align_concepts, figure_evidence, normalise
from ingest_worker.steps.ground import SlideMaterial, ground_page
from ingest_worker.steps.integrate import Claim, CourseSlide, integrate_course
from ingest_worker.steps.parse import Slide, course_id, parse_page
from ingest_worker.steps.read import Resources
from ingest_worker.steps.wiki import PageSpec, write_index_and_log, write_page
from kc_graph import Edge
from kc_graph.metrics import graph_metrics
from kc_ids import AttachmentId, PageId
from kc_models import EmbeddingModel, VisionModel
from kc_okf import KINDS, parse
from kc_store.context import SpaceContext
from kc_store.index import Chunk
from kc_store.space import SourcePage

__all__ = ["build_course"]

_BUNDLE_LINK = re.compile(rf"\]\(/((?:{'|'.join(KINDS.values())})/[0-9A-HJKMNP-TV-Z]{{26}}\.md)\)")
_FOOTNOTE_DEF = re.compile(r"(?m)^\[\^[^\]]+\]:.*$")


class _Slides:
    """This space's parsed slides and attachments, loaded on demand and cached."""

    def __init__(self, ctx: SpaceContext) -> None:
        self._ctx = ctx
        self._pages: dict[str, tuple[SourcePage, dict[int, Slide]]] = {}

    def page(self, page_id: str) -> tuple[SourcePage, dict[int, Slide]] | None:
        if page_id not in self._pages:
            page = self._ctx.store.get_source_page(PageId(page_id))
            if page is None:
                return None
            md = self._ctx.store.read_markdown(page.page_id, page.revision)
            self._pages[page_id] = (page, {s.number: s for s in parse_page(page, md)})
        return self._pages[page_id]

    def slide(self, page_id: str, slide_no: int) -> tuple[SourcePage, Slide] | None:
        got = self.page(page_id)
        if got is None or slide_no not in got[1]:
            return None
        return got[0], got[1][slide_no]

    def figures(self, claims: list[Claim]) -> tuple[tuple[str, str], ...]:
        out: dict[str, str] = {}
        for c in claims:
            for page_id, _, n in c.provenance:
                got = self.slide(page_id, n)
                if got is not None:
                    out |= {str(att): got[1].slide_ref for att in got[1].figures}
        return tuple(out.items())


class _Material(Mapping[str, SlideMaterial]):
    """source id -> the slide's text and images, for grounding."""

    def __init__(self, ctx: SpaceContext, slides: _Slides) -> None:
        self._ctx, self._slides = ctx, slides

    def _locate(self, sid: str) -> tuple[SourcePage, Slide] | None:
        page_id, _, n = sid.rpartition("-s")
        return self._slides.slide(page_id, int(n)) if n.isdigit() else None

    def __getitem__(self, sid: str) -> SlideMaterial:
        got = self._locate(sid)
        if got is None:
            raise KeyError(sid)
        page, slide = got
        images = tuple(
            self._ctx.store.read_attachment(page.page_id, page.revision, AttachmentId(a))
            for a in slide.figures
        )
        return SlideMaterial(slide.text, images)

    def __contains__(self, sid: object) -> bool:
        return isinstance(sid, str) and self._locate(sid) is not None

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


def split_versions(claims: list[Claim], versions: dict[str, datetime]) -> list[Claim]:
    """One claim per course version it cites, and old versions marked superseded.

    Which statement is current is decided by the host from the platform's version dates,
    not by the model: a claim cited only by pages older than the course's newest page is
    superseded. A claim the model merged across versions is split so each version's copy
    is judged (and grounded) separately."""
    newest = max(versions.values()) if versions else None
    out: list[Claim] = []
    for c in claims:
        by_version: dict[datetime, list[tuple[str, int, int]]] = {}
        for prov in c.provenance:
            by_version.setdefault(versions.get(prov[0], c.course_version), []).append(prov)
        for when in sorted(by_version):
            provs = tuple(by_version[when])
            refs = tuple(f"{p}#{n}" for p, _, n in provs)
            out.append(
                replace(
                    c,
                    source_refs=refs,
                    provenance=provs,
                    course_version=when,
                    superseded=newest is not None and when < newest,
                )
            )
    return out


def _claim_from_doc(d: dict[str, Any]) -> Claim:
    return Claim(
        text=d["text"],
        value=d.get("value"),
        unit=d.get("unit"),
        concepts=tuple(d.get("concept_ids", [])),
        source_refs=tuple(d.get("source_refs", [])),
        provenance=tuple((p["page_id"], p["revision"], p["slide_no"]) for p in d["provenance"]),
        course_version=datetime.fromisoformat(d["course_version"]),
        superseded=bool(d.get("superseded", False)),
    )


def _concept_name(ctx: SpaceContext, res: Resources, cid: str) -> str:
    for gid, aliases in res.glossary:
        if gid == cid:
            return aliases[0] if aliases else cid.removeprefix("concept:")
    return ctx.store.local_concept_name(cid) or cid


def build_course(
    ctx: SpaceContext,
    model: VisionModel,
    embedder: EmbeddingModel,
    res: Resources,
    page: SourcePage,
    *,
    cache_salt: str,
    model_id: str,
) -> bool:
    """Returns True if every affected page was written and grounded."""
    store, labels = ctx.store, ctx.labels
    course = course_id(page)
    pages = sorted(
        (p for p in store.list_source_pages(course) if not store.is_quarantined(p.page_id)),
        key=lambda p: p.updated_date,
    )
    ctx.assert_single_space([p.labels for p in pages])
    slides = _Slides(ctx)

    # Step 3: integrate the course's slide notes.
    course_slides: list[CourseSlide] = []
    evidence_parts: list[str] = []
    for p in pages:
        notes = {n["slide_ref"]: n for n in store.slide_notes(p.page_id, p.revision)}
        got = slides.page(p.page_id)
        page_slides: list[Slide] = list(got[1].values()) if got else []
        for s in page_slides:
            evidence_parts += [s.title, s.text]
            note = notes.get(s.slide_ref)
            if note and note["status"] == "ok" and note["body"]:
                course_slides.append(
                    CourseSlide(
                        s.slide_ref, p.page_id, p.revision, s.number, p.updated_date, note["body"]
                    )
                )
    title = pages[-1].title if pages else page.title
    digest = integrate_course(model, title, course_slides, cache_salt=cache_salt)
    if digest is None:
        return False

    # Step 4: align concepts; store this course's claims with canonical/local IDs.
    names = [*digest.concepts, *(n for c in digest.claims for n in c.concepts)]
    names += [x for r in digest.relations for x in r[:2]]
    # Evidence: the slides' own text plus the notes' points and claims (not their concept
    # lists), so a concept is kept only if the course actually talks about it.
    for cs in course_slides:
        evidence_parts += [str(cs.note.get("point", "")), *map(str, cs.note.get("claims", []))]
        evidence_parts += figure_evidence(cs.note.get("figures", []))
    mapping = align_concepts(
        model,
        res,
        names,
        local_concept=store.local_concept,
        cache_salt=cache_salt,
        evidence="\n".join(evidence_parts),
    )
    store.replace_course_claims(
        course,
        [
            {
                "text": c.text,
                "value": c.value,
                "unit": c.unit,
                "concept_ids": sorted({mapping[n] for n in c.concepts if n in mapping}),
                "source_refs": list(c.source_refs),
                "provenance": [
                    {"page_id": p, "revision": r, "slide_no": n} for p, r, n in c.provenance
                ],
                "course_version": c.course_version.isoformat(),
                "superseded": c.superseded,
            }
            for c in split_versions(list(digest.claims), {p.page_id: p.updated_date for p in pages})
        ],
        labels,
    )
    course_claims = [_claim_from_doc(d) for d in store.course_claims(course)]

    # Step 5: the pages this course touches.
    specs: list[PageSpec] = []
    for cid in sorted({cid for c in course_claims for cid in c.concepts}):
        claims = [_claim_from_doc(d) for d in store.claims_for_concept(cid)]  # all courses
        specs.append(
            PageSpec(
                "concept",
                cid,
                cid,
                _concept_name(ctx, res, cid),
                tuple(claims),
                slides.figures(claims),
            )
        )
    for name, _kind in digest.entities:
        claims = [c for c in course_claims if name in c.text]
        if claims:
            key = "entity:" + store.keyed_digest(normalise(name).encode())[:24]
            specs.append(PageSpec("entity", key, None, name, tuple(claims), slides.figures(claims)))
    course_key = store.keyed_digest(course.encode())[:24]
    if course_claims:
        specs.append(
            PageSpec("course", f"course:{course_key}", None, title, tuple(course_claims),
                     slides.figures(course_claims))
        )  # fmt: skip
        if len({p.page_id for p in pages}) >= 2:
            specs.append(
                PageSpec("synthesis", f"synthesis:{course_key}", None, f"{title}：各版本比較",
                         tuple(course_claims), ())
            )  # fmt: skip
    written = [
        path
        for spec in specs
        if (path := write_page(model, res, store, spec, cache_salt=cache_salt, model_id=model_id))
    ]

    # Step 6: grounding.
    material = _Material(ctx, slides)
    grounded = [
        ground_page(model, store, path, material, cache_salt=cache_salt) for path in written
    ]
    write_index_and_log(
        store,
        entry=f"ingest | page {page.page_id} rev {int(page.revision)} | {len(written)} pages",
        when=datetime.now(UTC),
    )

    # Step 7: rebuild this space's search index and wiki graph.
    _rebuild_index(ctx, embedder)
    _rebuild_graph(ctx)
    return len(written) == len(specs) and all(s == "stable" for s in grounded)


def _rebuild_index(ctx: SpaceContext, embedder: EmbeddingModel) -> None:
    if ctx.index is None:
        raise RuntimeError("space context has no index")
    store = ctx.store
    rows: list[tuple[str, str, str, str]] = []  # (id, kind, ref, text)
    for p in store.wiki_pages():
        got = store.wiki_get(p["path"]) if p.get("status") == "stable" else None
        if got is None:
            continue
        body = _FOOTNOTE_DEF.sub("", parse(got[0]).body)
        for i, para in enumerate(x.strip() for x in body.split("\n\n")):
            if para:
                rows.append((f"{p['path']}#{i}", "page", p["path"], para))
    for page in store.list_all_source_pages():
        for n in store.slide_notes(page.page_id, page.revision):
            note_body: dict[str, Any] = n.get("body") or {}
            text = " ".join([note_body.get("point", ""), *note_body.get("claims", [])]).strip()
            if text:
                rows.append((n["slide_ref"], "slide", n["slide_ref"], text))
    vectors: list[list[float]] = []
    for i in range(0, len(rows), 32):
        vectors += embedder.embed([r[3] for r in rows[i : i + 32]], tag="index")
    ctx.index.replace(
        [Chunk(r[0], r[1], r[2], r[3], v) for r, v in zip(rows, vectors, strict=True)]
    )


def _rebuild_graph(ctx: SpaceContext) -> None:
    store = ctx.store
    pages: list[dict[str, Any]] = []
    edges: list[Edge] = []
    for p in store.wiki_pages():
        got = store.wiki_get(p["path"])
        if got is None:
            continue
        doc = parse(got[0])
        pages.append({"path": p["path"], "kind": p.get("kind"), "concept_id": p.get("concept_id")})
        edges += [
            ("page", p["path"], "LINKS_TO", "page", t) for t in _BUNDLE_LINK.findall(doc.body)
        ]
        for s in doc.frontmatter.get("sources", []):
            edges.append(("page", p["path"], "CITES", "slide", s["id"]))
            edges.append(("slide", s["id"], "IN", "source", s["id"].rpartition("-s")[0]))
        if p.get("concept_id"):
            edges.append(("page", p["path"], "ABOUT", "concept", p["concept_id"]))
    edges = list(dict.fromkeys(edges))
    ctx.graph.replace_wiki_graph(pages, edges, ctx.labels)
    props, similar = graph_metrics(edges)
    ctx.graph.write_metrics(props, similar, ctx.labels)
