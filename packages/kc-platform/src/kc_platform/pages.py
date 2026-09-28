"""getPages: page metadata per space, used by sync for change detection."""

from dataclasses import dataclass, field
from datetime import datetime

import httpx

from kc_labels import SpaceId
from kc_platform.acl import PlatformUnavailable

__all__ = ["PageMeta", "PagesClient"]


@dataclass(frozen=True, slots=True)
class PageMeta:
    page_id: str
    space_id: SpaceId
    parent_id: str | None
    updated_date: datetime
    title: str = field(repr=False)  # content: never in logs (invariant 7)


class PagesClient:
    def __init__(self, client: httpx.Client, *, service_token: str) -> None:
        self._client = client
        self._token = service_token

    def get_pages(self, space_id: str) -> list[PageMeta]:
        space = SpaceId(space_id)
        try:
            resp = self._client.get(
                f"/api/spaces/{space}/pages",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            resp.raise_for_status()
            pages = [
                PageMeta(
                    page_id=str(p["page_id"]),
                    space_id=SpaceId(p["space_id"]),
                    parent_id=p["parent_id"],
                    updated_date=datetime.fromisoformat(p["updated_date"]),
                    title=str(p["title"]),
                )
                for p in resp.json()["pages"]
            ]
        except Exception as e:
            raise PlatformUnavailable from e
        if any(p.space_id != space for p in pages):
            raise PlatformUnavailable
        return pages
