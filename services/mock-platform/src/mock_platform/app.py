"""Mock knowledge platform: space ACLs, getPages, page content, and a JWT issuer.

Development and tests only. Serves the synthetic test set's manifest. Like the real
platform, it exposes `updated_date` and no version number.
"""

import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from jwt.algorithms import ECAlgorithm
from pydantic import BaseModel
from starlette.routing import Match

__all__ = ["AUDIENCE", "ISSUER", "create_app", "create_app_from_env"]

ISSUER = "https://mock-platform.kc.local"
AUDIENCE = "knowledge-center"
SERVICE_ACCOUNTS = frozenset({"svc_sync"})
TOKEN_TTL_SECONDS = 3600

log = logging.getLogger("mock_platform")


class TokenRequest(BaseModel):
    user_id: str


class FaultRequest(BaseModel):
    down: bool


class AclRequest(BaseModel):
    user_id: str
    spaces: list[str]


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"detail": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
    )


def _not_found() -> JSONResponse:
    # Same response whether the thing is missing or the caller may not see it.
    return JSONResponse({"detail": "not found"}, status_code=404)


def create_app(manifest_path: Path) -> FastAPI:
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())
    root = manifest_path.parent
    users: dict[str, set[str]] = {u: set(s) for u, s in manifest["users"].items()}
    pages: dict[str, dict[str, Any]] = {p["page_id"]: p for p in manifest["pages"]}
    state = {"down": False}

    key = ec.generate_private_key(ec.SECP256R1())
    kid = uuid.uuid4().hex
    public_jwk: dict[str, Any] = json.loads(ECAlgorithm.to_jwk(key.public_key()))
    public_jwk.update(kid=kid, alg="ES256", use="sig")

    app = FastAPI(title="mock-platform", docs_url=None, redoc_url=None, openapi_url=None)

    def subject(authorization: str | None) -> str | None:
        if authorization is None or not authorization.startswith("Bearer "):
            return None
        try:
            claims = jwt.decode(
                authorization.removeprefix("Bearer "),
                key.public_key(),
                algorithms=["ES256"],
                issuer=ISSUER,
                audience=AUDIENCE,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError:
            return None
        sub = claims["sub"]
        return sub if isinstance(sub, str) else None

    @app.middleware("http")
    async def fault_and_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if state["down"] and request.url.path.startswith("/api/"):
            response: Response = JSONResponse({"detail": "unavailable"}, status_code=503)
        else:
            response = await call_next(request)
        # Route template only: never raw paths or bodies (invariant 7).
        route = next(
            (
                getattr(r, "path", "?")
                for r in app.router.routes
                if r.matches(request.scope)[0] == Match.FULL
            ),
            "unmatched",
        )
        log.info(
            "request method=%s route=%s status=%d", request.method, route, response.status_code
        )
        return response

    @app.get("/.well-known/jwks.json")
    def jwks() -> dict[str, Any]:
        return {"keys": [public_jwk]}

    @app.post("/dev/token")
    def issue_token(req: TokenRequest) -> Response:
        if req.user_id not in users and req.user_id not in SERVICE_ACCOUNTS:
            return _not_found()
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": req.user_id,
                "iss": ISSUER,
                "aud": AUDIENCE,
                "iat": now,
                "exp": now + TOKEN_TTL_SECONDS,
            },
            key,
            algorithm="ES256",
            headers={"kid": kid},
        )
        return JSONResponse({"access_token": token, "token_type": "Bearer"})

    @app.post("/dev/fault")
    def fault(req: FaultRequest) -> dict[str, bool]:
        state["down"] = req.down
        return {"down": req.down}

    @app.post("/dev/acl")
    def set_acl(req: AclRequest) -> Response:
        if req.user_id not in users:
            return _not_found()
        users[req.user_id] = set(req.spaces)
        return JSONResponse({"user_id": req.user_id})

    @app.get("/api/me/spaces")
    def my_spaces(authorization: str | None = Header(default=None)) -> Response:
        # Identity comes only from the verified token; query, headers, body are ignored.
        sub = subject(authorization)
        if sub is None:
            return _unauthorized()
        return JSONResponse({"spaces": sorted(users.get(sub, set()))})

    def is_service(authorization: str | None) -> bool:
        return subject(authorization) in SERVICE_ACCOUNTS

    @app.get("/api/spaces/{space_id}/pages")
    def get_pages(space_id: str, authorization: str | None = Header(default=None)) -> Response:
        if not is_service(authorization) or space_id not in manifest["spaces"]:
            return _not_found()
        return JSONResponse(
            {
                "pages": [
                    {
                        "page_id": p["page_id"],
                        "space_id": p["space_id"],
                        "title": p["title"],
                        "parent_id": p.get("parent_id"),
                        "updated_date": p["updated_at"],
                    }
                    for p in pages.values()
                    if p["space_id"] == space_id
                ]
            }
        )

    @app.get("/api/pages/{page_id}")
    def get_page(page_id: str, authorization: str | None = Header(default=None)) -> Response:
        page = pages.get(page_id)
        if not is_service(authorization) or page is None:
            return _not_found()
        return JSONResponse(
            {
                "page_id": page["page_id"],
                "space_id": page["space_id"],
                "title": page["title"],
                "parent_id": page.get("parent_id"),
                "updated_date": page["updated_at"],
                "markdown": (root / page["path"]).read_text(),
                "attachments": page["attachments"],
            }
        )

    @app.get("/api/pages/{page_id}/attachments/{name}")
    def get_attachment(
        page_id: str, name: str, authorization: str | None = Header(default=None)
    ) -> Response:
        page = pages.get(page_id)
        # Only names the manifest lists for this page; no path is built from user input.
        if not is_service(authorization) or page is None:
            return _not_found()
        rel = f"attachments/{name}"
        if rel not in page["attachments"]:
            return _not_found()
        return FileResponse(root / "spaces" / page["space_id"] / rel, media_type="image/png")

    return app


def create_app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app(Path(os.environ["KC_MOCK_MANIFEST"]))
