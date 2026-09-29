"""Platform page API for sync: page metadata per space, raw page content, attachments.

Sync always fetches the raw form. The rendered form may already contain other spaces'
content through expanded includes.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from kc_labels import SpaceId
from kc_platform.acl import PlatformUnavailable

__all__ = ["ClientCredentials", "PageMeta", "PagesClient", "RawPage"]

_ATTACHMENT_REF = re.compile(r"attachments/([A-Za-z0-9_.-]+)")


@dataclass(frozen=True, slots=True)
class PageMeta:
    page_id: str
    space_id: SpaceId
    parent_id: str | None
    updated_date: datetime
    title: str = field(repr=False)  # content: never in logs (invariant 7)
    restricted: bool = False


@dataclass(frozen=True, slots=True)
class RawPage:
    page_id: str
    space_id: SpaceId
    updated_date: datetime
    parent_id: str | None
    title: str = field(repr=False)
    markdown: str = field(repr=False)
    attachment_names: tuple[str, ...] = field(repr=False, default=())


class ClientCredentials:
    """Service token via OAuth client credentials, cached until shortly before expiry."""

    def __init__(
        self,
        client: httpx.Client,
        client_id: str,
        client_secret: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._id = client_id
        self._secret = client_secret
        self._clock = clock
        self._token: str | None = None
        self._expires = 0.0

    def __repr__(self) -> str:
        return f"ClientCredentials(client_id={self._id!r})"

    def __call__(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._expires - 60:
            return self._token
        try:
            resp = self._client.post(
                "/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._id,
                    "client_secret": self._secret,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            self._token = str(body["access_token"])
            self._expires = now + float(body.get("expires_in", 300))
        except Exception as e:
            raise PlatformUnavailable from e
        return self._token


class PagesClient:
    def __init__(self, client: httpx.Client, *, service_token: str | Callable[[], str]) -> None:
        self._client = client
        self._token = service_token if callable(service_token) else (lambda: service_token)

    def _get(self, path: str) -> httpx.Response:
        resp = self._client.get(path, headers={"Authorization": f"Bearer {self._token()}"})
        resp.raise_for_status()
        return resp

    def get_pages(self, space_id: str) -> list[PageMeta]:
        space = SpaceId(space_id)
        try:
            pages = [
                PageMeta(
                    page_id=str(p["page_id"]),
                    space_id=SpaceId(p["space_id"]),
                    parent_id=p["parent_id"],
                    updated_date=datetime.fromisoformat(p["updated_date"]),
                    title=str(p["title"]),
                    # Absent flag = restricted (fail closed, ADR-001).
                    restricted=bool(p.get("restricted", True)),
                )
                for p in self._get(f"/api/spaces/{space}/pages").json()["pages"]
            ]
        except Exception as e:
            raise PlatformUnavailable from e
        if any(p.space_id != space for p in pages):
            raise PlatformUnavailable
        return pages

    def get_page(self, page_id: str) -> RawPage:
        try:
            p = self._get(f"/api/pages/{page_id}").json()
            names: list[str] = []
            for ref in p["attachments"]:
                m = _ATTACHMENT_REF.fullmatch(str(ref))
                if m is None:
                    raise ValueError("bad attachment reference")
                names.append(m.group(1))
            return RawPage(
                page_id=str(p["page_id"]),
                space_id=SpaceId(p["space_id"]),
                updated_date=datetime.fromisoformat(p["updated_date"]),
                parent_id=p["parent_id"],
                title=str(p["title"]),
                markdown=str(p["markdown"]),
                attachment_names=tuple(names),
            )
        except Exception as e:
            raise PlatformUnavailable from e

    def get_attachment(self, page_id: str, name: str) -> bytes:
        try:
            return self._get(f"/api/pages/{page_id}/attachments/{name}").content
        except Exception as e:
            raise PlatformUnavailable from e
