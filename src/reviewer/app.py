"""FastAPI app: factory, token auth, health + config routers.

ponytail: one file for the app + Phase-1 routers. Split into a routers/
package only when this grows past a screen or two per concern.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import DB_PATH, connect, init_db
from .engine import post_to_gerrit, run_review
from .events import EventBus
from .gerrit import GerritClient
from .settings import Settings, basic_auth_header, get_settings
from .store import (
    get_latest_review,
    review_history,
    rule_effectiveness,
    set_feedback,
)
from .worker import review_file
from .worker_direct import review_file_direct

STATIC_DIR = Path(__file__).resolve().parent / "static"


class DashboardStaticFiles(StaticFiles):
    """Prevent stale dashboard HTML after a local server restart."""

    async def get_response(self, path: str, scope: dict) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


class LoginRequest(BaseModel):
    username: str
    http_password: str


def _session(request: Request) -> dict | None:
    sessions = getattr(request.app.state, "sessions", {})
    return sessions.get(request.cookies.get("reviewer_session", ""))


def require_token(
    request: Request,
    x_auth_token: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> str:
    """Guard local API calls with an HttpOnly login session or API_TOKEN."""
    session = _session(request)
    if session:
        return session["auth"]
    expected = settings.secrets.api_token
    if not expected or expected == "change-me":
        raise HTTPException(503, "API_TOKEN not configured — set it in .env")
    if x_auth_token != expected:
        raise HTTPException(401, "invalid or missing X-Auth-Token")
    return settings.secrets.auth_header()


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.conn = init_db(connect(DB_PATH))  # schema + shared connection
        app.state.bus = EventBus()
        app.state.sessions = {}
        try:
            yield
        finally:
            app.state.conn.close()

    app = FastAPI(title="Gerrit Reviewer v4", version="0.4.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "gerrit_url": settings.gerrit.url,
            "auth_configured": bool(settings.secrets.auth_header()),
            "tls_verify": bool(settings.gerrit.ca_bundle) or "system",
            "strategy": settings.review.strategy,
        }

    @app.post("/auth/login")
    async def login(payload: LoginRequest) -> JSONResponse:
        """Validate Gerrit HTTP credentials and issue an opaque local session."""
        if not payload.username.strip() or not payload.http_password:
            raise HTTPException(422, "username and Gerrit HTTP password are required")
        auth = basic_auth_header(payload.username.strip(), payload.http_password)
        try:
            async with GerritClient(
                settings.gerrit.url, auth,
                settings.gerrit.ca_bundle, settings.gerrit.timeout_seconds,
            ) as gerrit:
                profile = await gerrit.account_self()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise HTTPException(401, "invalid Gerrit username or HTTP password") from exc
            raise HTTPException(502, "Gerrit rejected the login request") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, "could not reach Gerrit") from exc

        username = str(profile.get("username") or payload.username.strip())
        display_name = str(profile.get("name") or profile.get("display_name") or username)
        session_id = secrets.token_urlsafe(32)
        app.state.sessions[session_id] = {
            "auth": auth, "username": username, "display_name": display_name,
        }
        response = JSONResponse({"username": username, "display_name": display_name})
        response.set_cookie(
            "reviewer_session", session_id, httponly=True, samesite="lax", max_age=8 * 60 * 60,
        )
        return response

    @app.get("/auth/status")
    def auth_status(request: Request) -> dict:
        session = _session(request)
        if not session:
            return {"authenticated": False}
        return {
            "authenticated": True,
            "username": session["username"],
            "display_name": session["display_name"],
        }

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        getattr(app.state, "sessions", {}).pop(request.cookies.get("reviewer_session", ""), None)
        response = Response(status_code=204)
        response.delete_cookie("reviewer_session")
        return response

    @app.get("/config", dependencies=[Depends(require_token)])
    def config() -> dict:
        """Non-secret runtime config. Token-gated so secrets can't leak by drift."""
        return {
            "gerrit": settings.gerrit.model_dump(),
            "server": settings.server.model_dump(),
            "review": settings.review.model_dump(),
            "cost": settings.cost.model_dump(),
        }

    @app.get("/gerrit/projects")
    async def gerrit_projects(auth: str = Depends(require_token)) -> dict:
        """List visible Gerrit code projects for the dashboard queue."""
        async with GerritClient(
            settings.gerrit.url, auth, settings.gerrit.ca_bundle, settings.gerrit.timeout_seconds,
        ) as gerrit:
            return {"projects": await gerrit.list_projects()}

    @app.get("/gerrit/changes/open")
    async def gerrit_open_changes(
        project: str = "", limit: int = Query(default=100, ge=1, le=200),
        auth: str = Depends(require_token),
    ) -> dict:
        """List open changes, optionally constrained to one Gerrit project."""
        async with GerritClient(
            settings.gerrit.url, auth, settings.gerrit.ca_bundle, settings.gerrit.timeout_seconds,
        ) as gerrit:
            return {"changes": await gerrit.open_changes(project.strip(), limit)}

    @app.get("/gerrit/changes/{change_id}/files")
    async def gerrit_change_files(change_id: str, auth: str = Depends(require_token)) -> dict:
        """List changed files for the interactive diff workspace."""
        async with GerritClient(
            settings.gerrit.url, auth, settings.gerrit.ca_bundle, settings.gerrit.timeout_seconds,
        ) as gerrit:
            files = await gerrit.changed_files(change_id)
            return {"files": [file.model_dump() for file in files]}

    @app.get("/gerrit/changes/{change_id}/files/{file_path:path}/diff")
    async def gerrit_file_diff(
        change_id: str, file_path: str, auth: str = Depends(require_token),
    ) -> dict:
        """Get one file's normalized unified diff on demand."""
        async with GerritClient(
            settings.gerrit.url, auth, settings.gerrit.ca_bundle, settings.gerrit.timeout_seconds,
        ) as gerrit:
            return {"path": file_path, "diff": await gerrit.file_diff(change_id, file_path)}

    async def _do_review(change_id: str, project_slug: str, auth: str) -> None:
        """Background job: run the engine against a real Gerrit + hermes worker."""
        bus: EventBus = app.state.bus

        async def emit(evt: dict) -> None:
            await bus.publish(change_id, evt)

        if settings.review.strategy == "direct":
            async def worker(*args):
                return await review_file_direct(
                    args[0], args[1], args[2], args[3], args[5], args[6],
                    base_url=settings.model.base_url, model=settings.model.name,
                    api_key=settings.secrets.model_api_key,
                )
        else:
            worker = review_file

        try:
            async with GerritClient(
                settings.gerrit.url, auth,
                settings.gerrit.ca_bundle, settings.gerrit.timeout_seconds,
            ) as gerrit:
                await run_review(
                    app.state.conn, settings, gerrit, worker,
                    change_id, auth, project_slug, on_event=emit,
                )
        except Exception as e:  # surface engine/network failures to the stream
            await bus.publish(change_id, {"type": "error", "error": str(e)})

    @app.post("/review/{change_id}")
    async def start_review_endpoint(
        change_id: str, tasks: BackgroundTasks, project_slug: str = "",
        auth: str = Depends(require_token),
    ) -> dict:
        if not auth:
            raise HTTPException(503, "Gerrit auth not configured — sign in or set GERRIT_* in .env")
        if settings.review.strategy == "direct" and not settings.secrets.model_api_key:
            raise HTTPException(503, "MODEL_API_KEY not configured for direct strategy")
        tasks.add_task(_do_review, change_id, project_slug, auth)
        return {"status": "started", "change_id": change_id}

    @app.get("/review/{change_id}", dependencies=[Depends(require_token)])
    def get_review_endpoint(change_id: str) -> dict:
        result = get_latest_review(app.state.conn, change_id)
        if result is None:
            return {"status": "not_started", "change_id": change_id}
        return result

    @app.get("/reviews", dependencies=[Depends(require_token)])
    def review_history_endpoint(limit: int = 50) -> dict:
        return {"reviews": review_history(app.state.conn, max(1, min(limit, 200)))}

    @app.post("/comments/{comment_id}/feedback", dependencies=[Depends(require_token)])
    async def feedback_endpoint(comment_id: int, request: Request) -> dict:
        feedback = (await request.json()).get("feedback", "")
        try:
            changed = set_feedback(app.state.conn, comment_id, feedback)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not changed:
            raise HTTPException(404, "comment not found")
        return {"comment_id": comment_id, "feedback": feedback}

    @app.get("/rules/effectiveness", dependencies=[Depends(require_token)])
    def effectiveness_endpoint() -> dict:
        return {"effectiveness": rule_effectiveness(app.state.conn)}

    @app.post("/hooks/gerrit", status_code=202)
    async def gerrit_webhook(request: Request,
                             x_webhook_secret: str = Header(default="")) -> dict:
        """Accept Gerrit patchset-created webhooks and schedule a review."""
        secret = settings.secrets.webhook_secret
        if not secret or x_webhook_secret != secret:
            raise HTTPException(401, "invalid or missing X-Webhook-Secret")
        payload = await request.json()
        if payload.get("type") != "patchset-created":
            return {"status": "ignored", "reason": "not a patchset-created event"}
        change = payload.get("change") or {}
        change_id = str(change.get("id") or change.get("number") or "")
        if not change_id:
            raise HTTPException(422, "webhook event has no change id")
        auth = settings.secrets.auth_header()
        asyncio.create_task(_do_review(change_id, str(change.get("project") or ""), auth))
        return {"status": "queued", "change_id": change_id}

    @app.post("/review/{change_id}/post")
    async def post_review_endpoint(change_id: str, auth: str = Depends(require_token)) -> dict:
        """Post the latest stored review's comments to Gerrit as robot comments."""
        if not auth:
            raise HTTPException(503, "Gerrit auth not configured — sign in or set GERRIT_* in .env")
        stored = get_latest_review(app.state.conn, change_id)
        if stored is None or stored.get("status") != "done":
            raise HTTPException(409, "no completed review to post")
        async with GerritClient(
            settings.gerrit.url, auth,
            settings.gerrit.ca_bundle, settings.gerrit.timeout_seconds,
        ) as gerrit:
            return await post_to_gerrit(
                app.state.conn, settings, gerrit, change_id, stored["patchset"],
            )

    @app.get("/review/{change_id}/stream")
    async def stream_review(change_id: str, request: Request, token: str = ""):
        """SSE live event stream. ponytail: EventSource can't set headers, so
        the token rides as a query param — same shared secret, gated here."""
        if not _session(request):
            expected = settings.secrets.api_token
            if not expected or expected == "change-me":
                raise HTTPException(503, "API_TOKEN not configured — sign in or set it in .env")
            if token != expected:
                raise HTTPException(401, "invalid or missing token")

        bus: EventBus = app.state.bus
        queue = bus.subscribe(change_id)

        async def gen():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        evt = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"  # comment frame, keeps proxies happy
                        continue
                    yield f"data: {json.dumps(evt)}\n\n"
                    if evt.get("type") in ("done", "error"):
                        break
            finally:
                bus.unsubscribe(change_id, queue)

        return StreamingResponse(gen(), media_type="text/event-stream")

    if STATIC_DIR.is_dir():
        app.mount("/", DashboardStaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
