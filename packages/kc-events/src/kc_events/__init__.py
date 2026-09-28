"""Queue messages carry IDs only: (space_id, page_id, revision)."""

import json
from dataclasses import dataclass
from typing import Any, cast

from kc_ids import PageId, Revision
from kc_labels import SpaceId

__all__ = ["PageEvent", "subject_for"]

SUBJECT_PREFIX = "kc.page."
_FIELDS = {"space_id", "page_id", "revision"}


def subject_for(space_id: str) -> str:
    return SUBJECT_PREFIX + SpaceId(space_id)


@dataclass(frozen=True, slots=True)
class PageEvent:
    space_id: SpaceId
    page_id: PageId
    revision: Revision

    @classmethod
    def of(cls, *, space_id: str, page_id: str, revision: int) -> PageEvent:
        return cls(SpaceId(space_id), PageId(page_id), Revision(revision))

    @property
    def subject(self) -> str:
        return subject_for(self.space_id)

    def to_bytes(self) -> bytes:
        return json.dumps(
            {"space_id": self.space_id, "page_id": self.page_id, "revision": int(self.revision)}
        ).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> PageEvent:
        try:
            raw: object = json.loads(data)
        except ValueError as e:
            raise ValueError("invalid event") from e
        if not isinstance(raw, dict):
            raise ValueError("invalid event")
        raw = cast(dict[str, Any], raw)
        if set(raw) != _FIELDS:
            raise ValueError("invalid event")
        try:
            return cls.of(
                space_id=raw["space_id"], page_id=raw["page_id"], revision=raw["revision"]
            )
        except (TypeError, ValueError) as e:
            raise ValueError("invalid event") from e

    @classmethod
    def from_message(cls, *, subject: str, data: bytes) -> PageEvent:
        """Parse and check the event belongs to the subject (space) it arrived on."""
        event = cls.from_bytes(data)
        if event.subject != subject:
            raise ValueError("event does not match subject")
        return event
