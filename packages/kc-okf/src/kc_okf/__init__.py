"""OKF v0.2 documents for space wiki bundles (ADR-010).

The host owns `type`, `sources`, `generated`, `verified`, `status`, `kc_labels` and
`kc_concept`; the model may only supply `title`, `description` and `tags` plus the body.
Bodies are cleaned before storage: footnotes must cite the page's own sources, figures
must be the space's own cited attachments, and links may only point inside the bundle.
Paths are opaque ULIDs, never names (paths reach logs, invariant 7).
"""

import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import yaml

__all__ = [
    "HOST_FIELDS",
    "KINDS",
    "LLM_FIELDS",
    "Document",
    "HostFields",
    "Source",
    "UnknownSource",
    "build_frontmatter",
    "clean_body",
    "is_bundle_path",
    "new_ulid",
    "page_path",
    "parse",
    "render",
]

HOST_FIELDS = ("type", "sources", "generated", "verified", "status", "kc_labels", "kc_concept")
LLM_FIELDS = ("title", "description", "tags")
KINDS = {"concept": "concepts", "entity": "entities", "course": "courses", "synthesis": "synthesis"}
STATUSES = ("draft", "stable", "deprecated")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_PATH = re.compile(rf"(?:index|log)\.md|(?:{'|'.join(KINDS.values())})/{_ULID}\.md")
_FOOTNOTE_REF = re.compile(r"\[\^([A-Za-z0-9_#.-]+)\](?!:)")
_FOOTNOTE_DEF = re.compile(r"(?m)^\[\^[^\]]+\]:.*$\n?")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)\)")
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]*)\)")
_FIGURE = re.compile(r"kc-figure://(att_[0-9a-f]{16})")


class UnknownSource(ValueError):
    """The body cites a footnote id that is not one of the page's sources."""


def new_ulid() -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10))
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def page_path(kind: str, ulid: str) -> str:
    if re.fullmatch(_ULID, ulid) is None:
        raise ValueError("invalid ulid")
    return f"{KINDS[kind]}/{ulid}.md"


def is_bundle_path(path: str) -> bool:
    return _PATH.fullmatch(path) is not None


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    resource: str
    last_modified: str


@dataclass(frozen=True, slots=True)
class HostFields:
    type: str
    sources: tuple[Source, ...]
    generated_by: str
    generated_at: str
    status: str
    kc_labels: tuple[str, ...]
    kc_concept: str | None = None
    verified_by: str | None = None
    verified_at: str | None = None

    def __post_init__(self) -> None:
        if self.type not in KINDS:
            raise ValueError("unknown page type")
        if self.status not in STATUSES:
            raise ValueError("unknown status")
        if not self.kc_labels:
            raise ValueError("labels must not be empty")
        if self.status == "stable" and not (self.verified_by and self.verified_at):
            raise ValueError("stable pages must be verified")
        if self.verified_by is not None and not self.verified_by.startswith("kc-grounding/"):
            raise ValueError("only machine grounding verifies pages (ADR-004)")


def _llm_fields(llm: Mapping[str, Any]) -> dict[str, Any]:
    title, description, tags = llm.get("title"), llm.get("description", ""), llm.get("tags", [])
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if not isinstance(description, str):
        raise ValueError("description must be a string")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):  # pyright: ignore[reportUnknownVariableType]
        raise ValueError("tags must be a list of strings")
    return {"title": title.strip(), "description": description.strip(), "tags": list(tags)}  # pyright: ignore[reportUnknownArgumentType]


def build_frontmatter(host: HostFields, llm: Mapping[str, Any]) -> dict[str, Any]:
    """Frontmatter = the model's title/description/tags + the host's fields. Anything else
    the model produced, including host fields, is dropped."""
    fm: dict[str, Any] = {
        "type": host.type,
        **_llm_fields(llm),
        "sources": [
            {"id": s.id, "resource": s.resource, "last_modified": s.last_modified}
            for s in host.sources
        ],
        "generated": {"by": host.generated_by, "at": host.generated_at},
        "status": host.status,
        "kc_labels": sorted(str(label) for label in host.kc_labels),  # plain str for YAML
    }
    if host.verified_by:
        fm["verified"] = {"by": host.verified_by, "at": host.verified_at}
    if host.kc_concept:
        fm["kc_concept"] = str(host.kc_concept)
    return fm


def clean_body(
    body: str,
    sources: Iterable[Source],
    figures: frozenset[str],
    resolve: Callable[[str], str | None],
) -> str:
    """Enforce the body rules and append footnote definitions from the host's sources."""
    by_id = {s.id: s for s in sources}
    body = _FOOTNOTE_DEF.sub("", body)
    cited = _FOOTNOTE_REF.findall(body)
    unknown = [c for c in cited if c not in by_id]
    if unknown:
        raise UnknownSource("footnote cites an unknown source")

    def image(m: re.Match[str]) -> str:
        fig = _FIGURE.fullmatch(m.group(2))
        return m.group(0) if fig is not None and fig.group(1) in figures else ""

    def wikilink(m: re.Match[str]) -> str:
        path = resolve(m.group(1).strip())
        return f"[{m.group(1)}](/{path})" if path and is_bundle_path(path) else m.group(1)

    def link(m: re.Match[str]) -> str:
        target = m.group(2)
        return m.group(0) if is_bundle_path(target.removeprefix("/")) else m.group(1)

    body = _IMAGE.sub(image, body)
    body = _WIKILINK.sub(wikilink, body)
    body = _LINK.sub(link, body)
    used = list(dict.fromkeys(cited))
    defs = "\n".join(f"[^{i}]: {by_id[i].resource}" for i in used)
    return body.rstrip() + ("\n\n" + defs if defs else "") + "\n"


@dataclass(frozen=True, slots=True)
class Document:
    frontmatter: dict[str, Any]
    body: str


def render(doc: Document) -> str:
    fm = yaml.safe_dump(doc.frontmatter, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{doc.body.lstrip()}"


def parse(text: str) -> Document:
    m = re.match(r"---\n(.*?)\n---\n\n?(.*)", text, re.S)
    if m is None:
        raise ValueError("not an OKF document")
    loaded: object = yaml.safe_load(m.group(1)) or {}
    if not isinstance(loaded, dict):
        raise ValueError("frontmatter must be a mapping")
    fm = cast(dict[str, Any], loaded)
    return Document(fm, m.group(2))
