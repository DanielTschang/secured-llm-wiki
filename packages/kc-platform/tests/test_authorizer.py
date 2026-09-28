import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from kc_labels import Labels, ReadableSpaces
from kc_platform.acl import HttpPlatformAcl, PlatformUnavailable
from kc_platform.authorizer import Authorizer
from kc_platform.identity import Principal, verify_token

ISS = "https://mock-platform.test"
AUD = "knowledge-center"
KEY = ec.generate_private_key(ec.SECP256R1())
_jwk: dict[str, Any] = json.loads(ECAlgorithm.to_jwk(KEY.public_key()))
_jwk.update(kid="k1", alg="ES256")
JWKS = {"keys": [_jwk]}

OPC = Labels.of(["sp_opc"])
CD = Labels.of(["sp_cd"])


def principal(user_id: str) -> Principal:
    now = int(time.time())
    tok = jwt.encode(
        {"sub": user_id, "iss": ISS, "aud": AUD, "exp": now + 600},
        KEY,
        algorithm="ES256",
        headers={"kid": "k1"},
    )
    return verify_token(tok, JWKS, issuer=ISS, audience=AUD)


class FakeAcl:
    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = table
        self.calls = 0
        self.down = False

    def readable_spaces(self, principal: Principal) -> ReadableSpaces:
        self.calls += 1
        if self.down:
            raise PlatformUnavailable
        return ReadableSpaces.of(self.table.get(principal.user_id, []))


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def setup() -> tuple[FakeAcl, Clock, Authorizer]:
    acl = FakeAcl({"u_opc": ["sp_common", "sp_opc"]})
    clock = Clock()
    return acl, clock, Authorizer(acl, ttl_seconds=300, clock=clock)


def test_can_read_uses_platform(setup: tuple[FakeAcl, Clock, Authorizer]) -> None:
    _, _, authz = setup
    u = principal("u_opc")
    assert authz.can_read(u, OPC)
    assert not authz.can_read(u, CD)


def test_cache_hit_within_ttl(setup: tuple[FakeAcl, Clock, Authorizer]) -> None:
    acl, clock, authz = setup
    u = principal("u_opc")
    authz.can_read(u, OPC)
    clock.t += 299
    authz.can_read(u, OPC)
    assert acl.calls == 1


def test_refetch_after_ttl_sees_revocation(setup: tuple[FakeAcl, Clock, Authorizer]) -> None:
    acl, clock, authz = setup
    u = principal("u_opc")
    assert authz.can_read(u, OPC)
    acl.table["u_opc"] = ["sp_common"]
    clock.t += 301
    assert not authz.can_read(u, OPC)
    assert acl.calls == 2


def test_fail_closed_when_platform_down_and_cache_expired(
    setup: tuple[FakeAcl, Clock, Authorizer],
) -> None:
    acl, clock, authz = setup
    u = principal("u_opc")
    assert authz.can_read(u, OPC)
    clock.t += 301
    acl.down = True
    assert not authz.can_read(u, OPC)
    assert authz.readable_spaces(u) == ReadableSpaces.of([])


def test_fail_closed_with_no_cache(setup: tuple[FakeAcl, Clock, Authorizer]) -> None:
    acl, _, authz = setup
    acl.down = True
    assert not authz.can_read(principal("u_opc"), Labels.of(["sp_common"]))


def test_failure_is_not_cached(setup: tuple[FakeAcl, Clock, Authorizer]) -> None:
    acl, _, authz = setup
    u = principal("u_opc")
    acl.down = True
    assert not authz.can_read(u, OPC)
    acl.down = False
    assert authz.can_read(u, OPC)


def test_valid_cache_used_while_platform_down(setup: tuple[FakeAcl, Clock, Authorizer]) -> None:
    acl, clock, authz = setup
    u = principal("u_opc")
    authz.can_read(u, OPC)
    acl.down = True
    clock.t += 100
    assert authz.can_read(u, OPC)


def test_invalidate_forces_refetch(setup: tuple[FakeAcl, Clock, Authorizer]) -> None:
    acl, _, authz = setup
    u = principal("u_opc")
    authz.can_read(u, OPC)
    acl.table["u_opc"] = ["sp_common"]
    authz.invalidate(u.user_id)
    assert not authz.can_read(u, OPC)


def test_cache_is_per_user(setup: tuple[FakeAcl, Clock, Authorizer]) -> None:
    _, _, authz = setup
    assert authz.can_read(principal("u_opc"), OPC)
    assert not authz.can_read(principal("u_cd"), OPC)


def test_unexpected_acl_error_fails_closed() -> None:
    class Broken:
        def readable_spaces(self, principal: Principal) -> ReadableSpaces:
            raise RuntimeError("boom")

    authz = Authorizer(Broken(), ttl_seconds=300)
    assert not authz.can_read(principal("u_opc"), OPC)


# --- HTTP ACL client --------------------------------------------------------


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://platform")


def test_http_acl_sends_user_token_and_parses() -> None:
    u = principal("u_opc")
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["path"] = req.url.path
        return httpx.Response(200, json={"spaces": ["sp_common", "sp_opc"]})

    r = HttpPlatformAcl(_client(handler)).readable_spaces(u)
    assert r == ReadableSpaces.of(["sp_common", "sp_opc"])
    assert seen == {"auth": f"Bearer {u.bearer_token}", "path": "/api/me/spaces"}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(401),
        httpx.Response(200, json={"spaces": ["not a space id"]}),
        httpx.Response(200, json={"nope": []}),
        httpx.Response(200, text="<html>"),
    ],
)
def test_http_acl_errors_raise_unavailable(response: httpx.Response) -> None:
    acl = HttpPlatformAcl(_client(lambda _req: response))
    with pytest.raises(PlatformUnavailable):
        acl.readable_spaces(principal("u_opc"))


def test_http_acl_network_error_raises_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(PlatformUnavailable):
        HttpPlatformAcl(_client(handler)).readable_spaces(principal("u_opc"))
