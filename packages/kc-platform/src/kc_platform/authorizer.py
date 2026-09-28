"""can_read against the platform with a short TTL cache; fails closed (ADR-002)."""

import time
from collections.abc import Callable

from kc_labels import Labels, ReadableSpaces, can_read
from kc_platform.acl import PlatformAcl
from kc_platform.identity import Principal

__all__ = ["DEFAULT_TTL_SECONDS", "Authorizer"]

DEFAULT_TTL_SECONDS = 300.0
_NOTHING = ReadableSpaces.of([])


class Authorizer:
    def __init__(
        self,
        acl: PlatformAcl,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._acl = acl
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, ReadableSpaces]] = {}

    def readable_spaces(self, principal: Principal) -> ReadableSpaces:
        """The user's readable spaces, or none at all if the platform cannot answer."""
        now = self._clock()
        hit = self._cache.get(principal.user_id)
        if hit is not None and now < hit[0]:
            return hit[1]
        try:
            readable = self._acl.readable_spaces(principal)
        except Exception:
            # Fail closed; never fall back to an expired entry, never cache the failure.
            self._cache.pop(principal.user_id, None)
            return _NOTHING
        self._cache[principal.user_id] = (now + self._ttl, readable)
        return readable

    def can_read(self, principal: Principal, labels: Labels) -> bool:
        return can_read(self.readable_spaces(principal), labels)

    def invalidate(self, user_id: str) -> None:
        self._cache.pop(user_id, None)
