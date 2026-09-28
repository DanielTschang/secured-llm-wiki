"""Identity comes only from a verified token (invariant 6)."""

from dataclasses import dataclass, field
from typing import Any

import jwt

__all__ = ["InvalidToken", "Principal", "verify_token"]

_ALGORITHMS = ["ES256"]
_CONSTRUCT = object()


class InvalidToken(Exception):
    def __init__(self) -> None:
        # Generic on purpose: never reveal why a token failed.
        super().__init__("invalid token")


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated user. Only verify_token can create one."""

    user_id: str
    bearer_token: str = field(repr=False)
    _guard: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._guard is not _CONSTRUCT:
            raise TypeError("Principal is created only by verify_token")


def verify_token(token: str, jwks: dict[str, Any], *, issuer: str, audience: str) -> Principal:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        if not isinstance(kid, str):
            raise InvalidToken
        key = jwt.PyJWKSet.from_dict(jwks)[kid]
        claims = jwt.decode(
            token,
            key.key,
            algorithms=_ALGORITHMS,
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except Exception as e:
        raise InvalidToken from e
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise InvalidToken
    return Principal(sub, token, _CONSTRUCT)
