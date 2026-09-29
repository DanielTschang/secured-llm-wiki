import base64
import json

import httpx
import pytest

from kc_models import (
    FakeModel,
    ImagePart,
    Message,
    ModelError,
    OpenAICompatibleBackend,
    TextPart,
)

MSGS = [
    Message("system", [TextPart("You read slides.")]),
    Message("user", [TextPart("before"), ImagePart(b"\x89PNG"), TextPart("after KESTREL")]),
]


def backend(handler: httpx.MockTransport, **kw: object) -> OpenAICompatibleBackend:
    return OpenAICompatibleBackend(
        "http://kc-model-gateway:8080/v1",
        "qwen2.5vl:7b",
        allowed_hosts=frozenset({"kc-model-gateway"}),
        http=httpx.Client(transport=handler),
        **kw,  # type: ignore[arg-type]
    )


def test_request_is_openai_chat_completions_with_interleaved_parts() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    out = backend(httpx.MockTransport(handler)).chat(MSGS, json_schema={"type": "object"})
    assert out == '{"ok": true}'
    assert seen["url"] == "http://kc-model-gateway:8080/v1/chat/completions"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "qwen2.5vl:7b"
    assert body["temperature"] == 0
    user = body["messages"][1]["content"]
    assert [p["type"] for p in user] == ["text", "image_url", "text"]  # order preserved
    assert (
        user[1]["image_url"]["url"]
        == "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()
    )
    assert body["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://kc-model-gateway.evil.com:8080/v1",
        "http://user@api.openai.com/v1",
        "file:///etc/passwd",
    ],
)
def test_base_url_outside_allowlist_refused(url: str) -> None:
    with pytest.raises(ValueError, match="not allowed"):
        OpenAICompatibleBackend(url, "m", allowed_hosts=frozenset({"kc-model-gateway"}))


def test_errors_do_not_echo_content() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream said: after KESTREL")

    with pytest.raises(ModelError) as exc:
        backend(httpx.MockTransport(handler)).chat(MSGS)
    assert "KESTREL" not in str(exc.value)
    assert exc.value.status == 500


def test_malformed_response_is_model_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nope": 1})

    with pytest.raises(ModelError):
        backend(httpx.MockTransport(handler)).chat(MSGS)


def test_fake_model_records_and_scripts() -> None:
    fake = FakeModel(lambda messages, tag: f'{{"tag": "{tag}"}}')
    assert fake.chat(MSGS, tag="opc_o1#2") == '{"tag": "opc_o1#2"}'
    assert fake.calls[0].tag == "opc_o1#2"
    assert "after KESTREL" in fake.calls[0].text()
    assert fake.calls[0].images() == [b"\x89PNG"]


def test_default_client_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    b = OpenAICompatibleBackend(
        "http://kc-model-gateway:8080/v1", "m", allowed_hosts=frozenset({"kc-model-gateway"})
    )
    assert b._http._trust_env is False  # pyright: ignore[reportPrivateUsage]
