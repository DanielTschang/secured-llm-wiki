"""M0 done criterion: the four mock users get exactly their spaces, end to end."""

import json
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from kc_labels import Labels
from kc_platform.acl import HttpPlatformAcl
from kc_platform.authorizer import Authorizer
from kc_platform.identity import Principal, verify_token
from mock_platform.app import AUDIENCE, ISSUER
from tests.leak.harness import Canary, assert_indistinguishable, assert_no_content

USERS = ["u_general", "u_opc", "u_cd", "u_opc_cd"]


def login(platform: TestClient, token_for: Callable[[str], str], user: str) -> Principal:
    jwks = platform.get("/.well-known/jwks.json").json()
    return verify_token(token_for(user), jwks, issuer=ISSUER, audience=AUDIENCE)


@pytest.mark.parametrize("user", USERS)
def test_four_users_readable_spaces(
    platform: TestClient,
    token_for: Callable[[str], str],
    users: dict[str, frozenset[str]],
    user: str,
) -> None:
    authz = Authorizer(HttpPlatformAcl(platform))
    readable = authz.readable_spaces(login(platform, token_for, user))
    assert readable.spaces == users[user]


@pytest.mark.parametrize("user", USERS)
def test_canary_source_space_readable_only_by_allowed_users(
    platform: TestClient,
    token_for: Callable[[str], str],
    canaries: list[Canary],
    user: str,
) -> None:
    authz = Authorizer(HttpPlatformAcl(platform))
    p = login(platform, token_for, user)
    for c in canaries:
        assert authz.can_read(p, Labels.of([c.source_space])) == (user not in c.must_never_reach)


@pytest.mark.parametrize(
    "smuggle",
    [
        {"params": {"user_id": "u_opc_cd"}},
        {"params": {"spaces": "sp_opc,sp_cd"}},
        {"headers": {"X-User-Id": "u_opc_cd", "X-Spaces": "sp_opc"}},
        {"content": json.dumps({"user_id": "u_opc_cd", "role": "OPC 主管"})},
    ],
    ids=["query-user", "query-spaces", "headers", "body"],
)
def test_identity_claims_in_request_ignored(
    platform: TestClient, token_for: Callable[[str], str], smuggle: dict[str, object]
) -> None:
    tok = token_for("u_cd")
    headers = {"Authorization": f"Bearer {tok}"}
    headers.update(smuggle.pop("headers", {}))  # type: ignore[arg-type]
    resp = platform.request("GET", "/api/me/spaces", headers=headers, **smuggle)  # type: ignore[arg-type]
    assert sorted(resp.json()["spaces"]) == ["sp_cd", "sp_common"]


def test_missing_and_bad_tokens_indistinguishable(
    platform: TestClient, token_for: Callable[[str], str]
) -> None:
    base = platform.get("/api/me/spaces")
    for headers in (
        {"Authorization": "Bearer garbage"},
        {"Authorization": f"Bearer {token_for('u_opc')}x"},
        {"Authorization": "Basic dTpw"},
    ):
        assert_indistinguishable(base, platform.get("/api/me/spaces", headers=headers))


def test_user_token_cannot_list_pages_and_looks_like_missing_space(
    platform: TestClient, token_for: Callable[[str], str]
) -> None:
    h = {"Authorization": f"Bearer {token_for('u_opc_cd')}"}
    svc = {"Authorization": f"Bearer {token_for('svc_sync')}"}
    forbidden = platform.get("/api/spaces/sp_opc/pages", headers=h)
    missing = platform.get("/api/spaces/sp_nope/pages", headers=svc)
    assert forbidden.status_code == 404
    assert_indistinguishable(forbidden, missing)
    assert_indistinguishable(
        platform.get("/api/pages/opc_o1", headers=h),
        platform.get("/api/pages/no_such_page", headers=svc),
    )


def test_user_cannot_distinguish_existing_from_missing_space(
    platform: TestClient, token_for: Callable[[str], str]
) -> None:
    """Attacker view: u_cd probes sp_opc (exists, not readable) vs a space that does not exist."""
    h = {"Authorization": f"Bearer {token_for('u_cd')}"}
    for existing, missing in [
        ("/api/spaces/sp_opc/pages", "/api/spaces/sp_nope/pages"),
        ("/api/spaces/sp_cd/pages", "/api/spaces/sp_nope/pages"),
        ("/api/pages/opc_o1", "/api/pages/no_such_page"),
        ("/api/pages/opc_o1/attachments/o1_residual.png", "/api/pages/nope/attachments/x.png"),
    ]:
        assert_indistinguishable(
            platform.get(existing, headers=h), platform.get(missing, headers=h)
        )


def test_get_pages_exposes_no_version(
    platform: TestClient, token_for: Callable[[str], str]
) -> None:
    h = {"Authorization": f"Bearer {token_for('svc_sync')}"}
    for space in ("sp_common", "sp_opc", "sp_cd"):
        for p in platform.get(f"/api/spaces/{space}/pages", headers=h).json()["pages"]:
            assert "version" not in p
            assert "revision" not in p


def test_platform_down_fails_closed(platform: TestClient, token_for: Callable[[str], str]) -> None:
    authz = Authorizer(HttpPlatformAcl(platform))
    p = login(platform, token_for, "u_opc_cd")
    assert authz.readable_spaces(p).spaces  # positive control: works before the fault
    authz.invalidate(p.user_id)
    platform.post("/dev/fault", json={"down": True})
    assert not authz.can_read(p, Labels.of(["sp_common"]))


def test_acl_changed_scenario(platform: TestClient, token_for: Callable[[str], str]) -> None:
    """gold/sync_scenarios.json acl_changed: no reprocessing; effective after the TTL."""
    now = [0.0]
    authz = Authorizer(HttpPlatformAcl(platform), ttl_seconds=300, clock=lambda: now[0])
    p = login(platform, token_for, "u_opc_cd")
    cd = Labels.of(["sp_cd"])
    assert authz.can_read(p, cd)
    platform.post("/dev/acl", json={"user_id": "u_opc_cd", "spaces": ["sp_common", "sp_opc"]})
    now[0] = 299
    assert authz.can_read(p, cd)  # still cached: accepted delay per ADR-002
    now[0] = 301
    assert not authz.can_read(p, cd)
    assert authz.can_read(p, Labels.of(["sp_opc"]))


def test_logs_contain_no_content(
    platform: TestClient,
    token_for: Callable[[str], str],
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    h = {"Authorization": f"Bearer {token_for('svc_sync')}"}
    for space in ("sp_common", "sp_opc", "sp_cd"):
        for page in platform.get(f"/api/spaces/{space}/pages", headers=h).json()["pages"]:
            platform.get(f"/api/pages/{page['page_id']}", headers=h)
    platform.get("/api/me/spaces", headers={"Authorization": "Bearer garbage"})
    assert captured_logs.records, "expected the mock platform to log requests"
    assert_no_content(captured_logs.text)


def test_harness_detects_canary() -> None:
    """The framework itself must catch a leak (guards against a vacuous harness)."""
    from tests.leak.harness import assert_no_canary

    with pytest.raises(AssertionError):
        assert_no_canary("模型更新為 KESTREL-7", "u_cd")
    assert_no_canary("模型更新為 KESTREL-7", "u_opc")
    with pytest.raises(AssertionError):
        assert_no_content("微影基礎：製程窗口與 MEEF")
    with pytest.raises(AssertionError):
        assert_indistinguishable(httpx.Response(404), httpx.Response(403))
