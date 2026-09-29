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
            ref = re.search(r"\[\^([A-Za-z0-9_#.-]+)\]", text)
            cite = f"[^{ref.group(1)}]" if ref else ""
            return json.dumps(
                {"title": f"頁 {tag} {cite}", "description": "d", "tags": [],
                 "body": f"## 定義\n內容一。{cite} 內容二。{cite}\n"},
                ensure_ascii=False,
            )  # fmt: skip
        if tag == "wiki:ground":
            n = len(re.findall(r"^\d+\. ", text, flags=re.M))
            return json.dumps(
                {"verdicts": [{"n": i + 1, "verdict": "supported"} for i in range(n)]}
            )
        raise AssertionError(f"unexpected tag {tag}")

    return FakeModel(respond)
