"""Ingest step 5: write space wiki pages as OKF v0.2 documents (ADR-008, ADR-010).

The model edits one page at a time from this space's claims (with their source IDs and
course versions), the space's index and the page's current text. It supplies title,
description, tags and the body only; the host builds the frontmatter, cleans the body
(own sources, own figures, in-bundle links), writes with ETag fencing and regenerates
index.md and log.md.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, ValidationError

from ingest_worker.steps.common import ROLE, salted
from ingest_worker.steps.integrate import Claim
from ingest_worker.steps.read import Resources
from kc_models import Message, ModelError, TextPart, VisionModel
from kc_okf import (
    Document,
    HostFields,
    Source,
    UnknownSource,
    build_frontmatter,
    clean_body,
    new_ulid,
    page_path,
    parse,
    render,
)
from kc_store.space import SpaceStore

__all__ = ["PageSpec", "source_id", "write_index_and_log", "write_page"]

TEMPLATES = {
    "concept": "## 定義、## 原理、## 本 team 實務、## 常見問題、## 相關概念",
    "entity": "## 概要、## 用途、## 版本與設定、## 相關概念",
    "course": "## 課程概要、## 重點、## 涵蓋概念",
    "synthesis": "## 比較、## 差異與原因、## 相關概念",
}
_KIND_TITLES = {"concept": "概念", "entity": "專有對象", "course": "課程", "synthesis": "整合比較"}


@dataclass(frozen=True, slots=True)
class PageSpec:
    kind: str
    key: str
    concept_id: str | None
    subject: str = field(repr=False)
    claims: tuple[Claim, ...]
    figures: tuple[tuple[str, str], ...] = ()  # (attachment id, slide ref) of cited slides


class _PageOut(BaseModel):
    # Extra fields (e.g. a model trying to set kc_labels or status) are dropped: the
    # frontmatter takes only title, description and tags from the model (ADR-010).
    model_config = ConfigDict(extra="ignore", frozen=True)
    title: str
    description: str
    tags: list[str]
    body: str
    evolution: str = ""


def strip_markers(text: str) -> str:
    """Grounding's inference markers are the host's; the writer never sees or copies them."""
    return text.replace("（推論）", "")


def source_id(page_id: str, slide_no: int) -> str:
    return f"{page_id}-s{slide_no}"


def _sources(store: SpaceStore, claims: Sequence[Claim]) -> tuple[Source, ...]:
    out: dict[str, Source] = {}
    for c in claims:
        for page_id, rev, slide_no in c.provenance:
            sid = source_id(page_id, slide_no)
            out[sid] = Source(
                sid,
                f"kc://{store.space_id}/pages/{page_id}?rev={rev}&slide={slide_no}",
                c.course_version.isoformat().replace("+00:00", "Z"),
            )
    return tuple(out.values())


_EVOLUTION = "## 版本演變"
_SPLIT_KINDS = frozenset({"concept", "entity"})


def _drop_evolution(body: str) -> str:
    """The version-history section is assembled by the host, never taken from `body`:
    drop that section (up to the next heading), keep everything else."""
    out: list[str] = []
    skipping = False
    for line in body.split("\n"):
        if line.startswith("## "):
            skipping = line.strip() == _EVOLUTION
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _without_defs(body: str) -> str:
    return "\n".join(line for line in body.split("\n") if not line.startswith("[^")).rstrip()


def _claim_lines(claims: Sequence[Claim]) -> str:
    lines: list[str] = []
    for c in sorted(claims, key=lambda c: c.course_version):
        refs = " ".join(f"[^{source_id(p, n)}]" for p, _, n in c.provenance)
        lines.append(f"- （課程版本 {c.course_version.date().isoformat()}）{c.text} {refs}")
    return "\n".join(lines)


def _resolver(store: SpaceStore, own_path: str):
    by_title = {p["title"].strip().lower(): p["path"] for p in store.wiki_pages() if p.get("title")}

    def resolve(name: str) -> str | None:
        path = by_title.get(name.strip().lower())
        return None if path == own_path else path

    return resolve


def write_page(
    model: VisionModel,
    res: Resources,
    store: SpaceStore,
    spec: PageSpec,
    *,
    cache_salt: str,
    model_id: str,
) -> str | None:
    """Create or update one page. Returns its bundle path, or None if the model's output
    could not be made valid (nothing is written then)."""
    entry = store.wiki_page_by_key(spec.key)
    path = entry["path"] if entry else page_path(spec.kind, new_ulid())
    current = store.wiki_get(path)
    existing_body = strip_markers(parse(current[0]).body) if current else ""
    index = store.wiki_get("index.md")
    sources = _sources(store, spec.claims)
    figures = frozenset(att for att, _ in spec.figures)
    current_claims = [c for c in spec.claims if not c.superseded]
    old_claims = [c for c in spec.claims if c.superseded]
    split = spec.kind in _SPLIT_KINDS
    current_sources = _sources(store, current_claims) if split else sources
    old_sources = _sources(store, old_claims)

    rules = (
        "撰寫或修改一個 space wiki 頁。規則：\n"
        "- 只根據提供的 claims 撰寫；每一句都要以註腳 [^來源代號] 引用支持它的來源，"
        "只能使用 claims 旁列出的來源代號。\n"
        "- body 只寫**現行** claims；**舊版** claims 只能寫在 evolution 欄位（版本演變），"
        "說明舊版的說法與改變。沒有舊版 claims 時 evolution 留空。\n"
        f"- 段落結構：{TEMPLATES[spec.kind]}（沒有內容的段落可省略）。\n"
        "- 提到其他頁面時用 [[頁面標題]]；可嵌入下列圖片之一：![說明](kc-figure://<附件代號>)。\n"
        "- 只輸出 title、description（一句話）、tags 與 body。"
    )
    parts = [
        TextPart(f"頁面類型：{_KIND_TITLES[spec.kind]}；主題：{spec.subject}"),
        TextPart("本 space 的 index：\n" + (index[0] if index else "（尚無頁面）")),
        TextPart("目前的頁面內文：\n" + (existing_body or "（新頁面）")),
    ]
    if split:
        parts.append(
            TextPart("現行 claims（寫在 body）：\n" + (_claim_lines(current_claims) or "（無）"))
        )
        parts.append(
            TextPart(
                "舊版 claims（只能寫在 evolution）：\n" + (_claim_lines(old_claims) or "（無）")
            )
        )
    else:
        parts.append(TextPart("claims：\n" + _claim_lines(spec.claims)))
    if spec.figures:
        figs = "\n".join(f"- kc-figure://{att}（投影片 {ref}）" for att, ref in spec.figures)
        parts.append(TextPart("可用的圖片：\n" + figs))
    messages = [salted(cache_salt, TextPart(ROLE), TextPart(rules)), Message("user", parts)]
    schema = _PageOut.model_json_schema()

    for _ in range(2):
        try:
            raw = model.chat(messages, json_schema=schema, tag=f"wiki:{spec.kind}")
            out = _PageOut.model_validate_json(raw)
            resolve = _resolver(store, path)
            if split:
                # Each part may cite only its own claims' sources (UnknownSource otherwise).
                main = _drop_evolution(out.body)
                clean_body(main, current_sources, figures, resolve)
                parts_out = [_without_defs(main)]
                if old_claims and out.evolution.strip():
                    evolution = _drop_evolution(out.evolution.replace(_EVOLUTION, ""))
                    clean_body(evolution, old_sources, figures, resolve)
                    parts_out += [_EVOLUTION, _without_defs(evolution)]
                body = clean_body("\n\n".join(parts_out), sources, figures, resolve)
            else:
                body = clean_body(out.body, sources, figures, resolve)
            host = HostFields(
                type=spec.kind,
                sources=sources,
                generated_by=f"model:{model_id}",
                generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                status="draft",
                kc_labels=tuple(sorted(store.labels.spaces)),
                kc_concept=spec.concept_id,
            )
            fm = build_frontmatter(host, out.model_dump())
        except ModelError, ValidationError, UnknownSource, ValueError:
            continue
        store.wiki_put(
            path, render(Document(fm, body)), current[1] if current else None, store.labels
        )
        store.upsert_wiki_page(
            path,
            {
                "kind": spec.kind,
                "key": spec.key,
                "concept_id": spec.concept_id,
                "title": fm["title"],
                "description": fm["description"],
                "status": "draft",
                "source_ids": [s.id for s in sources],
            },
            store.labels,
        )
        return path
    return None


def write_index_and_log(store: SpaceStore, *, entry: str, when: datetime) -> None:
    """index.md (catalogue by kind) and log.md (append-only) are written by the host."""
    sections: list[str] = ["# Index"]
    pages = store.wiki_pages()
    for kind, title in _KIND_TITLES.items():
        rows = [p for p in pages if p.get("kind") == kind]
        if rows:
            sections.append(f"\n## {title}\n")
            sections += [
                f"- [{p['title']}](/{p['path']}) — {p.get('description', '')}" for p in rows
            ]
    current = store.wiki_get("index.md")
    store.wiki_put(
        "index.md", "\n".join(sections) + "\n", current[1] if current else None, store.labels
    )

    log = store.wiki_get("log.md")
    text = (log[0] if log else "# Log\n") + f"\n## [{when.date().isoformat()}] {entry}\n"
    store.wiki_put("log.md", text, log[1] if log else None, store.labels)
