"""In-memory fakes for unit tests. Not for production use."""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kc_labels import SpaceId
from kc_store.keys import KeyServiceError

__all__ = ["FakeKeyService"]


class FakeKeyService:
    """One master key per space, like Vault transit; can be restricted to some spaces."""

    def __init__(
        self, masters: dict[str, bytes] | None = None, allowed: frozenset[str] | None = None
    ):
        self._masters = masters if masters is not None else {}
        self._allowed = allowed
        self.data_keys_issued = 0

    def restricted_to(self, *spaces: SpaceId) -> FakeKeyService:
        return FakeKeyService(self._masters, frozenset(spaces))

    def _master(self, space: SpaceId) -> bytes:
        if self._allowed is not None and space not in self._allowed:
            raise KeyServiceError("permission denied")
        return self._masters.setdefault(space, AESGCM.generate_key(bit_length=256))

    def new_data_key(self, space_id: SpaceId) -> tuple[bytes, str]:
        self.data_keys_issued += 1
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        wrapped = nonce + AESGCM(self._master(space_id)).encrypt(nonce, dek, space_id.encode())
        return dek, "fake:v1:" + base64.b64encode(wrapped).decode()

    def unwrap(self, space_id: SpaceId, wrapped: str) -> bytes:
        raw = base64.b64decode(wrapped.removeprefix("fake:v1:"))
        try:
            return AESGCM(self._master(space_id)).decrypt(raw[:12], raw[12:], space_id.encode())
        except InvalidTag as e:
            raise KeyServiceError("unwrap failed") from e
