"""model-gateway: the single in-cluster door to the on-prem model server.

Exposes only POST /v1/chat/completions and forwards it verbatim to the configured
upstream. Request and response bodies are never logged (they are space content); logs
carry the status and duration only. Network policy lets only ingest reach it, and lets
it reach only the model server.
"""

import os
import time

import httpx
from fastapi import FastAPI, Request, Response

from kc_obs import configure_logging, get_logger

__all__ = ["create_app", "create_app_from_env"]

log = get_logger("model_gateway")


def create_app(upstream: httpx.AsyncClient) -> FastAPI:
    app = FastAPI(title="model-gateway", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        body = await request.body()
        start = time.monotonic()
        try:
            resp = await upstream.post(
                "/chat/completions", content=body, headers={"Content-Type": "application/json"}
            )
        except httpx.HTTPError as e:
            log.error("model_call", status=0, error=e)
            return Response(status_code=502)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info("model_call", status=resp.status_code, duration_ms=elapsed_ms)
        if resp.status_code != 200:
            return Response(status_code=502)  # never relay the upstream body
        return Response(resp.content, media_type="application/json")

    return app


def create_app_from_env() -> FastAPI:
    configure_logging()
    upstream = httpx.AsyncClient(base_url=os.environ["KC_MODEL_UPSTREAM"], timeout=600)
    return create_app(upstream)
