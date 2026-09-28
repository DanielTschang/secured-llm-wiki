"""Minimal Vault client (transit, KV v2, database creds, token roles, kubernetes auth).

Errors never include response bodies, which could echo request content.
"""

from typing import Any

import httpx

from kc_labels import SpaceId
from kc_store.keys import KeyServiceError

__all__ = ["VaultClient", "VaultError", "VaultKeyService"]


class VaultError(KeyServiceError):
    def __init__(self, status: int) -> None:
        super().__init__(f"vault request failed ({status})")
        self.status = status


class VaultClient:
    def __init__(self, http: httpx.Client, token: str) -> None:
        self._http = http
        self._token = token

    def __repr__(self) -> str:
        return "VaultClient(<token hidden>)"

    @classmethod
    def kubernetes_login(cls, http: httpx.Client, role: str, jwt: str) -> VaultClient:
        resp = http.post("/v1/auth/kubernetes/login", json={"role": role, "jwt": jwt})
        if resp.status_code != 200:
            raise VaultError(resp.status_code)
        return cls(http, resp.json()["auth"]["client_token"])

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._http.request(
            method, f"/v1/{path}", json=body, headers={"X-Vault-Token": self._token}
        )
        if resp.status_code >= 400:
            raise VaultError(resp.status_code)
        return resp.json() if resp.content else {}

    def child_token(self, token_role: str) -> str:
        """A token from a token role (the broker mints single-space task tokens this way)."""
        return self._call("POST", f"auth/token/create/{token_role}")["auth"]["client_token"]

    def with_token(self, token: str) -> VaultClient:
        return VaultClient(self._http, token)

    def datakey(self, key: str) -> tuple[str, str]:
        data = self._call("POST", f"transit/datakey/plaintext/{key}")["data"]
        return data["plaintext"], data["ciphertext"]

    def decrypt(self, key: str, ciphertext: str) -> str:
        return self._call("POST", f"transit/decrypt/{key}", {"ciphertext": ciphertext})["data"][
            "plaintext"
        ]

    def kv(self, path: str) -> dict[str, Any]:
        return self._call("GET", f"kv/data/{path}")["data"]["data"]

    def database_creds(self, role: str) -> tuple[str, str]:
        data = self._call("GET", f"database/creds/{role}")["data"]
        return data["username"], data["password"]


class VaultKeyService:
    """KeyService on Vault transit: one key per space, named by the space ID."""

    def __init__(self, vault: VaultClient) -> None:
        self._vault = vault

    def new_data_key(self, space_id: SpaceId) -> tuple[bytes, str]:
        import base64

        plaintext, wrapped = self._vault.datakey(space_id)
        return base64.b64decode(plaintext), wrapped

    def unwrap(self, space_id: SpaceId, wrapped: str) -> bytes:
        import base64

        return base64.b64decode(self._vault.decrypt(space_id, wrapped))
