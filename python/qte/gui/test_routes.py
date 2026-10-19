"""Explicit fixed test controls; no browser-supplied paths or executable code."""
import asyncio
import re
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .dto import artifact_value
from .jobs import JobError
from .requests import json_request, pagination
from .security import SecurityError
from .test_preflight import prepare_test_run
from . import test_views


def test_routes(manager, authenticate):
    launch_lock = asyncio.Lock()

    async def call(method, *args):
        try:
            return await run_in_threadpool(method, *args)
        except JobError as exc:
            raise SecurityError(exc.code, exc.status, exc.message) from None

    def response(**values):
        return JSONResponse({"schema_version": 1, **values}, headers={"Cache-Control": "no-store"})

    def no_query(request):
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")

    def launch(payload):
        try:
            prepared = prepare_test_run(manager.repository, payload["runner"])
        except Exception:
            raise JobError("test_preflight_unavailable", 409, "Fixed test prerequisites are unavailable.") from None
        return manager.submit(uuid4().hex, "test_run", prepared,
                              payload["idempotency_key"], payload["timeout_seconds"])

    async def listing(request):
        authenticate(request, token=True)
        offset, limit = pagination(request)
        return response(**await call(test_views.list_tests, manager, offset, limit))

    async def submit(request):
        authenticate(request, token=True)
        no_query(request)
        payload = await json_request(request, {"runner", "idempotency_key", "timeout_seconds"})
        if (type(payload["runner"]) is not str or payload["runner"] not in {"pytest", "ctest"}
                or type(payload["timeout_seconds"]) is not int or not 1 <= payload["timeout_seconds"] <= 1800
                or not isinstance(payload["idempotency_key"], str)
                or re.fullmatch(r"[A-Za-z0-9_-]{16,128}", payload["idempotency_key"]) is None):
            raise SecurityError("invalid_request", 400, "Invalid fixed test request.")
        async with launch_lock:
            job = await call(launch, payload)
        return response(job=artifact_value(job))

    async def detail(request):
        authenticate(request, token=True)
        offset, limit = pagination(request)
        return response(**await call(test_views.test_detail, manager, request.path_params["job_id"], offset, limit))

    async def reveal(request):
        authenticate(request, token=True)
        no_query(request)
        payload = await json_request(request, {"confirmation"})
        if payload["confirmation"] != "reveal outside protocol":
            raise SecurityError("invalid_request", 400, "Explicit test evidence reveal confirmation is required.")
        return response(**await call(test_views.reveal_test, manager, request.path_params["job_id"]))

    async def current(request):
        authenticate(request, token=True)
        no_query(request)
        await json_request(request, set())
        return response(**await call(test_views.check_current, manager, request.path_params["job_id"]))

    prefix = "/api/v1/system/tests"
    return [Route(prefix, listing, methods=["GET"]), Route(prefix, submit, methods=["POST"]),
            Route(prefix + "/{job_id:str}", detail, methods=["GET"]),
            Route(prefix + "/{job_id:str}/reveal", reveal, methods=["POST"]),
            Route(prefix + "/{job_id:str}/check-current", current, methods=["POST"])]


test_routes.__test__ = False
