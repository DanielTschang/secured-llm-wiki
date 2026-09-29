"""Ingest step 3: integrate a course's slide notes into concepts, claims, relations,
entities and a summary. One course = the space's pages under one platform parent.

The model sees only this course's notes (same space). Every claim must cite slides it
was given; anything else is dropped. Provenance and course versions are the host's.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ingest_worker.steps.common import ROLE, salted
from kc_ids import PageId, Revision
from kc_models import Message, ModelError, TextPart, VisionModel

__all__ = ["Claim", "CourseDigest", "CourseSlide", "integrate_course"]


@dataclass(frozen=True, slots=True)
class CourseSlide:
    slide_ref: str
    page_id: PageId
    page_revision: Revision
    slide_no: int
    course_version: datetime  # the page's platform updated_date
    note: dict[str, Any] = field(repr=False)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Claim(_Strict):
    text: str
    value: float | None
    unit: str | None
    concepts: list[str]
    sources: list[str]


class _Relation(_Strict):
    source: str
    target: str
    kind: Literal["related", "part_of", "causes", "measures", "contrasts"]


class _Entity(_Strict):
    name: str
    kind: Literal["equipment", "mask", "material", "software", "model", "other"]


class _Digest(_Strict):
    concepts: list[str]
    claims: list[_Claim]
    relations: list[_Relation]
    entities: list[_Entity]
    summary: list[str]


@dataclass(frozen=True, slots=True)
class Claim:
    text: str = field(repr=False)
    value: float | None
    unit: str | None
    concepts: tuple[str, ...] = field(repr=False)
    source_refs: tuple[str, ...]
    provenance: tuple[tuple[str, int, int], ...]  # (page_id, revision, slide_no)
    course_version: datetime  # newest version among the cited slides
    # Set by the host: only older versions of its course say this (ADR-008 structure).
    superseded: bool = False

    def as_superseded(self) -> Claim:
        return replace(self, superseded=True)


@dataclass(frozen=True, slots=True)
class CourseDigest:
    concepts: tuple[str, ...] = field(repr=False)
    claims: tuple[Claim, ...]
    relations: tuple[tuple[str, str, str], ...] = field(repr=False)
    entities: tuple[tuple[str, str], ...] = field(repr=False)
    summary: tuple[str, ...] = field(repr=False)


def _slide_block(s: CourseSlide) -> str:
    return f"### {s.slide_ref}（課程版本 {s.course_version.date().isoformat()}）\n" + json.dumps(
        s.note, ensure_ascii=False
    )


def integrate_course(
    model: VisionModel, course_title: str, slides: Sequence[CourseSlide], *, cache_salt: str
) -> CourseDigest | None:
    by_ref = {s.slide_ref: s for s in slides}
    system = salted(cache_salt, TextPart(ROLE))
    user = Message(
        "user",
        [
            TextPart(f"課程：{course_title}\n以下是這門課各版本投影片的判讀筆記："),
            *(TextPart(_slide_block(s)) for s in slides),
            TextPart(
                "整理出這門課的概念、可驗證的敘述（claims，每筆列出支持它的投影片代號）、"
                "概念之間的關係、專有對象（設備、光罩、材料、軟體、模型）與課程重點。"
                "只能引用上面列出的投影片代號。不同版本說法不同時，兩者都列為 claims。"
            ),
        ],
    )
    schema = _Digest.model_json_schema()
    for _ in range(2):
        try:
            raw = model.chat([system, user], json_schema=schema, tag="course:integrate")
            digest = _Digest.model_validate_json(raw)
        except ModelError, ValidationError, ValueError:
            continue
        claims: list[Claim] = []
        for c in digest.claims:
            refs = tuple(dict.fromkeys(c.sources))
            if not refs or any(r not in by_ref for r in refs):
                continue  # must cite, and only this course's slides
            cited = [by_ref[r] for r in refs]
            claims.append(
                Claim(
                    text=c.text,
                    value=c.value,
                    unit=c.unit,
                    concepts=tuple(c.concepts),
                    source_refs=refs,
                    provenance=tuple((s.page_id, int(s.page_revision), s.slide_no) for s in cited),
                    course_version=max(s.course_version for s in cited),
                )
            )
        return CourseDigest(
            concepts=tuple(dict.fromkeys(digest.concepts)),
            claims=tuple(claims),
            relations=tuple((r.source, r.target, r.kind) for r in digest.relations),
            entities=tuple((e.name, e.kind) for e in digest.entities),
            summary=tuple(digest.summary),
        )
    return None
