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
            status, json={"choices": [{"message": {"content": "R-CT-114 answer"}}]}
        )

    return httpx.MockTransport(handler)


def client(seen: list[httpx.Request], status: int = 200) -> TestClient:
    http = httpx.AsyncClient(
        transport=upstream(seen, status), base_url="http://192.168.65.254:11434/v1"
    )
    return TestClient(create_app(http))


def test_forwards_chat_completions_verbatim() -> None:
    seen: list[httpx.Request] = []
    resp = client(seen).post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "R-CT-114 answer"
    assert str(seen[0].url) == "http://192.168.65.254:11434/v1/chat/completions"
    assert json.loads(seen[0].content) == BODY


@pytest.mark.parametrize("path", ["/v1/models", "/api/pull", "/v1/embeddings", "/admin"])
def test_only_chat_completions_is_exposed(path: str) -> None:
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
