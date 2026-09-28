"""Labels are sets of source space IDs (ADR-001); can_read is a subset check (ADR-002).

This package is deliberately tiny and dependency-free. It never fetches permissions;
callers obtain ReadableSpaces from the platform (kc-platform) at read time.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["Labels", "ReadableSpaces", "SpaceId", "can_read", "derive"]

_SPACE_ID = re.compile(r"sp_[a-z0-9_]+")


class SpaceId(str):
    """A platform space ID. IDs never contain names or content (invariant 7)."""

    __slots__ = ()

    def __new__(cls, raw: str) -> SpaceId:
        if not isinstance(raw, str) or _SPACE_ID.fullmatch(raw) is None:  # pyright: ignore[reportUnnecessaryIsInstance]
            # Do not echo the input: exceptions can reach logs.
            raise ValueError("invalid space id")
        return super().__new__(cls, raw)


def _space_set(raw: Iterable[str]) -> frozenset[SpaceId]:
    if isinstance(raw, str):
        raise TypeError("expected an iterable of space ids, not a single string")
    return frozenset(SpaceId(s) for s in raw)


@dataclass(frozen=True, slots=True)
class Labels:
    """Non-empty set of source space IDs. Empty labels would be readable by anyone, so they
    indicate a bug and are rejected at construction."""

    spaces: frozenset[SpaceId]

    def __post_init__(self) -> None:
        if not self.spaces:
            raise ValueError("labels must not be empty")
        for s in self.spaces:
            if not isinstance(s, SpaceId):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise TypeError("labels must contain SpaceId values")

    @classmethod
    def of(cls, spaces: Iterable[str]) -> Labels:
        return cls(_space_set(spaces))


@dataclass(frozen=True, slots=True)
class ReadableSpaces:
    """The spaces the platform reports a user can read, as of one lookup. May be empty."""

    spaces: frozenset[SpaceId]

    @classmethod
    def of(cls, spaces: Iterable[str]) -> ReadableSpaces:
        return cls(_space_set(spaces))


def derive(*inputs: Labels) -> Labels:
    """Labels of a derived artifact: the union of all its inputs' labels."""
    if not inputs:
        raise ValueError("derive needs at least one input")
    for i in inputs:
        if not isinstance(i, Labels):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("derive takes Labels")
    return Labels(frozenset[SpaceId]().union(*(i.spaces for i in inputs)))


def can_read(readable: ReadableSpaces, labels: Labels) -> bool:
    """True iff every source space of the artifact is readable."""
    if not isinstance(readable, ReadableSpaces) or not isinstance(labels, Labels):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("can_read takes ReadableSpaces and Labels")
    return labels.spaces <= readable.spaces
