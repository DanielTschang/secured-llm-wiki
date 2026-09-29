"""Ingest step 6: grounding (ADR-005 point 6, ADR-010 point 4).

Every sentence of a page is checked against the slides its footnotes cite, with the
slides' text and images. Unsupported sentences are deleted, inferences are marked, and
uncited sentences are deleted. A page that passes becomes `stable` and machine-verified
(`kc-grounding/1`; never a human actor, ADR-004); if the check itself fails the page is
left untouched as `draft`. Only this space's cited slides are shown to the model.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

from ingest_worker.steps.common import ROLE, salted
from kc_models import ImagePart, Message, ModelError, Part, TextPart, VisionModel
from kc_okf import Document, HostFields, Source, build_frontmatter, clean_body, parse, render
from kc_store.space import SpaceStore

__all__ = [
    "INFERENCE",
    "VERIFIER",
    "Sentence",
    "SlideMaterial",
    "ground_page",
    "mark_inference",
    "split_sentences",
]

VERIFIER = "kc-grounding/1"
_REF = re.compile(r"\[\^([A-Za-z0-9_#.-]+)\]")
_SENTENCE = re.compile(r"[^。！？!?\n]+?(?:[。！？!?]|$)(?:\[\^[^\]]+\])*")
_FIGURE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_FIGURE = re.compile(r"kc-figure://(att_[0-9a-f]{16})")


@dataclass(frozen=True, slots=True)
class SlideMaterial:
    text: str = field(repr=False)
    images: tuple[bytes, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class Sentence:
    line: int
    text: str = field(repr=False)
    refs: tuple[str, ...]

    @property
    def cited(self) -> bool:
        return bool(self.refs)


def _is_prose(line: str) -> bool:
    s = line.strip()
    return (
        bool(s) and not s.startswith("#") and not s.startswith("[^") and not _FIGURE_LINE.match(s)
    )


_LIST_MARKER = re.compile(r"^\s*(?:[-*]\s+)+")


def split_sentences(body: str) -> list[Sentence]:
    """Prose sentences with their footnotes. List markers are not part of a sentence, and a
    fragment that is only footnotes belongs to the sentence before it."""
    out: list[Sentence] = []
    for i, line in enumerate(body.split("\n")):
        if not _is_prose(line):
            continue
        for m in _SENTENCE.finditer(_LIST_MARKER.sub("", line)):
            text = m.group(0).strip()
            if not text or text in {"-", "*"}:
                continue
            if _REF.sub("", text).strip() == "" and out and out[-1].line == i:
                prev = out.pop()
                text = prev.text + text.replace(" ", "")
                out.append(Sentence(i, text, tuple(_REF.findall(text))))
                continue
            out.append(Sentence(i, text, tuple(_REF.findall(text))))
    return out


class _Verdict(BaseModel):
    n: int
    verdict: Literal["supported", "unsupported", "inference"]


class _Verdicts(BaseModel):
    verdicts: list[_Verdict]


INFERENCE = "（推論）"
_TAIL = re.compile(r"([。！？!?]?)((?:\[\^[^\]]+\])*)$")


def mark_inference(text: str) -> str:
    """Mark once, before the sentence's final punctuation and footnotes."""
    if INFERENCE in text:
        return text
    m = _TAIL.search(text)
    if m is None:  # the pattern matches any string; kept for the type checker
        return text + INFERENCE
    return f"{text[: m.start()]}{INFERENCE}{m.group(1)}{m.group(2)}"


def _rebuild(body: str, keep: dict[int, list[str]]) -> str:
    lines = body.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        if not _is_prose(line):
            if not line.strip().startswith("[^"):  # footnote definitions are re-added
                out.append(line)
            continue
        kept = keep.get(i, [])
        if kept:
            prefix = re.match(r"^\s*(?:[-*]\s+)?", line)
            out.append((prefix.group(0) if prefix else "") + "".join(kept))
    # Drop headings whose section ended up empty.
    result: list[str] = []
    for i, line in enumerate(out):
        if line.startswith("#"):
            rest = out[i + 1 :]
            nxt = next((r for r in rest if r.strip()), "")
            if not nxt or nxt.startswith("#"):
                continue
        result.append(line)
    return "\n".join(result).strip() + "\n"


def ground_page(
    model: VisionModel,
    store: SpaceStore,
    path: str,
    material: Mapping[str, SlideMaterial],
    *,
    cache_salt: str,
) -> str:
    """Returns the page's resulting status ("stable" or "draft")."""
    current = store.wiki_get(path)
    if current is None:
        return "draft"
    doc = parse(current[0])
    fm = doc.frontmatter
    sentences = split_sentences(doc.body)
    cited = [s for s in sentences if s.cited]
    sources = [r for r in dict.fromkeys(r for s in cited for r in s.refs) if r in material]

    parts: list[Part] = []
    for sid in sources:
        parts.append(TextPart(f"來源 {sid}：{material[sid].text}"))
        parts += [ImagePart(img) for img in material[sid].images]
    numbered = "\n".join(f"{i + 1}. {s.text}" for i, s in enumerate(cited))
    parts.append(
        TextPart(
            f"逐句判斷下列敘述是否被它所引用（[^來源代號]）的來源投影片支持：\n{numbered}\n"
            "supported：來源明確支持；inference：來源沒有直接寫出但可合理推得；"
            "unsupported：來源不支持或與來源矛盾。"
        )
    )
    schema = {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer", "minimum": 1, "maximum": max(1, len(cited))},
                        "verdict": {"enum": ["supported", "unsupported", "inference"]},
                    },
                    "required": ["n", "verdict"],
                },
            }
        },
        "required": ["verdicts"],
    }
    messages = [salted(cache_salt, TextPart(ROLE)), Message("user", parts)]
    verdicts: dict[int, str] | None = None
    for _ in range(2):
        try:
            raw = model.chat(messages, json_schema=schema, tag="wiki:ground")
            verdicts = {v.n: v.verdict for v in _Verdicts.model_validate_json(raw).verdicts}
            break
        except ModelError, ValidationError, ValueError:
            continue
    if verdicts is None:
        return "draft"

    keep: dict[int, list[str]] = {}
    for i, s in enumerate(cited):
        # Missing verdicts, and sentences citing slides we could not show, fail closed.
        v = verdicts.get(i + 1, "unsupported")
        if v == "unsupported" or any(r not in material for r in s.refs):
            continue
        keep.setdefault(s.line, []).append(mark_inference(s.text) if v == "inference" else s.text)

    all_sources = tuple(Source(**s) for s in fm["sources"])
    kept_refs = {r for texts in keep.values() for t in texts for r in _REF.findall(t)}
    used = tuple(s for s in all_sources if s.id in kept_refs)
    figures = frozenset(_FIGURE.findall(doc.body))
    body = clean_body(_rebuild(doc.body, keep), used, figures, lambda _n: None)
    status = "stable" if kept_refs else "draft"
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    host = HostFields(
        type=fm["type"],
        sources=used,
        generated_by=fm["generated"]["by"],
        generated_at=fm["generated"]["at"],
        status=status,
        kc_labels=tuple(sorted(store.labels.spaces)),
        kc_concept=fm.get("kc_concept"),
        verified_by=VERIFIER if status == "stable" else None,
        verified_at=now if status == "stable" else None,
    )
    new_fm = build_frontmatter(host, fm)
    store.wiki_put(path, render(Document(new_fm, body)), current[1], store.labels)
    entry = next((p for p in store.wiki_pages() if p["path"] == path), None)
    if entry is not None:
        meta = {k: v for k, v in entry.items() if k not in {"_id", "path", "labels"}}
        store.upsert_wiki_page(
            path, {**meta, "status": status, "source_ids": [s.id for s in used]}, store.labels
        )
    return status
