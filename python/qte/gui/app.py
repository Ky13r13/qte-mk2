"""Local documentation, evidence and explicitly requested offline research jobs."""

from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
import time
from typing import Callable
from uuid import uuid4

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .documents import DocumentLibrary
from .artifact_routes import artifact_routes
from .catalog import Catalog
from .dataset_routes import dataset_routes
from .form_routes import form_routes
from .comparison_routes import comparison_routes
from .job_routes import job_routes
from .test_routes import test_routes
from .benchmark_routes import benchmark_routes
from .connection_routes import connection_routes
from .jobs import JobManager
from .requests import json_request, pagination
from .safeio import SafeReadError
from .security import IDLE_TIMEOUT_SECONDS, SecurityError, SessionController, unique_header

TOKEN_HEADER = "X-QTE-Token"
COOKIE_NAME = "qte_session"
STATIC_NAMES = {"index.html", "app.css", "app.js", "experiments.js", "comparisons.js", "comparison_forms.js", "research_forms.js", "research_jobs.js", "datasets.js", "system_tests.js", "system_benchmarks.js", "system_connections.js"}


def create_app(repository: Path, *, port: int = 8765, code_sink: Callable[[str], object] = print,
               clock: Callable[[], float] = time.monotonic,
               alpaca_download_enabled: bool = False) -> Starlette:
    controller = SessionController(code_sink, clock)
    library = DocumentLibrary(Path(repository))
    catalog = Catalog(Path(repository))
    # The job dispatcher owns its own verification cache; durable catalog and
    # protection storage are shared, not mutable request-thread cache objects.
    jobs = JobManager(Path(repository), Catalog(Path(repository)),
                      alpaca_download_enabled=alpaca_download_enabled)
    expected_host = f"127.0.0.1:{port}"
    expected_origin = f"http://{expected_host}"
    static_root = Path(__file__).with_name("static")

    def error(code: str, status: int, message: str) -> JSONResponse:
        return JSONResponse({"schema_version": 1, "error": {"code": code, "message": message,
                            "request_id": uuid4().hex}}, status_code=status,
                            headers={"Cache-Control": "no-store"})

    def session_for(request: Request, *, token: bool = False):
        session = controller.authenticate(request.cookies.get(COOKIE_NAME))
        if token:
            controller.require_token(session, unique_header(request.scope, TOKEN_HEADER.lower().encode("ascii")))
        return session

    async def root(request: Request) -> Response:
        return RedirectResponse("/library", status_code=303)

    async def shell(request: Request) -> Response:
        try:
            controller.authenticate(request.cookies.get(COOKIE_NAME))
        except SecurityError:
            return RedirectResponse("/login", status_code=303)
        index = static_root / "index.html"
        if index.is_file():
            return Response(index.read_bytes(), media_type="text/html")
        return Response("QTE Library", media_type="text/html")

    async def login_page(request: Request) -> Response:
        index = static_root / "index.html"
        if index.is_file():
            return Response(index.read_bytes(), media_type="text/html")
        return Response("QTE Library login", media_type="text/html")

    async def asset(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in STATIC_NAMES or name == "index.html":
            return error("not_found", 404, "Resource not found.")
        path = static_root / name
        if not path.is_file():
            return error("not_found", 404, "Resource not found.")
        media = "text/css" if name.endswith(".css") else "text/javascript"
        return Response(path.read_bytes(), media_type=media)

    async def create_session(request: Request) -> Response:
        payload = await json_request(request, {"code"})
        session = controller.login(payload["code"])
        response = JSONResponse({"schema_version": 1, "request_token": session.request_token,
                                 "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS},
                                headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE_NAME, session.cookie, httponly=True, samesite="strict", path="/")
        return response

    async def get_session(request: Request) -> Response:
        if request.headers.get("sec-fetch-site") != "same-origin":
            raise SecurityError("same_origin_required", 403, "Same-origin fetch metadata required.")
        session = session_for(request)
        return JSONResponse({"schema_version": 1, "request_token": session.request_token,
                             "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS}, headers={"Cache-Control": "no-store"})

    async def delete_session(request: Request) -> Response:
        session = session_for(request, token=True)
        controller.logout(session)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    async def capabilities(request: Request) -> Response:
        session_for(request, token=True)
        available = library.available
        values = [
            {"id": "library", "state": "available" if available else "blocked", "reason":
             "Configured document source has passed validation." if available else "Library source unavailable."},
             {"id": "research", "state": "available", "reason": "Local artifact catalog, exact-value research forms and explicitly launched offline run/lab jobs."},
            {"id": "data", "state": "available", "reason": "Local validation and download request previews. Downloads require explicit server permission and credentials."},
            {"id": "test_execution", "state": "available", "reason": "Explicit fixed local test jobs; prerequisites checked on launch. Test evidence requires recorded disclosure."},
            {"id": "benchmarks", "state": "available", "reason": "Explicit bounded synthetic no-op/SMA timing jobs; not evidence of trading merit."},
            {"id": "connection_inspection", "state": "available", "reason": "Offline prerequisite status only; credential values are not inspected and no account authentication is checked."},
            {"id": "broker_reads", "state": "blocked", "reason": "GUI broker transport is disabled; approved setup and private-response contracts remain unresolved."},
            {"id": "paper_execution", "state": "blocked", "reason": "Operational paper execution is not integrated."},
        ]
        return JSONResponse({"schema_version": 1, "capabilities": values}, headers={"Cache-Control": "no-store"})

    async def download_prerequisites(request: Request) -> Response:
        session_for(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        return JSONResponse({"schema_version": 1, **jobs.download_prerequisites()},
                            headers={"Cache-Control": "no-store"})

    async def library_api(request: Request) -> Response:
        session_for(request, token=True)
        q = request.query_params.get("q", "")
        offset, limit = pagination(request, extra={"q"})
        if len(q) > 200:
            raise SecurityError("invalid_query", 400, "Invalid library query.")
        items, total = library.search(q, offset, limit)
        return JSONResponse({"schema_version": 1, "items": items, "total": total,
                             "offset": offset, "limit": limit}, headers={"Cache-Control": "no-store"})

    async def document_api(request: Request) -> Response:
        session_for(request, token=True)
        return JSONResponse(library.render(request.path_params["document_id"]), headers={"Cache-Control": "no-store"})

    routes = [
        Route("/", root, methods=["GET"]), Route("/login", login_page, methods=["GET"]),
        Route("/assets/{name:str}", asset, methods=["GET"]),
        Route("/api/v1/session", create_session, methods=["POST"]),
        Route("/api/v1/session", get_session, methods=["GET"]),
        Route("/api/v1/session", delete_session, methods=["DELETE"]),
        Route("/api/v1/capabilities", capabilities, methods=["GET"]),
        Route("/api/v1/data/alpaca/prerequisites", download_prerequisites, methods=["GET"]),
        Route("/api/v1/library", library_api, methods=["GET"]),
        Route("/api/v1/documents/{document_id:str}", document_api, methods=["GET"]),
        Route("/library", shell, methods=["GET"]), Route("/library/{document_id:str}", shell, methods=["GET"]),
        Route("/research", shell, methods=["GET"]),
        Route("/research/new/run", shell, methods=["GET"]),
        Route("/research/new/lab", shell, methods=["GET"]),
        Route("/research/new/comparison", shell, methods=["GET"]),
        Route("/research/jobs/{job_id:str}", shell, methods=["GET"]),
        Route("/research/{artifact_id:str}", shell, methods=["GET"]),
        Route("/data", shell, methods=["GET"]),
        Route("/data/new", shell, methods=["GET"]),
        Route("/data/download/alpaca", shell, methods=["GET"]),
        Route("/data/downloads/{artifact_id:str}", shell, methods=["GET"]),
        Route("/data/{artifact_id:str}", shell, methods=["GET"]), Route("/system", shell, methods=["GET"]),
        Route("/system/benchmarks", shell, methods=["GET"]),
        Route("/system/connections", shell, methods=["GET"]),
    ] + connection_routes(session_for) + benchmark_routes(jobs, session_for) + test_routes(jobs, session_for) + comparison_routes(Catalog(Path(repository)), session_for) + artifact_routes(catalog, session_for) + job_routes(jobs, session_for) + form_routes(Catalog(Path(repository)), session_for) + dataset_routes(Catalog(Path(repository)), session_for)
    class BoundaryMiddleware:
        def __init__(self, app) -> None:
            self.app = app

        async def __call__(self, scope, receive, send) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            request = Request(scope, receive=receive)

            async def secured_send(message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers["X-Content-Type-Options"] = "nosniff"
                    headers["Referrer-Policy"] = "no-referrer"
                    headers["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
                        "font-src 'none'; connect-src 'self'; frame-src 'none'; frame-ancestors 'none'; "
                        "object-src 'none'; base-uri 'none'; form-action 'self'")
                await send(message)

            try:
                host = unique_header(request.scope, b"host")
                if host != expected_host:
                    raise SecurityError("invalid_host", 400, "Invalid Host header.")
                origin = unique_header(request.scope, b"origin")
                fetch_site = unique_header(request.scope, b"sec-fetch-site")
                unique_header(request.scope, b"cookie")
                if origin is not None and origin != expected_origin:
                    raise SecurityError("same_origin_required", 403, "Same-origin request required.")
                if fetch_site == "cross-site" or (fetch_site == "none" and request.url.path not in {
                        "/", "/login", "/library", "/research", "/data", "/system",
                        "/system/benchmarks", "/system/connections"} and
                        not request.url.path.startswith(("/library/", "/research/", "/data/"))):
                    raise SecurityError("same_origin_required", 403, "Same-origin request required.")
                if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin != expected_origin:
                    raise SecurityError("same_origin_required", 403, "Same-origin request required.")
                await self.app(scope, receive, secured_send)
                return
            except SecurityError as exc:
                response = error(exc.code, exc.status, exc.message)
            except SafeReadError as exc:
                response = error(exc.code, exc.status, exc.message)
            await response(scope, receive, secured_send)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            # Construction and browsing are idle. Only explicit job operations
            # initialize scheduling; shutdown still cleans up any owned worker.
            await run_in_threadpool(jobs.close)

    app = Starlette(routes=routes, middleware=[Middleware(BoundaryMiddleware)], lifespan=lifespan)
    app.state.session_controller = controller
    app.state.document_library = library
    app.state.catalog = catalog
    app.state.jobs = jobs

    return app
