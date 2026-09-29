"""Ingest step 4: align concept names to the public canonical glossary.

Deterministic alias matching first; for the rest the model may only choose a glossary
ID or "none". "none" becomes the space's own local concept. The glossary is read-only:
nothing here can add to it (a new term there would tell everyone a space uses it).
"""

import unicodedata
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ValidationError

from ingest_worker.steps.common import ROLE, salted
from ingest_worker.steps.read import Resources
from kc_models import Message, ModelError, TextPart, VisionModel

__all__ = ["align_concepts", "normalise"]


def normalise(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).lower().split())


class _Mapping(BaseModel):
    name: str
    concept_id: str


class _Mappings(BaseModel):
    mappings: list[_Mapping]


def align_concepts(
    model: VisionModel,
    res: Resources,
    names: Sequence[str],
    *,
    local_concept: Callable[[str], str],
    cache_salt: str,
) -> dict[str, str]:
    ids = {cid for cid, _ in res.glossary}
    alias_index: dict[str, str] = {}
    for cid, aliases in res.glossary:
        alias_index[normalise(cid)] = cid
        alias_index[normalise(cid.removeprefix("concept:"))] = cid
        for a in aliases:
            alias_index[normalise(a)] = cid

    out: dict[str, str] = {}
    unknown: list[str] = []
    for name in dict.fromkeys(names):
        cid = alias_index.get(normalise(name))
        if cid is not None:
            out[name] = cid
        elif name.strip():
            unknown.append(name)
    if not unknown:
        return out

    choices = sorted(ids)
    schema = {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"enum": unknown},
                        "concept_id": {"enum": [*choices, "none"]},
                    },
                    "required": ["name", "concept_id"],
                },
            }
        },
        "required": ["mappings"],
    }
    glossary = "\n".join(f"- {cid}：{'、'.join(a)}" for cid, a in res.glossary)
    messages = [
        salted(cache_salt, TextPart(ROLE)),
        Message(
            "user",
            [
                TextPart(f"標準術語表：\n{glossary}"),
                TextPart(
                    "把下列每個用語對應到術語表的一個 ID；意義確實相同才對應，"
                    f"否則回答 none。用語：{'、'.join(unknown)}"
                ),
            ],
        ),
    ]
    chosen: dict[str, str] = {}
    try:
        raw = model.chat(messages, json_schema=schema, tag="course:align")
        for m in _Mappings.model_validate_json(raw).mappings:
            if m.name in unknown and m.concept_id in ids:
                chosen[m.name] = m.concept_id  # anything outside the glossary is ignored
    except ModelError, ValidationError, ValueError:
        pass
    for name in unknown:
        out[name] = chosen.get(name) or local_concept(name)
    return out
