from datetime import UTC, datetime

import pytest

from kc_ids import PageId, Revision, attachment_id_for
from kc_labels import Labels, SpaceId
from kc_store.space import SourcePage, SpaceStore, StaleWrite, WrongSpace
from kc_store.testing import FakeKeyService, MemoryBlobs, MemoryDocs

OPC = SpaceId("sp_opc")
CD = SpaceId("sp_cd")
T1 = datetime(2025, 8, 20, 9, tzinfo=UTC)
T2 = datetime(2025, 9, 1, 9, tzinfo=UTC)
PNG = b"\x89PNG fake"


def page(rev: int = 1, space: SpaceId = OPC, updated: datetime = T1) -> SourcePage:
    return SourcePage(
        page_id=PageId("opc_o2"),
        space_id=space,
        revision=Revision(rev),
        updated_date=updated,
        content_hash="h" * 64,
        title="OPC 實務入門（2025 版）",
        parent_id="opc_courses",
        attachment_ids=(attachment_id_for(PNG),),
        labels=Labels.of([space]),
    )


@pytest.fixture
def parts() -> tuple[FakeKeyService, MemoryDocs, MemoryBlobs]:
    return FakeKeyService(), MemoryDocs(), MemoryBlobs()


def store(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs], space: SpaceId = OPC
) -> SpaceStore:
    keys, docs, blobs = parts
    return SpaceStore(space, keys=keys, docs=docs, blobs=blobs)


def test_roundtrip(parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs]) -> None:
    s = store(parts)
    s.put_source_page(page(), "# md KESTREL", {attachment_id_for(PNG): PNG})
    got = s.get_source_page(PageId("opc_o2"))
    assert got == page()
    assert s.read_markdown(PageId("opc_o2"), Revision(1)) == "# md KESTREL"
    assert s.read_attachment(PageId("opc_o2"), Revision(1), attachment_id_for(PNG)) == PNG


def test_content_is_encrypted_at_rest(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs],
) -> None:
    _, docs, blobs = parts
    store(parts).put_source_page(page(), "# md KESTREL", {attachment_id_for(PNG): PNG})
    assert "OPC 實務" not in repr(docs.dump())
    assert all(b"KESTREL" not in b and PNG not in b for b in blobs.dump().values())
    # Object keys are IDs only.
    assert all("opc_o2" in k and "OPC" not in k for k in blobs.dump())


@pytest.mark.parametrize("rev", [1, 2])
def test_stale_or_equal_revision_rejected(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs], rev: int
) -> None:
    s = store(parts)
    s.put_source_page(page(2), "v2", {})
    with pytest.raises(StaleWrite):
        s.put_source_page(page(rev), "old", {})
    assert s.read_markdown(PageId("opc_o2"), Revision(2)) == "v2"


def test_blob_for_revision_cannot_be_overwritten(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs],
) -> None:
    s = store(parts)
    s.put_source_page(page(1), "v1", {})
    with pytest.raises(StaleWrite):
        s.put_source_page(page(1), "tampered", {})
    assert s.read_markdown(PageId("opc_o2"), Revision(1)) == "v1"


def test_newer_revision_accepted(parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs]) -> None:
    s = store(parts)
    s.put_source_page(page(1), "v1", {})
    s.put_source_page(page(2), "v2", {})
    got = s.get_source_page(PageId("opc_o2"))
    assert got is not None and got.revision == 2


def test_touch_updated_date_only_for_current_revision(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs],
) -> None:
    s = store(parts)
    s.put_source_page(page(2), "v2", {})
    s.touch_updated_date(PageId("opc_o2"), Revision(2), T2)
    got = s.get_source_page(PageId("opc_o2"))
    assert got is not None and got.updated_date == T2 and got.revision == 2
    with pytest.raises(StaleWrite):
        s.touch_updated_date(PageId("opc_o2"), Revision(1), T2)


@pytest.mark.parametrize("labels", [["sp_cd"], ["sp_opc", "sp_cd"]])
def test_writes_with_foreign_labels_abort(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs], labels: list[str]
) -> None:
    bad = page()
    object.__setattr__(bad, "labels", Labels.of(labels))
    with pytest.raises(WrongSpace):
        store(parts).put_source_page(bad, "x", {})


def test_page_from_other_space_rejected(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs],
) -> None:
    with pytest.raises(WrongSpace):
        store(parts).put_source_page(page(space=CD), "x", {})


def test_undeclared_attachment_rejected(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs],
) -> None:
    with pytest.raises(ValueError, match="attachment"):
        store(parts).put_source_page(page(), "x", {attachment_id_for(b"other"): b"other"})


def test_other_space_keys_cannot_read(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs],
) -> None:
    keys, docs, blobs = parts
    SpaceStore(OPC, keys=keys, docs=docs, blobs=blobs).put_source_page(page(), "secret", {})
    thief = SpaceStore(OPC, keys=keys.restricted_to(CD), docs=docs, blobs=blobs)
    with pytest.raises(Exception, match="decrypt"):
        thief.read_markdown(PageId("opc_o2"), Revision(1))


def test_ingest_run_fenced(parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs]) -> None:
    s = store(parts)
    s.record_ingest_run(PageId("opc_o2"), Revision(2), "done")
    with pytest.raises(StaleWrite):
        s.record_ingest_run(PageId("opc_o2"), Revision(1), "done")
    assert s.ingest_run(PageId("opc_o2")) == (2, "done")


def test_quarantine_records_ids_only(parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs]) -> None:
    _, docs, _ = parts
    s = store(parts)
    s.quarantine(PageId("opc_secret"))
    assert s.is_quarantined(PageId("opc_secret"))
    assert docs.dump()["quarantine"] == [{"_id": "opc_secret"}]


def test_attachment_names_stored_encrypted(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs],
) -> None:
    from dataclasses import replace

    _, docs, _ = parts
    att = attachment_id_for(PNG)
    p = replace(page(), attachment_map=(("o2_rules_2025.png", att),))
    store(parts).put_source_page(p, "x", {att: PNG})
    got = store(parts).get_source_page(PageId("opc_o2"))
    assert got is not None and got.attachment_map == (("o2_rules_2025.png", att),)
    assert "o2_rules" not in repr(docs.dump())


def test_attachment_map_must_reference_declared_ids(
    parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs],
) -> None:
    from dataclasses import replace

    p = replace(page(), attachment_map=(("x.png", attachment_id_for(b"undeclared")),))
    with pytest.raises(ValueError, match="attachment"):
        store(parts).put_source_page(p, "x", {})


def test_publish_tracking(parts: tuple[FakeKeyService, MemoryDocs, MemoryBlobs]) -> None:
    s = store(parts)
    s.put_source_page(page(1), "v1", {})
    assert s.unpublished_revision(PageId("opc_o2")) == 1
    s.mark_published(PageId("opc_o2"), Revision(1))
    assert s.unpublished_revision(PageId("opc_o2")) is None
    s.put_source_page(page(2), "v2", {})
    assert s.unpublished_revision(PageId("opc_o2")) == 2
    with pytest.raises(StaleWrite):
        s.mark_published(PageId("opc_o2"), Revision(1))
