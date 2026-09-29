"""Ingest step 1: split a page into slides (ADR-005).

Slides are separated by a line containing only `---`; `## n. Title` is the slide heading;
images are `![alt](attachments/<name>)` and map to attachment IDs through the page's own
attachment map. Cross-space includes stay references (never expanded or fetched) and
links keep only their text (never followed). The format assumptions live here only.
"""

import re
from dataclasses import dataclass, field

from kc_ids import AttachmentId, Revision
from kc_labels import Labels
from kc_store.space import SourcePage

__all__ = [
    "ImageSegment",
    "IncludeRef",
    "Segment",
    "Slide",
    "TextSegment",
    "course_id",
    "parse_page",
]

_SEPARATOR = re.compile(r"(?m)^---[ \t]*$")
_HEADING = re.compile(r"(?m)^##[ \t]+(?:(\d+)\.[ \t]*)?(.*)$")
_PAGE_TITLE = re.compile(r"(?m)^#[ \t]+.*$")
_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_INCLUDE = re.compile(r'\{\{include\s+page="([^"]+)"\s+section="([^"]+)"\s*\}\}')
_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\([^)]*\)")
_INCLUDE_MARKER = "[外部引用，未展開]"


@dataclass(frozen=True, slots=True)
class IncludeRef:
    """A reference to another page's section. Recorded for provenance, never expanded."""

    page_id: str
    section: str


@dataclass(frozen=True, slots=True)
class TextSegment:
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ImageSegment:
    attachment_id: AttachmentId


type Segment = TextSegment | ImageSegment


@dataclass(frozen=True, slots=True)
class Slide:
    slide_ref: str
    number: int
    page_revision: Revision
    title: str = field(repr=False)
    segments: tuple[Segment, ...] = field(repr=False)
    includes: tuple[IncludeRef, ...]
    labels: Labels

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments if isinstance(s, TextSegment)).strip()

    @property
    def figures(self) -> tuple[AttachmentId, ...]:
        return tuple(s.attachment_id for s in self.segments if isinstance(s, ImageSegment))


def course_id(page: SourcePage) -> str:
    """Pages under the same platform parent form one course (e.g. yearly versions)."""
    return page.parent_id or page.page_id


def _clean(text: str, includes: list[IncludeRef]) -> str:
    def include(m: re.Match[str]) -> str:
        includes.append(IncludeRef(page_id=m.group(1), section=m.group(2)))
        return _INCLUDE_MARKER

    text = _INCLUDE.sub(include, text)
    return _LINK.sub(lambda m: m.group(1), text)


def _segments(
    body: str, names: dict[str, AttachmentId], includes: list[IncludeRef]
) -> tuple[Segment, ...]:
    out: list[Segment] = []
    pos = 0
    for m in _IMAGE.finditer(body):
        before = _clean(body[pos : m.start()], includes).strip()
        if before:
            out.append(TextSegment(before))
        ref = m.group(1)
        name = ref.removeprefix("attachments/")
        att = names.get(name) if ref.startswith("attachments/") and "/" not in name else None
        if att is not None:
            out.append(ImageSegment(att))
        pos = m.end()
    rest = _clean(body[pos:], includes).strip()
    if rest:
        out.append(TextSegment(rest))
    return tuple(out)


def parse_page(page: SourcePage, markdown: str) -> list[Slide]:
    names = dict(page.attachment_map)
    slides: list[Slide] = []
    for chunk in _SEPARATOR.split(markdown):
        chunk = _PAGE_TITLE.sub("", chunk, count=1) if not slides else chunk
        heading = _HEADING.search(chunk)
        if heading is None and not chunk.strip():
            continue
        number = len(slides) + 1
        title = ""
        body = chunk
        if heading is not None:
            title = heading.group(2).strip()
            body = chunk[: heading.start()] + chunk[heading.end() :]
        includes: list[IncludeRef] = []
        slides.append(
            Slide(
                slide_ref=f"{page.page_id}#{number}",
                number=number,
                page_revision=page.revision,
                title=_clean(title, includes),
                segments=_segments(body, names, includes),
                includes=tuple(includes),
                labels=page.labels,
            )
        )
    return slides
