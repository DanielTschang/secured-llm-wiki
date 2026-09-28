import base64
import hashlib
import hmac
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from kc_platform.identity import InvalidToken, Principal, verify_token

ISS = "https://mock-platform.test"
AUD = "knowledge-center"


def _keypair(kid: str) -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    key = ec.generate_private_key(ec.SECP256R1())
    jwk: dict[str, Any] = json.loads(ECAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid=kid, alg="ES256", use="sig")
    return key, {"keys": [jwk]}


KEY, JWKS = _keypair("k1")
OTHER_KEY, _ = _keypair("k1")


def _token(key: ec.EllipticCurvePrivateKey = KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"sub": "u_cd", "iss": ISS, "aud": AUD, "iat": now, "exp": now + 60}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "k1"})


def test_valid_token_yields_principal() -> None:
    p = verify_token(_token(), JWKS, issuer=ISS, audience=AUD)
    assert p.user_id == "u_cd"


def test_principal_repr_hides_token() -> None:
    tok = _token()
    p = verify_token(tok, JWKS, issuer=ISS, audience=AUD)
    assert tok not in repr(p)
    assert p.bearer_token == tok


def test_principal_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError):
        Principal("u_opc", "forged", object())


@pytest.mark.parametrize(
    "token",
    [
        _token(OTHER_KEY),
        _token(exp=int(time.time()) - 10),
        _token(aud="someone-else"),
        _token(iss="https://evil.test"),
        _token(sub=None),
        _token(exp=None),
        "not-a-jwt",
        "",
    ],
    ids=[
        "bad-signature",
        "expired",
        "wrong-aud",
        "wrong-iss",
        "no-sub",
        "no-exp",
        "garbage",
        "empty",
    ],
)
def test_invalid_tokens_rejected(token: str) -> None:
    with pytest.raises(InvalidToken):
        verify_token(token, JWKS, issuer=ISS, audience=AUD)


def _b64(d: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_alg_none_rejected() -> None:
    now = int(time.time())
    body = {"sub": "u_opc_cd", "iss": ISS, "aud": AUD, "exp": now + 60}
    token = f"{_b64({'alg': 'none', 'kid': 'k1'})}.{_b64(body)}."
    with pytest.raises(InvalidToken):
        verify_token(token, JWKS, issuer=ISS, audience=AUD)


def test_hs256_with_public_key_rejected() -> None:
    # Algorithm confusion: sign with HMAC using the public JWK as secret.
    now = int(time.time())
    body = {"sub": "u_opc_cd", "iss": ISS, "aud": AUD, "exp": now + 60}
    signing_input = f"{_b64({'alg': 'HS256', 'kid': 'k1'})}.{_b64(body)}"
    secret = json.dumps(JWKS["keys"][0]).encode()
    mac = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"
    with pytest.raises(InvalidToken):
        verify_token(token, JWKS, issuer=ISS, audience=AUD)


def test_unknown_kid_rejected() -> None:
    token = jwt.encode(
        {"sub": "u_cd", "iss": ISS, "aud": AUD, "exp": int(time.time()) + 60},
        KEY,
        algorithm="ES256",
        headers={"kid": "nope"},
    )
    with pytest.raises(InvalidToken):
        verify_token(token, JWKS, issuer=ISS, audience=AUD)


def test_invalid_token_message_is_generic() -> None:
    with pytest.raises(InvalidToken) as exc:
        verify_token(_token(aud="x"), JWKS, issuer=ISS, audience=AUD)
    assert str(exc.value) == "invalid token"


def test_principal_cannot_be_forged_by_replace_or_mutation() -> None:
    import copy
    import dataclasses

    p = verify_token(_token(), JWKS, issuer=ISS, audience=AUD)
    with pytest.raises(TypeError):
        dataclasses.replace(p, user_id="u_opc_cd")  # type: ignore[type-var]
    with pytest.raises(AttributeError):
        p.user_id = "u_opc_cd"  # type: ignore[misc]
    with pytest.raises(TypeError):
        copy.copy(p)
    with pytest.raises(TypeError):
        copy.deepcopy(p)
