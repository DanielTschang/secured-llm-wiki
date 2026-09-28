"""Per-space key service (Vault transit in the cluster). Each space has its own key."""

from typing import Protocol

from kc_labels import SpaceId

__all__ = ["KeyService", "KeyServiceError"]


class KeyServiceError(Exception):
    """The key service refused or failed. Never carries content."""


class KeyService(Protocol):
    def new_data_key(self, space_id: SpaceId) -> tuple[bytes, str]:
        """A fresh 256-bit data key and its wrapped form (wrapped by the space's key)."""
        ...

    def unwrap(self, space_id: SpaceId, wrapped: str) -> bytes: ...
