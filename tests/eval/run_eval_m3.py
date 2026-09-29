"""M3 evaluation (make eval): concept alignment and OPC's current MEEF threshold.

1. Alignment: each gold slide's slide-note concepts, aligned by ingest step 4, against
   the gold concepts (precision / recall).
2. Version judgement (ADR-008): ingest the OPC course in both orders (o1->o2, o2->o1);
   the MEEF concept page must state 2.5 as current and mention 3.0 only under 版本演變.

Runs on the host against the model server (Ollama), in-memory storage, synthetic data.
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

from ingest_worker.pipeline import ingest_page
from ingest_worker.steps.align import align_concepts
from ingest_worker.steps.parse import parse_page
from ingest_worker.steps.read import CourseContext, Resources, read_slide
from kc_graph import SpaceGraph
from kc_graph.testing import MemoryGraph
from kc_ids import PageId, Revision
from kc_labels import SpaceId
from kc_models import OpenAICompatibleBackend
from kc_okf import parse
from kc_store.context import SpaceContext
from kc_store.index import MemoryIndex
from kc_store.space import SpaceStore
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

REPO = Path(__file__).parents[2]
OUT = Path(__file__).parent / "results/latest_m3.json"
HOSTS = frozenset({"127.0.0.1", "localhost"})


def model(name: str) -> OpenAICompatibleBackend:
    url = os.environ.get("KC_EVAL_MODEL_URL", "http://127.0.0.1:11434/v1")
    return OpenAICompatibleBackend(url, name, allowed_hosts=HOSTS)


def salt(space: str) -> str:
    return hashlib.sha256(f"eval/{space}".encode()).hexdigest()[:32]


def eval_alignment(vlm: OpenAICompatibleBackend, res: Resources) -> dict[str, Any]:
    tp = fp = fn = 0
    rows: list[dict[str, Any]] = []
    for meta in MANIFEST["pages"]:
        page, md, blobs = load_page(meta["page_id"])
        store = SpaceStore(
            page.space_id, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs()
        )
        for slide in parse_page(page, md):
            if slide.slide_ref not in GOLD:
                continue
            note = read_slide(
                vlm, res, slide, blobs, CourseContext(page.title, None, ()),
                cache_salt=salt(page.space_id),
            )  # fmt: skip
            names = list(note.body.concepts) if note.body else []
            mapped = align_concepts(
                vlm, res, names, local_concept=store.local_concept, cache_salt=salt(page.space_id)
            )
            got = {c for c in mapped.values() if c.startswith("concept:")}
            want = set(GOLD[slide.slide_ref]["concepts"])
            tp, fp, fn = tp + len(got & want), fp + len(got - want), fn + len(want - got)
            rows.append({"slide": slide.slide_ref, "want": sorted(want), "got": sorted(got)})
            print(
                f"{slide.slide_ref:<14} missing={sorted(want - got)} extra={sorted(got - want)}",
                flush=True,
            )
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "slides": rows}


def _sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = ""
    for line in body.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            continue
        if not line.startswith("[^"):
            out[current] = out.get(current, "") + line + "\n"
    return out


def eval_version(
    vlm: OpenAICompatibleBackend,
    embedder: OpenAICompatibleBackend,
    res: Resources,
    order: list[str],
) -> dict[str, Any]:
    space = SpaceId("sp_opc")
    store = SpaceStore(space, keys=FakeKeyService(), docs=MemoryDocs(), blobs=MemoryBlobs())
    ctx = SpaceContext(space, store, SpaceGraph(space, MemoryGraph()), MemoryIndex(space))
    for pid in order:
        page, md, blobs = load_page(pid)
        store.put_source_page(page, md, blobs)
        ingest_page(
            ctx, vlm, embedder, res, PageId(pid), Revision(1), model_id=vlm.model
        )  # fmt: skip
    entry = store.wiki_page_by_key("concept:meef")
    if entry is None:
        return {"order": order, "ok": False, "reason": "no MEEF page"}
    doc = parse(store.wiki_get(entry["path"])[0])  # type: ignore[index]
    sections = _sections(doc.body)
    evolution = "".join(v for k, v in sections.items() if "版本演變" in k)
    current = "".join(v for k, v in sections.items() if "版本演變" not in k)
    has = lambda text, value: re.search(rf"(?<![\d.]){re.escape(value)}(?![\d.])", text) is not None  # noqa: E731
    ok = has(current, "2.5") and not has(current, "3.0") and has(evolution, "3.0")
    return {
        "order": order,
        "ok": ok,
        "status": doc.frontmatter.get("status"),
        "current_has_2_5": has(current, "2.5"),
        "current_has_3_0": has(current, "3.0"),
        "evolution_has_3_0": has(evolution, "3.0"),
        "pages": len(store.wiki_pages()),
        "body": doc.body,
    }


def main() -> None:
    res = Resources.load(REPO / "schema")
    vlm = model(os.environ.get("KC_EVAL_MODEL", "qwen2.5vl:7b"))
    embedder = model(os.environ.get("KC_EVAL_EMBED_MODEL", "bge-m3"))
    start = time.monotonic()
    alignment = eval_alignment(vlm, res)
    print(json.dumps({k: alignment[k] for k in ("precision", "recall")}), flush=True)
    versions = []
    for order in (["opc_o1", "opc_o2"], ["opc_o2", "opc_o1"]):
        v = eval_version(vlm, embedder, res, order)
        versions.append(v)
        print(json.dumps({k: v[k] for k in v if k != "body"}, ensure_ascii=False), flush=True)
    summary = {
        "alignment_precision": alignment["precision"],
        "alignment_recall": alignment["recall"],
        "meef_current_2_5_both_orders": all(v["ok"] for v in versions),
        "seconds": round(time.monotonic() - start),
    }
    print(json.dumps(summary))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"summary": summary, "alignment": alignment, "versions": versions},
            ensure_ascii=False,
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
