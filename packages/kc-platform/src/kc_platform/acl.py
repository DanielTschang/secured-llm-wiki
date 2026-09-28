"""Readable spaces as reported by the platform at read time (ADR-002)."""

from typing import Protocol

import httpx

from kc_labels import ReadableSpaces
from kc_platform.identity import Principal

__all__ = ["HttpPlatformAcl", "PlatformAcl", "PlatformUnavailable"]


class PlatformUnavailable(Exception):
    """The platform could not give a trustworthy answer. Callers must fail closed."""


class PlatformAcl(Protocol):
    def readable_spaces(self, principal: Principal) -> ReadableSpaces: ...


class HttpPlatformAcl:
    """Asks the platform with the user's own token; nothing from the request is forwarded."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def readable_spaces(self, principal: Principal) -> ReadableSpaces:
        try:
            resp = self._client.get(
                "/api/me/spaces",
                headers={"Authorization": f"Bearer {principal.bearer_token}"},
            )
            resp.raise_for_status()
            spaces = resp.json()["spaces"]
            if not isinstance(spaces, list):
                raise TypeError
            return ReadableSpaces.of(spaces)  # pyright: ignore[reportUnknownArgumentType]
        except Exception as e:
            raise PlatformUnavailable from e
