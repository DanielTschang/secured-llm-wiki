from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kc_platform.identity import verify_token
from mock_platform.app import AUDIENCE, ISSUER, create_app

TESTSET = Path(__file__).parents[3] / "tests/fixtures/synthetic_litho_testset"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(TESTSET / "manifest.json", dev_endpoints=True))


def token(client: TestClient, subject: str) -> str:
    resp = client.post("/dev/token", json={"user_id": subject})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def auth(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def test_issued_token_verifies_against_jwks(client: TestClient) -> None:
    jwks = client.get("/.well-known/jwks.json").json()
    p = verify_token(token(client, "u_cd"), jwks, issuer=ISSUER, audience=AUDIENCE)
    assert p.user_id == "u_cd"


def test_jwks_has_no_private_material(client: TestClient) -> None:
    for k in client.get("/.well-known/jwks.json").json()["keys"]:
        assert "d" not in k


def test_token_for_unknown_user_refused(client: TestClient) -> None:
    assert client.post("/dev/token", json={"user_id": "u_nobody"}).status_code == 404


def test_me_spaces(client: TestClient) -> None:
    resp = client.get("/api/me/spaces", headers=auth(token(client, "u_opc")))
    assert resp.status_code == 200
    assert sorted(resp.json()["spaces"]) == ["sp_common", "sp_opc"]


def test_me_spaces_requires_token(client: TestClient) -> None:
    assert client.get("/api/me/spaces").status_code == 401


def test_fault_makes_api_unavailable(client: TestClient) -> None:
    tok = token(client, "u_opc")
    client.post("/dev/fault", json={"down": True})
    assert client.get("/api/me/spaces", headers=auth(tok)).status_code == 503
    client.post("/dev/fault", json={"down": False})
    assert client.get("/api/me/spaces", headers=auth(tok)).status_code == 200


def test_acl_change(client: TestClient) -> None:
    tok = token(client, "u_opc_cd")
    client.post("/dev/acl", json={"user_id": "u_opc_cd", "spaces": ["sp_common", "sp_opc"]})
    spaces = client.get("/api/me/spaces", headers=auth(tok)).json()["spaces"]
    assert sorted(spaces) == ["sp_common", "sp_opc"]


def test_get_pages_for_sync_service(client: TestClient) -> None:
    resp = client.get("/api/spaces/sp_opc/pages", headers=auth(token(client, "svc_sync")))
    assert resp.status_code == 200
    pages = resp.json()["pages"]
    assert sorted(p["page_id"] for p in pages) == ["opc_o1", "opc_o2"]
    for p in pages:
        assert set(p) == {"page_id", "space_id", "title", "parent_id", "updated_date"}


def test_page_content_and_attachment(client: TestClient) -> None:
    h = auth(token(client, "svc_sync"))
    page = client.get("/api/pages/opc_o2", headers=h).json()
    assert page["space_id"] == "sp_opc"
    assert "attachments/o2_rules_2025.png" in page["attachments"]
    img = client.get("/api/pages/opc_o2/attachments/o2_rules_2025.png", headers=h)
    assert img.status_code == 200
    assert img.content.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "path",
    [
        "/api/pages/opc_o2/attachments/..%2F..%2Fmanifest.json",
        "/api/pages/opc_o2/attachments/d1_sem_bridge.png",  # belongs to another page
    ],
)
def test_attachment_outside_page_refused(client: TestClient, path: str) -> None:
    resp = client.get(path, headers=auth(token(client, "svc_sync")))
    assert resp.status_code == 404


@pytest.mark.parametrize("path", ["/dev/token", "/dev/fault", "/dev/acl"])
def test_dev_endpoints_off_by_default(path: str) -> None:
    c = TestClient(create_app(TESTSET / "manifest.json"))
    resp = c.post(path, json={"user_id": "svc_sync", "down": True, "spaces": []})
    assert resp.status_code == 404
    assert resp.content == c.post("/no/such/route").content


def test_env_factory_keeps_dev_endpoints_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from mock_platform.app import create_app_from_env

    monkeypatch.setenv("KC_MOCK_MANIFEST", str(TESTSET / "manifest.json"))
    monkeypatch.delenv("KC_MOCK_DEV_ENDPOINTS", raising=False)
    c = TestClient(create_app_from_env())
    assert c.post("/dev/token", json={"user_id": "svc_sync"}).status_code == 404
