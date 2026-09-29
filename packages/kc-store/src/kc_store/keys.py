"""Per-space keyed digests (Vault transit HMAC in the cluster). Each space has its own key.

Content encryption was removed for now (ADR-012); HMAC stays because IDs derived from it
go into logs and object keys and must not be matchable against outside content.
"""

from typing import Protocol

from kc_labels import SpaceId

__all__ = ["KeyService", "KeyServiceError"]


class KeyServiceError(Exception):
    """The key service refused or failed. Never carries content."""


class KeyService(Protocol):
    def hmac(self, space_id: SpaceId, data: bytes) -> bytes:
        """Keyed digest with the space's key: stable within a space, unlinkable across."""
        ...
