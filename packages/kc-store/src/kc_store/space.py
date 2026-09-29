"""SpaceStore: one space's source pages, raw content and ingest bookkeeping.

Every write is fenced by revision and checked to carry exactly this space's labels.
Content (titles, markdown, attachments) is encrypted with the space's key; object keys
and document IDs are IDs only.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kc_ids import AttachmentId, PageId, Revision, attachment_id_from_digest
from kc_labels import Labels, SpaceId
from kc_store.backends import BlobStore, DocStore
from kc_store.envelope import ObjectRef, open_sealed, seal
from kc_store.keys import KeyService

__all__ = ["RevisionTaken", "SourcePage", "SpaceStore", "StaleWrite", "WrongSpace"]


class StaleWrite(Exception):
    """The store already holds this revision or a newer one."""


class RevisionTaken(StaleWrite):
    """Objects for this revision exist but the document is older: a previous write
    crashed half way. Retry with the next revision (objects are write-once)."""


class WrongSpace(Exception):
    """An artifact's space or labels do not match the store's space. Abort, never fix up."""


@dataclass(frozen=True, slots=True)
class SourcePage:
    page_id: PageId
    space_id: SpaceId
    revision: Revision
    updated_date: datetime
    content_hash: str
    title: str
    parent_id: str | None
    attachment_ids: tuple[AttachmentId, ...]
    labels: Labels
    # (filename as referenced in the markdown, attachment id). Filenames are content.
    attachment_map: tuple[tuple[str, AttachmentId], ...] = ()

    def __repr__(self) -> str:  # title is content
        return f"SourcePage(page_id={self.page_id!r}, revision={int(self.revision)})"


class SpaceStore:
    def __init__(self, space_id: SpaceId, *, keys: KeyService, docs: DocStore, blobs: BlobStore):
        self.space_id = space_id
        self.labels = Labels.of([space_id])
        self._keys = keys
        self._docs = docs
        self._blobs = blobs

    # --- helpers -------------------------------------------------------------

    def _ref(self, kind: str, object_id: str, revision: int) -> ObjectRef:
        return ObjectRef(self.space_id, kind, object_id, revision)

    def check_labels(self, labels: Labels) -> None:
        if labels != self.labels:
            raise WrongSpace

    @staticmethod
    def _md_key(page_id: PageId, revision: Revision) -> str:
        return f"pages/{page_id}/{int(revision)}/page.md"

    @staticmethod
    def _att_key(page_id: PageId, revision: Revision, att: AttachmentId) -> str:
        return f"pages/{page_id}/{int(revision)}/att/{att}"

    def _current_revision(self, collection: str, page_id: PageId) -> int:
        doc = self._docs.get(collection, page_id)
        return 0 if doc is None else int(doc["revision"])

    def keyed_digest(self, data: bytes) -> str:
        """HMAC with this space's key, as hex. Used for content hashes and attachment IDs so
        they cannot be matched against content from outside the space."""
        return self._keys.hmac(self.space_id, data).hex()

    def attachment_id(self, data: bytes) -> AttachmentId:
        return attachment_id_from_digest(self.keyed_digest(data))

    # --- source pages ----------------------------------------------------------

    def put_source_page(
        self, page: SourcePage, markdown: str, attachments: dict[AttachmentId, bytes]
    ) -> None:
        if page.space_id != self.space_id:
            raise WrongSpace
        self.check_labels(page.labels)
        if set(attachments) - set(page.attachment_ids):
            raise ValueError("attachment not declared on the page")
        if {a for _, a in page.attachment_map} - set(page.attachment_ids):
            raise ValueError("attachment map references an undeclared attachment")
        if self._current_revision("source_pages", page.page_id) >= page.revision:
            raise StaleWrite

        rev = page.revision
        md_ref = self._ref("page_md", page.page_id, rev)
        if not self._blobs.put_if_absent(
            self._md_key(page.page_id, rev), seal(self._keys, md_ref, markdown.encode())
        ):
            raise RevisionTaken
        for att, data in attachments.items():
            ref = self._ref("attachment", f"{page.page_id}/{att}", rev)
            if not self._blobs.put_if_absent(
                self._att_key(page.page_id, rev, att), seal(self._keys, ref, data)
            ):
                raise RevisionTaken

        doc: dict[str, Any] = {
            "space_id": str(self.space_id),
            "revision": int(rev),
            "updated_date": page.updated_date,
            "content_hash": page.content_hash,
            "title_enc": seal(
                self._keys, self._ref("page_title", page.page_id, rev), page.title.encode()
            ),
            "parent_id": page.parent_id,
            "attachment_ids": [str(a) for a in page.attachment_ids],
            "attachment_map_enc": seal(
                self._keys,
                self._ref("attachment_map", page.page_id, rev),
                json.dumps([[n, str(a)] for n, a in page.attachment_map]).encode(),
            ),
            "labels": sorted(page.labels.spaces),
            "published": False,
        }
        if not self._docs.replace_if_revision_below("source_pages", page.page_id, doc):
            raise StaleWrite

    def get_source_page(self, page_id: PageId) -> SourcePage | None:
        doc = self._docs.get("source_pages", page_id)
        if doc is None:
            return None
        labels = Labels.of(doc["labels"])
        self.check_labels(labels)
        rev = Revision(int(doc["revision"]))
        title = open_sealed(
            self._keys, self._ref("page_title", page_id, rev), bytes(doc["title_enc"])
        )
        amap = json.loads(
            open_sealed(
                self._keys,
                self._ref("attachment_map", page_id, rev),
                bytes(doc["attachment_map_enc"]),
            )
        )
        updated: datetime = doc["updated_date"]
        return SourcePage(
            page_id=page_id,
            space_id=SpaceId(doc["space_id"]),
            revision=rev,
            updated_date=updated if updated.tzinfo else updated.replace(tzinfo=UTC),
            content_hash=doc["content_hash"],
            title=title.decode(),
            parent_id=doc["parent_id"],
            attachment_ids=tuple(AttachmentId(a) for a in doc["attachment_ids"]),
            labels=labels,
            attachment_map=tuple((str(n), AttachmentId(a)) for n, a in amap),
        )

    def unpublished_revision(self, page_id: PageId) -> Revision | None:
        """The stored revision if its PageEvent has not been published yet."""
        doc = self._docs.get("source_pages", page_id)
        if doc is None or doc.get("published", False):
            return None
        return Revision(int(doc["revision"]))

    def mark_published(self, page_id: PageId, revision: Revision) -> None:
        if not self._docs.update_if_revision_equals(
            "source_pages", page_id, int(revision), {"published": True}
        ):
            raise StaleWrite

    def touch_updated_date(self, page_id: PageId, revision: Revision, updated: datetime) -> None:
        """Content unchanged but the platform bumped updated_date: record it, no new revision."""
        if not self._docs.update_if_revision_equals(
            "source_pages", page_id, int(revision), {"updated_date": updated}
        ):
            raise StaleWrite

    def read_markdown(self, page_id: PageId, revision: Revision) -> str:
        blob = self._blobs.get(self._md_key(page_id, revision))
        if blob is None:
            raise KeyError("no such object")
        return open_sealed(self._keys, self._ref("page_md", page_id, revision), blob).decode()

    def read_attachment(self, page_id: PageId, revision: Revision, att: AttachmentId) -> bytes:
        blob = self._blobs.get(self._att_key(page_id, revision, att))
        if blob is None:
            raise KeyError("no such object")
        return open_sealed(self._keys, self._ref("attachment", f"{page_id}/{att}", revision), blob)

    # --- ingest bookkeeping ------------------------------------------------------

    def record_ingest_run(self, page_id: PageId, revision: Revision, status: str) -> None:
        doc = {"revision": int(revision), "status": status, "labels": sorted(self.labels.spaces)}
        if not self._docs.replace_if_revision_below("ingest_runs", page_id, doc):
            raise StaleWrite

    def ingest_run(self, page_id: PageId) -> tuple[int, str] | None:
        doc = self._docs.get("ingest_runs", page_id)
        return None if doc is None else (int(doc["revision"]), str(doc["status"]))

    # --- quarantine (page-level restrictions, ADR-001) -------------------------------

    def quarantine(self, page_id: PageId) -> None:
        self._docs.upsert("quarantine", page_id, {})

    def is_quarantined(self, page_id: PageId) -> bool:
        return self._docs.get("quarantine", page_id) is not None
