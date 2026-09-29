"""A FakeModel that answers every ingest step plausibly, for pipeline tests."""

import json
import re
from collections.abc import Sequence

from kc_models import FakeModel, Message, TextPart


def _text(messages: Sequence[Message]) -> str:
    return "\n".join(p.text for m in messages for p in m.parts if isinstance(p, TextPart))


def fake_pipeline_model(figure_counts: dict[str, int]) -> FakeModel:
    def respond(messages: Sequence[Message], tag: str) -> str:
        text = _text(messages)
        if tag.endswith(":classify"):
            return json.dumps({"figure_types": ["other"] * figure_counts[tag.split(":")[0]]})
        if tag.endswith(":read"):
            ref = tag.split(":")[0]
            figs = [{"type": "other", "reads": {}, "numbers_from_figure": False, "confidence": 0.5}]
            return json.dumps(
                {
                    "point": f"重點 {ref}",
                    "figures": figs * figure_counts.get(ref, 0),
                    "claims": [f"敘述 {ref}"],
                    "concepts": ["MEEF", "光罩誤差放大因子"],
                },
                ensure_ascii=False,
            )
        if tag == "course:integrate":
            refs = re.findall(r"^### (\S+#\d+)", text, flags=re.M)
            return json.dumps(
                {
                    "concepts": ["MEEF", "奇特新詞"],
                    "claims": [
                        {"text": f"說法 {r}", "value": None, "unit": None,
                         "concepts": ["MEEF"], "sources": [r]}
                        for r in refs[:3]
                    ],
                    "relations": [],
                    "entities": [{"name": "說法", "kind": "other"}],
                    "summary": ["課程重點"],
                },
                ensure_ascii=False,
            )  # fmt: skip
        if tag == "course:align":
            return json.dumps({"mappings": []})
        if tag.startswith("wiki:") and tag != "wiki:ground":
            ref_pat = r"\[\^([A-Za-z0-9_#.-]+)\]"
            current = old = ""
            if "現行 claims" in text:  # concept/entity pages: current vs superseded split
                current = text.split("現行 claims", 1)[1].split("舊版 claims", 1)[0]
                old = text.split("舊版 claims", 1)[1]
            else:
                current = text
            cur = re.search(ref_pat, current)
            prev = re.search(ref_pat, old)
            body = f"## 定義\n內容一。[^{cur.group(1)}] 內容二。[^{cur.group(1)}]\n" if cur else ""
            evolution = f"舊版說法。[^{prev.group(1)}]\n" if prev else ""
            first = cur or prev
            return json.dumps(
                {"title": f"頁 {tag} {first.group(1) if first else ''}",
                 "description": "d", "tags": [], "body": body, "evolution": evolution},
                ensure_ascii=False,
            )  # fmt: skip
        if tag == "wiki:ground":
            n = len(re.findall(r"^\d+\. ", text, flags=re.M))
            return json.dumps(
                {"verdicts": [{"n": i + 1, "verdict": "supported"} for i in range(n)]}
            )
        raise AssertionError(f"unexpected tag {tag}")

    return FakeModel(respond)
