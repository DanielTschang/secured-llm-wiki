"""The only way to call a model (CLAUDE.md). Two backends:

- OpenAICompatibleBackend: OpenAI Chat Completions over HTTP (Ollama, vLLM, TGI, ...).
  Only hosts on an explicit internal allowlist are accepted: no process that handles
  space content may reach anything outside the cluster (invariant 7).
- FakeModel: deterministic, records every call; for tests.
"""

import base64
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx

__all__ = [
    "FakeModel",
    "ImagePart",
    "Message",
    "ModelError",
    "OpenAICompatibleBackend",
    "Part",
    "TextPart",
    "VisionModel",
    "from_env",
]


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ImagePart:
    data: bytes = field(repr=False)
    mime: str = "image/png"


type Part = TextPart | ImagePart


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant"]
    parts: Sequence[Part]


class ModelError(Exception):
    def __init__(self, status: int = 0) -> None:
        super().__init__(f"model call failed ({status})")  # never the body
        self.status = status


class VisionModel(Protocol):
    def chat(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        tag: str = "",
    ) -> str:
        """Returns the model's text. `tag` is an ID (e.g. a slide ref) for fakes and logs."""
        ...


def _content(parts: Sequence[Part]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        if isinstance(p, TextPart):
            out.append({"type": "text", "text": p.text})
        else:
            url = f"data:{p.mime};base64,{base64.b64encode(p.data).decode()}"
            out.append({"type": "image_url", "image_url": {"url": url}})
    return out


class OpenAICompatibleBackend:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        allowed_hosts: frozenset[str],
        http: httpx.Client | None = None,
        timeout: float = 300,
    ) -> None:
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.username is not None
            or parts.hostname not in allowed_hosts
        ):
            raise ValueError("model endpoint not allowed")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._http = http or httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return f"OpenAICompatibleBackend(model={self._model!r})"

    def chat(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        tag: str = "",
    ) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "messages": [{"role": m.role, "content": _content(m.parts)} for m in messages],
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema, "strict": True},
            }
        try:
            resp = self._http.post(self._url, json=body)
        except httpx.HTTPError as e:
            raise ModelError(0) from e
        if resp.status_code != 200:
            raise ModelError(resp.status_code)
        try:
            return str(resp.json()["choices"][0]["message"]["content"])
        except Exception as e:
            raise ModelError(resp.status_code) from e


@dataclass(frozen=True, slots=True)
class Call:
    messages: Sequence[Message]
    tag: str
    json_schema: dict[str, Any] | None

    def text(self) -> str:
        return "\n".join(p.text for m in self.messages for p in m.parts if isinstance(p, TextPart))

    def images(self) -> list[bytes]:
        return [p.data for m in self.messages for p in m.parts if isinstance(p, ImagePart)]


class FakeModel:
    def __init__(self, script: Callable[[Sequence[Message], str], str]) -> None:
        self._script = script
        self.calls: list[Call] = []

    def chat(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        tag: str = "",
    ) -> str:
        self.calls.append(Call(messages, tag, json_schema))
        return self._script(messages, tag)


def from_env() -> OpenAICompatibleBackend:
    """KC_MODEL_BASE_URL, KC_MODEL_NAME, KC_MODEL_ALLOWED_HOSTS (comma-separated)."""
    return OpenAICompatibleBackend(
        os.environ["KC_MODEL_BASE_URL"],
        os.environ["KC_MODEL_NAME"],
        allowed_hosts=frozenset(h for h in os.environ["KC_MODEL_ALLOWED_HOSTS"].split(",") if h),
    )
