import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from model_gateway.app import create_app

BODY = {
    "model": "qwen2.5vl:7b",
    "messages": [{"role": "user", "content": [{"type": "text", "text": "KESTREL-7 設定"}]}],
}


def upstream(seen: list[httpx.Request], status: int = 200) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            status,
            json={
                "id": "chatcmpl-1",
                "model": "qwen2.5vl:7b",
                "system_fingerprint": "fp_ollama",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "R-CT-114 answer"}}
                ],
                "usage": {
                    "prompt_tokens": 1058,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 1057},
                },
            },
        )

    return httpx.MockTransport(handler)


def client(seen: list[httpx.Request], status: int = 200) -> TestClient:
    http = httpx.AsyncClient(
        transport=upstream(seen, status), base_url="http://192.168.65.254:11434/v1"
    )
    return TestClient(create_app(http))


def test_forwards_request_and_returns_only_the_content() -> None:
    """Shared model state (prefix/KV cache) must not be observable: usage, cached-token
    counts, fingerprints and ids from the model server never reach the caller."""
    seen: list[httpx.Request] = []
    resp = client(seen).post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 200
    assert resp.json() == {"choices": [{"message": {"content": "R-CT-114 answer"}}]}
    assert str(seen[0].url) == "http://192.168.65.254:11434/v1/chat/completions"
    assert json.loads(seen[0].content) == BODY


@pytest.mark.parametrize("path", ["/v1/models", "/api/pull", "/api/generate", "/admin"])
def test_only_chat_and_embeddings_are_exposed(path: str) -> None:
    seen: list[httpx.Request] = []
    assert client(seen).post(path, json={}).status_code == 404
    assert seen == []


def test_upstream_error_is_passed_as_status_only() -> None:
    seen: list[httpx.Request] = []
    resp = client(seen, status=500).post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 502
    assert "R-CT-114" not in resp.text


def test_logs_contain_no_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    seen: list[httpx.Request] = []
    client(seen).post("/v1/chat/completions", json=BODY)
    assert any("model_call" in r.getMessage() for r in caplog.records)
    for secret in ("KESTREL", "R-CT-114", "設定"):
        assert secret not in caplog.text


def test_unparseable_upstream_response_is_502() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://192.168.65.254:11434/v1"
    )
    resp = TestClient(create_app(http)).post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 502 and resp.content == b""


def test_upstream_client_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from model_gateway.app import upstream_client

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    assert upstream_client("http://192.168.65.254:11434/v1")._trust_env is False  # pyright: ignore[reportPrivateUsage]


def test_embeddings_return_only_vectors() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "bge-m3",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.5, 0.25]}],
                "usage": {"prompt_tokens": 9, "total_tokens": 9},
            },
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://192.168.65.254:11434/v1"
    )
    resp = TestClient(create_app(http)).post(
        "/v1/embeddings", json={"model": "bge-m3", "input": ["KESTREL"]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"data": [{"embedding": [0.5, 0.25]}]}
