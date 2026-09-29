"""Load the synthetic test set into SourcePage form, as sync would have stored it."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from kc_ids import AttachmentId, PageId, Revision
from kc_labels import Labels, SpaceId
from kc_store.space import SourcePage
from kc_store.testing import fake_attachment_id

TESTSET = Path(__file__).parents[3] / "tests/fixtures/synthetic_litho_testset"
MANIFEST = json.loads((TESTSET / "manifest.json").read_text())
GOLD = json.loads((TESTSET / "gold/slides.json").read_text())


def load_page(page_id: str) -> tuple[SourcePage, str, dict[AttachmentId, bytes]]:
    meta = next(p for p in MANIFEST["pages"] if p["page_id"] == page_id)
    space = SpaceId(meta["space_id"])
    blobs: dict[AttachmentId, bytes] = {}
    amap: list[tuple[str, AttachmentId]] = []
    for ref in meta["attachments"]:
        data = (TESTSET / "spaces" / space / ref).read_bytes()
        att = fake_attachment_id(data)
        blobs[att] = data
        amap.append((re.sub(r"^attachments/", "", ref), att))
    page = SourcePage(
        page_id=PageId(page_id),
        space_id=space,
        revision=Revision(1),
        updated_date=datetime.fromisoformat(meta["updated_at"]).astimezone(UTC),
        content_hash="0" * 64,
        title=meta["title"],
        parent_id=meta["parent_id"],
        attachment_ids=tuple(sorted(blobs)),
        labels=Labels.of([space]),
        attachment_map=tuple(amap),
    )
    return page, (TESTSET / meta["path"]).read_text(), blobs
