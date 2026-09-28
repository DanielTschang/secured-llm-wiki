from datetime import UTC, datetime

import httpx
import pytest

from kc_platform.acl import PlatformUnavailable
from kc_platform.pages import PagesClient


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, base_url="http://platform")


def test_get_pages_parses_metadata() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/spaces/sp_opc/pages"
        assert req.headers["authorization"] == "Bearer svc"
        return httpx.Response(
            200,
            json={
                "pages": [
                    {
                        "page_id": "opc_o1",
                        "space_id": "sp_opc",
                        "title": "t",
                        "parent_id": "opc_courses",
                        "updated_date": "2023-09-10T09:00:00Z",
                    }
                ]
            },
        )

    pages = PagesClient(_client(httpx.MockTransport(handler)), service_token="svc").get_pages(
        "sp_opc"
    )
    assert len(pages) == 1
    p = pages[0]
    assert (p.page_id, p.space_id, p.parent_id) == ("opc_o1", "sp_opc", "opc_courses")
    assert p.updated_date == datetime(2023, 9, 10, 9, tzinfo=UTC)


def test_page_meta_repr_hides_title() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "pages": [
                    {
                        "page_id": "opc_o1",
                        "space_id": "sp_opc",
                        "title": "KESTREL secret title",
                        "parent_id": None,
                        "updated_date": "2023-09-10T09:00:00Z",
                    }
                ]
            },
        )

    [p] = PagesClient(_client(httpx.MockTransport(handler)), service_token="svc").get_pages(
        "sp_opc"
    )
    assert "KESTREL" not in repr(p)
    assert p.title == "KESTREL secret title"


def test_get_pages_rejects_page_from_other_space() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "pages": [
                    {
                        "page_id": "cd_d1",
                        "space_id": "sp_cd",
                        "title": "t",
                        "parent_id": None,
                        "updated_date": "2025-10-01T09:00:00Z",
                    }
                ]
            },
        )

    with pytest.raises(PlatformUnavailable):
        PagesClient(_client(httpx.MockTransport(handler)), service_token="svc").get_pages("sp_opc")


def test_get_pages_error_raises_unavailable() -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(503)))
    with pytest.raises(PlatformUnavailable):
        PagesClient(client, service_token="svc").get_pages("sp_opc")


def test_get_pages_validates_space_id_before_request() -> None:
    called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"pages": []})

    with pytest.raises(ValueError, match="space id"):
        PagesClient(_client(httpx.MockTransport(handler)), service_token="svc").get_pages("../x")
    assert not called
