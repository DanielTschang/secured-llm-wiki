"""Mock knowledge platform: space ACLs, getPages, page content, and a JWT issuer.

Development and tests only. Serves the synthetic test set's manifest. Like the real
platform, it exposes `updated_date` and no version number.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
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


class RestrictRequest(BaseModel):
    page_id: str
    restricted: bool


class AttachmentEdit(BaseModel):
    name: str
    content_b64: str


class PageEdit(BaseModel):
    page_id: str
    markdown: str | None = None
    updated_date: str | None = None
    attachment: AttachmentEdit | None = None


_INCLUDE = re.compile(r'\{\{include page="([^"]+)" section="(\d+)"\}\}')


def _unregistered(_path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return lambda f: f


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"detail": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
    )


def _not_found() -> JSONResponse:
    # Same response whether the thing is missing or the caller may not see it.
    return JSONResponse({"detail": "not found"}, status_code=404)


def create_app(
    manifest_path: Path, *, dev_endpoints: bool = False, svc_sync_secret_sha256: str = ""
) -> FastAPI:
    """dev_endpoints registers /dev/* (token issuing, fault, ACL and content controls) for
    tests. svc_sync_secret_sha256 enables client-credentials login for svc_sync; only the
    hash is known here, the secret itself lives in Vault."""
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())
    root = manifest_path.parent
    users: dict[str, set[str]] = {u: set(s) for u, s in manifest["users"].items()}
    pages: dict[str, dict[str, Any]] = {p["page_id"]: p for p in manifest["pages"]}
    key = ec.generate_private_key(ec.SECP256R1())
    kid = uuid.uuid4().hex
    public_jwk: dict[str, Any] = json.loads(ECAlgorithm.to_jwk(key.public_key()))
    public_jwk.update(kid=kid, alg="ES256", use="sig")

    state = {"down": False}
    restricted: set[str] = set()
    markdown_edits: dict[str, str] = {}
    attachment_edits: dict[tuple[str, str], bytes] = {}

    def markdown_of(page: dict[str, Any]) -> str:
        return markdown_edits.get(page["page_id"]) or (root / page["path"]).read_text()

    def attachment_of(page: dict[str, Any], name: str) -> bytes:
        edited = attachment_edits.get((page["page_id"], name))
        if edited is not None:
            return edited
        return (root / "spaces" / page["space_id"] / "attachments" / name).read_bytes()

    def rendered(markdown: str) -> str:
        """What the platform UI shows: includes expanded from any space. Sync must never
        fetch this form (the raw form keeps includes as references)."""

        def expand(m: re.Match[str]) -> str:
            other = pages.get(m.group(1))
            if other is None:
                return ""
            for chunk in re.split(r"(?m)^---$", markdown_of(other)):
                if re.search(rf"(?m)^## {m.group(2)}\.", chunk):
                    return chunk.strip()
            return ""

        return _INCLUDE.sub(expand, markdown)

    def issue(subject_id: str) -> str:
        now = int(time.time())
        return jwt.encode(
            {"sub": subject_id, "iss": ISSUER, "aud": AUDIENCE, "iat": now,
             "exp": now + TOKEN_TTL_SECONDS},
            key,
            algorithm="ES256",
            headers={"kid": kid},
        )  # fmt: skip

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

    dev = app.post if dev_endpoints else _unregistered

    @dev("/dev/token")
    def issue_token(req: TokenRequest) -> Response:
        if req.user_id not in users and req.user_id not in SERVICE_ACCOUNTS:
            return _not_found()
        return JSONResponse({"access_token": issue(req.user_id), "token_type": "Bearer"})

    @dev("/dev/fault")
    def fault(req: FaultRequest) -> dict[str, bool]:
        state["down"] = req.down
        return {"down": req.down}

    @dev("/dev/acl")
    def set_acl(req: AclRequest) -> Response:
        if req.user_id not in users:
            return _not_found()
        users[req.user_id] = set(req.spaces)
        return JSONResponse({"user_id": req.user_id})

    @dev("/dev/restrict")
    def set_restricted(req: RestrictRequest) -> Response:
        if req.page_id not in pages:
            return _not_found()
        (restricted.add if req.restricted else restricted.discard)(req.page_id)
        return JSONResponse({"page_id": req.page_id})

    @dev("/dev/page")
    def edit_page(req: PageEdit) -> Response:
        page = pages.get(req.page_id)
        if page is None:
            return _not_found()
        if req.markdown is not None:
            markdown_edits[req.page_id] = req.markdown
        if req.updated_date is not None:
            page["updated_at"] = req.updated_date
        if req.attachment is not None:
            if f"attachments/{req.attachment.name}" not in page["attachments"]:
                return _not_found()
            attachment_edits[(req.page_id, req.attachment.name)] = base64.b64decode(
                req.attachment.content_b64
            )
        return JSONResponse({"page_id": req.page_id})

    @app.post("/oauth/token")
    async def client_credentials(request: Request) -> Response:
        # Parsed by hand to avoid a form-parsing dependency; every failure looks the same.
        form = {k: v[0] for k, v in urllib.parse.parse_qs((await request.body()).decode()).items()}
        client_id = form.get("client_id", "")
        secret = form.get("client_secret", "")
        ok = (
            bool(svc_sync_secret_sha256)
            and form.get("grant_type") == "client_credentials"
            and client_id in SERVICE_ACCOUNTS
            and hmac.compare_digest(
                hashlib.sha256(secret.encode()).hexdigest(), svc_sync_secret_sha256
            )
        )
        if not ok:
            return _unauthorized()
        return JSONResponse(
            {
                "access_token": issue(client_id),
                "token_type": "Bearer",
                "expires_in": TOKEN_TTL_SECONDS,
            }
        )

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
                        "restricted": p["page_id"] in restricted,
                    }
                    for p in pages.values()
                    if p["space_id"] == space_id
                ]
            }
        )

    @app.get("/api/pages/{page_id}")
    def get_page(
        page_id: str, format: str = "raw", authorization: str | None = Header(default=None)
    ) -> Response:
        page = pages.get(page_id)
        if not is_service(authorization) or page is None:
            return _not_found()
        markdown = markdown_of(page)
        return JSONResponse(
            {
                "page_id": page["page_id"],
                "space_id": page["space_id"],
                "title": page["title"],
                "parent_id": page.get("parent_id"),
                "updated_date": page["updated_at"],
                "markdown": rendered(markdown) if format == "rendered" else markdown,
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
        if f"attachments/{name}" not in page["attachments"]:
            return _not_found()
        return Response(attachment_of(page, name), media_type="image/png")

    return app


def create_app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app(
        Path(os.environ["KC_MOCK_MANIFEST"]),
        dev_endpoints=os.environ.get("KC_MOCK_DEV_ENDPOINTS") == "1",
        svc_sync_secret_sha256=os.environ.get("KC_MOCK_SVC_SYNC_SECRET_SHA256", ""),
    )
