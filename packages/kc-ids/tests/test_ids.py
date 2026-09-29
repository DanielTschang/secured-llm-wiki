import pytest

from kc_ids import AttachmentId, PageId, Revision, attachment_id_from_digest


@pytest.mark.parametrize("raw", ["opc_o1", "cd_d1", "12345", "Page-7"])
def test_page_ids(raw: str) -> None:
    assert PageId(raw) == raw


@pytest.mark.parametrize("raw", ["", "a b", "KESTREL 設定", "x" * 65, "../x", "a/b", "a\n"])
def test_invalid_page_ids(raw: str) -> None:
    with pytest.raises(ValueError, match="page id") as exc:
        PageId(raw)
    assert "KESTREL" not in str(exc.value)


def test_attachment_id_from_keyed_digest_not_filename() -> None:
    a = attachment_id_from_digest("0123456789abcdef" * 4)
    assert a == "att_0123456789abcdef"
    with pytest.raises(ValueError):
        AttachmentId("o2_rules_2025.png")
    with pytest.raises(ValueError):
        attachment_id_from_digest("not hex at all!!")


@pytest.mark.parametrize("raw", [0, -1, True, 1.0, "1"])
def test_invalid_revisions(raw: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        Revision(raw)  # type: ignore[arg-type]


def test_revision_ok() -> None:
    assert Revision(3) == 3
