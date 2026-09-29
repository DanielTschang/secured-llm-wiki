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


# --- M1: raw page content, attachments, restricted flag, service tokens ----------------

from kc_platform.pages import ClientCredentials  # noqa: E402


def _page_json(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "page_id": "opc_o2",
        "space_id": "sp_opc",
        "title": "t",
        "parent_id": "opc_courses",
        "updated_date": "2025-08-20T09:00:00Z",
        "markdown": '# raw {{include page="cd_d1" section="4"}}',
        "attachments": ["attachments/o2_rules_2025.png"],
    }
    base.update(over)
    return base


def test_get_page_requests_raw_form_without_query() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, json=_page_json())

    page = PagesClient(_client(httpx.MockTransport(handler)), service_token="svc").get_page(
        "opc_o2"
    )
    assert seen == ["http://platform/api/pages/opc_o2"]  # never ?format=rendered
    assert page.markdown.startswith("# raw")
    assert page.attachment_names == ("o2_rules_2025.png",)
    assert "raw" not in repr(page) and "o2_rules" not in repr(page)


@pytest.mark.parametrize(
    "bad",
    [
        {"attachments": ["../../etc/passwd"]},
        {"attachments": ["attachments/a/b.png"]},
        {"space_id": "OPC"},
    ],
)
def test_get_page_rejects_malformed(bad: dict[str, object]) -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json=_page_json(**bad))))
    with pytest.raises(PlatformUnavailable):
        PagesClient(client, service_token="svc").get_page("opc_o2")


def test_get_attachment() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/pages/opc_o2/attachments/o2_rules_2025.png"
        return httpx.Response(200, content=b"\x89PNG")

    data = PagesClient(_client(httpx.MockTransport(handler)), service_token="svc").get_attachment(
        "opc_o2", "o2_rules_2025.png"
    )
    assert data == b"\x89PNG"


def test_restricted_flag_parsed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"pages": [{**_page_json(), "restricted": True}]},
        )

    [p] = PagesClient(_client(httpx.MockTransport(handler)), service_token="svc").get_pages(
        "sp_opc"
    )
    assert p.restricted


def test_client_credentials_cached_until_near_expiry() -> None:
    calls: list[bytes] = []
    now = [1000.0]

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.content)
        return httpx.Response(200, json={"access_token": f"tok{len(calls)}", "expires_in": 3600})

    creds = ClientCredentials(
        _client(httpx.MockTransport(handler)), "svc_sync", "s3cret", clock=lambda: now[0]
    )
    assert creds() == "tok1"
    now[0] += 3000
    assert creds() == "tok1"
    now[0] += 590  # within 60s of expiry: refresh
    assert creds() == "tok2"
    assert b"client_secret=s3cret" in calls[0]
    assert "s3cret" not in repr(creds)


def test_client_credentials_failure_is_unavailable() -> None:
    creds = ClientCredentials(
        _client(httpx.MockTransport(lambda _r: httpx.Response(401))), "svc_sync", "bad"
    )
    with pytest.raises(PlatformUnavailable):
        creds()


def test_missing_restricted_flag_fails_closed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        page = {k: v for k, v in _page_json().items() if k != "restricted"}
        return httpx.Response(200, json={"pages": [page]})

    [p] = PagesClient(_client(httpx.MockTransport(handler)), service_token="svc").get_pages(
        "sp_opc"
    )
    assert p.restricted
