"""Identity comes only from a verified token (invariant 6)."""

from typing import Any, NoReturn

import jwt

__all__ = ["InvalidToken", "Principal", "verify_token"]

_ALGORITHMS = ["ES256"]
_CONSTRUCT = object()


class InvalidToken(Exception):
    def __init__(self) -> None:
        # Generic on purpose: never reveal why a token failed.
        super().__init__("invalid token")


class Principal:
    """An authenticated user. Only verify_token can create one; it cannot be copied,
    replaced, or mutated into another user."""

    __slots__ = ("_token", "_user_id")

    def __init__(self, user_id: str, bearer_token: str, guard: object) -> None:
        if guard is not _CONSTRUCT:
            raise TypeError("Principal is created only by verify_token")
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_token", bearer_token)

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def bearer_token(self) -> str:
        return self._token

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("Principal is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError("Principal is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("Principal cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("Principal cannot be copied")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        raise TypeError("Principal cannot be serialized")

    def __repr__(self) -> str:
        return f"Principal(user_id={self._user_id!r})"


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
