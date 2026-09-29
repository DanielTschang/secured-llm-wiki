"""Opaque IDs. They go into logs, metrics and queue messages, so they never carry names
or content (invariant 7). Validation errors never echo the input."""

import re

__all__ = ["AttachmentId", "PageId", "Revision", "attachment_id_from_digest"]

_PAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ATTACHMENT_ID = re.compile(r"att_[0-9a-f]{16}")


class PageId(str):
    """A platform page ID (assigned by the platform, opaque)."""

    __slots__ = ()

    def __new__(cls, raw: str) -> PageId:
        if not isinstance(raw, str) or _PAGE_ID.fullmatch(raw) is None:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("invalid page id")
        return super().__new__(cls, raw)


class AttachmentId(str):
    """Attachment ID from a per-space keyed digest; never the filename or a plain hash."""

    __slots__ = ()

    def __new__(cls, raw: str) -> AttachmentId:
        if not isinstance(raw, str) or _ATTACHMENT_ID.fullmatch(raw) is None:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("invalid attachment id")
        return super().__new__(cls, raw)


def attachment_id_from_digest(keyed_digest_hex: str) -> AttachmentId:
    """From a per-space keyed digest (HMAC) of the content. A plain content hash would let
    anyone holding a copy of an image confirm that a space contains it."""
    return AttachmentId("att_" + keyed_digest_hex[:16])


class Revision(int):
    """Per-page revision assigned by sync; monotonically increasing from 1 (fencing token)."""

    __slots__ = ()

    def __new__(cls, raw: int) -> Revision:
        if type(raw) is not int and not isinstance(raw, Revision):
            raise TypeError("revision must be an int")
        if raw < 1:
            raise ValueError("revision must be >= 1")
        return super().__new__(cls, raw)
