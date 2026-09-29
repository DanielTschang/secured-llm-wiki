import httpx

from kc_store.vault import VaultClient


def test_child_token_requests_short_ttl() -> None:
    seen: list[tuple[str, bytes]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.content))
        return httpx.Response(200, json={"auth": {"client_token": "child"}})

    v = VaultClient(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://v"), "t")
    assert v.child_token("space-sp_opc") == "child"
    assert seen[0][0] == "/v1/auth/token/create/space-sp_opc"
    assert b'"ttl":"5m"' in seen[0][1].replace(b" ", b"")
