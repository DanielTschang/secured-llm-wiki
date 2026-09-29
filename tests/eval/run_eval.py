"""Quality evaluation of ingest steps 1-2 against gold/slides.json (make eval).

Runs on the host against the on-prem model server (OpenAI-compatible; Ollama in dev),
with in-memory storage and the synthetic test set only. Prints a per-slide report and
writes tests/eval/results/latest.json.
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1] / "support"))

from testset import GOLD, MANIFEST, load_page

from ingest_worker.steps.parse import parse_page
from ingest_worker.steps.read import CourseContext, Resources, read_slide
from kc_models import OpenAICompatibleBackend

REPO = Path(__file__).parents[2]
OUT = Path(__file__).parent / "results/latest.json"


def as_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)\s*[a-zA-Zµ%]*\s*", v)
        return float(m.group(1)) if m else None
    return None


def value_ok(gold: Any, got: Any, tol: float | None) -> bool:
    if isinstance(gold, list):
        items = got if isinstance(got, list) else [got]
        return {str(x).lower() for x in gold} <= {str(x).lower() for x in items}
    if isinstance(gold, int | float):
        n = as_number(got)
        return n is not None and abs(n - float(gold)) <= (tol if tol is not None else 1e-6)
    return isinstance(got, str) and got.strip().lower() == str(gold).strip().lower()


def score(ref: str, gold: dict[str, Any], note: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"slide": ref, "status": "ok" if note else "failed"}
    figs = (note or {}).get("figures", [])
    gold_figs = gold.get("figures", [])
    out["types_ok"] = [f.get("type") for f in figs] == [g["type"] for g in gold_figs]
    reads_total = reads_ok = nff_total = nff_ok = 0
    details: list[str] = []
    for i, g in enumerate(gold_figs):
        got = figs[i] if i < len(figs) else {}
        for key, gv in (g.get("must_read") or {}).items():
            reads_total += 1
            gv_got = (got.get("reads") or {}).get(key)
            ok = value_ok(gv, gv_got, g.get("tolerance"))
            reads_ok += ok
            if not ok:
                details.append(f"{key}: want {gv!r} got {gv_got!r}")
        if g.get("numbers_from_figure") is not None:
            nff_total += 1
            nff_ok += got.get("numbers_from_figure") == g["numbers_from_figure"]
    out |= {
        "reads_ok": reads_ok,
        "reads_total": reads_total,
        "nff_ok": nff_ok,
        "nff_total": nff_total,
    }
    text = json.dumps(note or {}, ensure_ascii=False)
    out["must_not_contain_ok"] = all(s not in text for s in gold.get("must_not_contain", []))
    out["misses"] = details
    return out


def main() -> None:
    model = OpenAICompatibleBackend(
        os.environ.get("KC_EVAL_MODEL_URL", "http://127.0.0.1:11434/v1"),
        os.environ.get("KC_EVAL_MODEL", "qwen2.5vl:7b"),
        allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
    )
    res = Resources.load(REPO / "schema")
    only = set(sys.argv[1:])
    results: list[dict[str, Any]] = []
    start = time.monotonic()
    for meta in MANIFEST["pages"]:
        page, md, blobs = load_page(meta["page_id"])
        prev: str | None = None
        terms: list[str] = []
        for slide in parse_page(page, md):
            if slide.slide_ref not in GOLD or (only and slide.slide_ref not in only):
                continue
            ctx = CourseContext(page.title, prev, tuple(terms))
            # Eval runs on the host, one space at a time; any fixed per-space salt will do.
            salt = hashlib.sha256(f"eval/{page.space_id}".encode()).hexdigest()[:32]
            note = read_slide(model, res, slide, blobs, ctx, cache_salt=salt)
            body = note.body.model_dump(mode="json") if note.body else None
            if note.body:
                prev = note.body.point
                terms.extend(c for c in note.body.concepts if c not in terms)
            r = score(slide.slide_ref, GOLD[slide.slide_ref], body)
            r["note"] = body
            results.append(r)
            print(
                f"{r['slide']:<14} {r['status']:<6} types={'ok' if r['types_ok'] else 'NO':<3}"
                f" reads={r['reads_ok']}/{r['reads_total']} nff={r['nff_ok']}/{r['nff_total']}"
                f"{'' if r['must_not_contain_ok'] else '  LEAK'}  {'; '.join(r['misses'])}",
                flush=True,
            )
    summary = {
        "slides": len(results),
        "types_ok": sum(r["types_ok"] for r in results),
        "reads_ok": sum(r["reads_ok"] for r in results),
        "reads_total": sum(r["reads_total"] for r in results),
        "nff_ok": sum(r["nff_ok"] for r in results),
        "nff_total": sum(r["nff_total"] for r in results),
        "failed": sum(r["status"] == "failed" for r in results),
        "must_not_contain_ok": all(r["must_not_contain_ok"] for r in results),
        "seconds": round(time.monotonic() - start),
    }
    print(json.dumps(summary))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        json.dumps({"summary": summary, "slides": results}, ensure_ascii=False, indent=1)
    )


if __name__ == "__main__":
    main()
