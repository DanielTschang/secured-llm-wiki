"""SpaceStore: one space's source pages, raw content and ingest bookkeeping.

Every write is fenced by revision and checked to carry exactly this space's labels.
Object keys and document IDs are IDs only. Content is stored in plaintext for now
(ADR-012); cross-space isolation comes from each space's own credentials.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kc_ids import AttachmentId, PageId, Revision, attachment_id_from_digest
from kc_labels import Labels, SpaceId
from kc_okf import is_bundle_path, new_ulid
from kc_store.backends import BlobStore, DocStore
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
        if not self._blobs.put_if_absent(self._md_key(page.page_id, rev), markdown.encode()):
            raise RevisionTaken
        for att, data in attachments.items():
            if not self._blobs.put_if_absent(self._att_key(page.page_id, rev, att), data):
                raise RevisionTaken

        doc: dict[str, Any] = {
            "space_id": str(self.space_id),
            "revision": int(rev),
            "updated_date": page.updated_date,
            "content_hash": page.content_hash,
            "title": page.title,
            "parent_id": page.parent_id,
            "attachment_ids": [str(a) for a in page.attachment_ids],
            "attachment_map": [[n, str(a)] for n, a in page.attachment_map],
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
        updated: datetime = doc["updated_date"]
        return SourcePage(
            page_id=page_id,
            space_id=SpaceId(doc["space_id"]),
            revision=rev,
            updated_date=updated if updated.tzinfo else updated.replace(tzinfo=UTC),
            content_hash=doc["content_hash"],
            title=str(doc["title"]),
            parent_id=doc["parent_id"],
            attachment_ids=tuple(AttachmentId(a) for a in doc["attachment_ids"]),
            labels=labels,
            attachment_map=tuple((str(n), AttachmentId(a)) for n, a in doc["attachment_map"]),
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
        return blob.decode()

    def read_attachment(self, page_id: PageId, revision: Revision, att: AttachmentId) -> bytes:
        blob = self._blobs.get(self._att_key(page_id, revision, att))
        if blob is None:
            raise KeyError("no such object")
        return blob

    # --- ingest bookkeeping ------------------------------------------------------

    def record_ingest_run(self, page_id: PageId, revision: Revision, status: str) -> None:
        doc = {"revision": int(revision), "status": status, "labels": sorted(self.labels.spaces)}
        if not self._docs.replace_if_revision_below("ingest_runs", page_id, doc):
            raise StaleWrite

    def ingest_run(self, page_id: PageId) -> tuple[int, str] | None:
        doc = self._docs.get("ingest_runs", page_id)
        return None if doc is None else (int(doc["revision"]), str(doc["status"]))

    # --- slide notes (ingest step 2) -------------------------------------------------

    def put_slide_note(
        self,
        slide_ref: str,
        page_id: PageId,
        revision: Revision,
        labels: Labels,
        status: str,
        body: dict[str, Any] | None,
    ) -> None:
        self.check_labels(labels)
        doc = {
            "page_id": str(page_id),
            "revision": int(revision),
            "status": status,
            "body": body,
            "labels": sorted(labels.spaces),
        }
        if not self._docs.replace_if_revision_below("slide_notes", slide_ref, doc):
            raise StaleWrite

    def slide_notes(self, page_id: PageId, revision: Revision) -> list[dict[str, Any]]:
        """Notes of the page's slides at `revision`, in slide order. Notes left over from an
        older revision with more slides are not returned."""
        out: list[dict[str, Any]] = []
        n = 1
        while (doc := self._docs.get("slide_notes", f"{page_id}#{n}")) is not None:
            self.check_labels(Labels.of(doc["labels"]))
            if int(doc["revision"]) == int(revision):
                fields = {k: v for k, v in doc.items() if k != "_id"}
                out.append({"slide_ref": f"{page_id}#{n}", **fields})
            n += 1
        return out

    # --- course pages --------------------------------------------------------------

    def list_source_pages(self, parent_id: str) -> list[SourcePage]:
        """This space's pages of one course (same platform parent), in page-id order."""
        docs = self._docs.find("source_pages", {"parent_id": parent_id})
        return [p for d in docs if (p := self.get_source_page(PageId(d["_id"]))) is not None]

    # --- wiki bundle (OKF v0.2, ADR-010): wiki/<path> in the space's raw bucket ----------

    def wiki_get(self, path: str) -> tuple[str, str] | None:
        _check_bundle_path(path)
        got = self._blobs.get_with_etag(f"wiki/{path}")
        return None if got is None else (got[0].decode(), got[1])

    def wiki_put(self, path: str, text: str, etag: str | None, labels: Labels) -> None:
        """Create (etag None) or replace the object with this exact ETag; else StaleWrite."""
        _check_bundle_path(path)
        self.check_labels(labels)
        if not self._blobs.put_if_match(f"wiki/{path}", text.encode(), etag):
            raise StaleWrite

    def upsert_wiki_page(self, path: str, meta: dict[str, Any], labels: Labels) -> None:
        """Index entry for a bundle page (rebuildable from the bundle)."""
        _check_bundle_path(path)
        self.check_labels(labels)
        self._docs.upsert("wiki_pages", path, {**meta, "labels": sorted(labels.spaces)})

    def wiki_pages(self) -> list[dict[str, Any]]:
        return [{"path": d["_id"], **d} for d in self._docs.find("wiki_pages", {})]

    def wiki_page_for_concept(self, concept_id: str) -> dict[str, Any] | None:
        found = self._docs.find("wiki_pages", {"concept_id": concept_id})
        return {"path": found[0]["_id"], **found[0]} if found else None

    def wiki_page_by_key(self, key: str) -> dict[str, Any] | None:
        """The page for a subject key (a concept ID, or entity/course/synthesis keys)."""
        found = self._docs.find("wiki_pages", {"key": key})
        return {"path": found[0]["_id"], **found[0]} if found else None

    # --- claims (ingest step 3) -----------------------------------------------------

    def replace_course_claims(
        self, course_id: str, claims: list[dict[str, Any]], labels: Labels
    ) -> None:
        """A course's claims are rebuilt by each course integration (same-space ingest is
        sequential, ADR-006); IDs are positional within the course."""
        self.check_labels(labels)
        prefix = self.keyed_digest(course_id.encode())[:16]
        for old in self._docs.find("claims", {"course_key": prefix}):
            self._docs.delete("claims", old["_id"])
        for n, c in enumerate(claims):
            doc = {**c, "course_key": prefix, "labels": sorted(labels.spaces)}
            self._docs.upsert("claims", f"clm_{prefix}_{n:04d}", doc)

    def course_claims(self, course_id: str) -> list[dict[str, Any]]:
        prefix = self.keyed_digest(course_id.encode())[:16]
        return [self._checked(d) for d in self._docs.find("claims", {"course_key": prefix})]

    def claims_for_concept(self, concept_id: str) -> list[dict[str, Any]]:
        return [self._checked(d) for d in self._docs.find("claims", {"concept_ids": concept_id})]

    def _checked(self, doc: dict[str, Any]) -> dict[str, Any]:
        self.check_labels(Labels.of(doc["labels"]))
        return doc

    # --- local concepts (ingest step 4) -------------------------------------------

    def local_concept(self, name: str) -> str:
        """The space's local concept for a name that is not in the canonical glossary;
        reused for the same normalised name. The ID carries no name (invariant 7)."""
        key = "lc_" + self.keyed_digest(_normalise(name).encode())[:24]
        doc = self._docs.get("local_concepts", key)
        if doc is not None:
            return str(doc["concept_id"])
        concept_id = f"local:{self.space_id}:{new_ulid()}"
        self._docs.upsert(
            "local_concepts",
            key,
            {"concept_id": concept_id, "name": name.strip(), "labels": sorted(self.labels.spaces)},
        )
        return concept_id

    def local_concept_name(self, concept_id: str) -> str | None:
        found = self._docs.find("local_concepts", {"concept_id": concept_id})
        return str(found[0]["name"]) if found else None

    # --- quarantine (page-level restrictions, ADR-001) -------------------------------

    def quarantine(self, page_id: PageId) -> None:
        self._docs.upsert("quarantine", page_id, {})

    def is_quarantined(self, page_id: PageId) -> bool:
        return self._docs.get("quarantine", page_id) is not None


def _check_bundle_path(path: str) -> None:
    if not is_bundle_path(path):
        raise ValueError("not a bundle path")


def _normalise(name: str) -> str:
    import unicodedata

    return " ".join(unicodedata.normalize("NFKC", name).lower().split())
