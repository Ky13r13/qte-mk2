"""Authenticated, fixed-kind local jobs; no request supplies a command or code."""
from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .artifacts import ArtifactError, strict_json
from .catalog import Catalog
from .comparison_inputs import prepare_comparison_inputs
from .comparison_routes import COMPARISON_REQUEST_LIMIT
from .dto import artifact_value
from .form_inputs import capture_form_inputs
from .inline_inputs import prepare_inline_inputs
from .job_inputs import JobInputError, collect_inputs
from .jobs import JobError
from .requests import json_request, json_request_one_of, pagination
from .research_forms import FormError, form_identity, prepare_form
from .security import SecurityError


def job_routes(manager, authenticate):
    # Submission validation must not share the dispatcher's mutable catalog cache.
    catalog = Catalog(manager.repository)
    inline_lock = asyncio.Lock()
    async def call(method, *args):
        try:
            return await run_in_threadpool(method, *args)
        except (JobError, JobInputError, ArtifactError) as exc:
            raise SecurityError(exc.code, exc.status, exc.message) from None

    def response(*, status=200, **values):
        return JSONResponse({"schema_version": 1, **values}, status_code=status,
                            headers={"Cache-Control": "no-store"})

    def no_query(request):
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")

    def job_id(request):
        value = request.path_params["job_id"]
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            raise SecurityError("job_not_found", 404, "Job unavailable.")
        return value

    def submit_inline(payload):
        # This gate precedes capture, authorization, and all job storage so a
        # disabled network-capable kind has no observable submission side effect.
        if payload["kind"] == "download_alpaca":
            manager.require_download_enabled()
        captured = capture_form_inputs(manager.repository, payload["kind"], payload["config_origin"],
                                       payload["configuration"])
        catalog.authorize_job_inputs(captured)
        wire = strict_json(captured.configuration_bytes)
        identity = form_identity(captured.kind, wire, captured.data_sha256)
        if identity["preview_fingerprint"] != payload["preview_fingerprint"]:
            raise JobError("stale_preview", 409, "Inputs changed; preview the configuration again.")
        preview = prepare_form(captured.kind, wire, captured.data_bytes)
        prepared = prepare_inline_inputs(captured, preview)
        return manager.submit(uuid4().hex, captured.kind, prepared, payload["idempotency_key"],
                              payload["timeout_seconds"])

    def submit_comparison(payload):
        prepared = prepare_comparison_inputs(catalog, payload["configuration"])
        if prepared.fingerprint != payload["preview_fingerprint"]:
            raise JobError("stale_preview", 409, "Inputs changed; preview the configuration again.")
        return manager.submit(uuid4().hex, "comparison_run", prepared, payload["idempotency_key"],
                              payload["timeout_seconds"])

    async def submit(request, *, comparison_request=False):
        authenticate(request, token=True)
        no_query(request)
        envelopes = (
            ({"kind", "configuration", "preview_fingerprint", "idempotency_key", "timeout_seconds"}, COMPARISON_REQUEST_LIMIT),
        ) if comparison_request else (
            ({"kind", "config_path", "idempotency_key", "timeout_seconds"}, 4096),
            ({"kind", "config_origin", "configuration", "preview_fingerprint", "idempotency_key", "timeout_seconds"}, 16384),
        )
        payload = await json_request_one_of(request, envelopes)
        key, timeout = payload["idempotency_key"], payload["timeout_seconds"]
        if (not isinstance(key, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key) is None
                or type(timeout) is not int or not 1 <= timeout <= 1800):
            raise SecurityError("invalid_request", 400, "Invalid local research job request.")
        if "configuration" in payload:
            comparison = payload["kind"] == "comparison_run"
            if comparison != comparison_request or comparison == ("config_origin" in payload):
                raise SecurityError("invalid_request", 400, "Invalid local research job request.")
            fingerprint = payload["preview_fingerprint"]
            if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
                raise SecurityError("invalid_request", 400, "Invalid local research job request.")
            async with inline_lock:
                try:
                    result = await call(submit_comparison if comparison else submit_inline, payload)
                except FormError as exc:
                    return response(status=400, error={"code": exc.code, "field": exc.field,
                                                       "message": exc.message, "request_id": uuid4().hex})
            return response(job=artifact_value(result))
        path = payload["config_path"]
        if payload["kind"] != "research_run" or not isinstance(path, str) or not 1 <= len(path) <= 512:
            raise SecurityError("invalid_request", 400, "Invalid local research job request.")
        prepared = await call(collect_inputs, manager.repository, path)
        result = await call(manager.submit, uuid4().hex, "research_run", prepared, key, timeout)
        return response(job=artifact_value(result))

    async def comparison_submit(request):
        return await submit(request, comparison_request=True)

    async def listing(request):
        authenticate(request, token=True)
        offset, limit = pagination(request)
        items, total = await call(manager.list, offset, limit)
        return response(items=artifact_value(items), total=total, offset=offset, limit=limit)

    async def detail(request):
        authenticate(request, token=True)
        no_query(request)
        return response(job=artifact_value(await call(manager.get, job_id(request))))

    async def cancel(request):
        authenticate(request, token=True)
        no_query(request)
        await json_request(request, set())
        return response(job=artifact_value(await call(manager.cancel, job_id(request))))

    async def log(request):
        authenticate(request, token=True)
        query = request.query_params
        if any(key not in {"offset", "limit"} or len(query.getlist(key)) != 1 for key in query):
            raise SecurityError("invalid_query", 400, "Invalid job log query.")
        offset, limit = query.get("offset", "0"), query.get("limit", "65536")
        if (not re.fullmatch(r"[0-9]{1,5}", offset) or not re.fullmatch(r"[0-9]{1,5}", limit)
                or not 0 <= int(offset) <= 65536 or not 1 <= int(limit) <= 65536):
            raise SecurityError("invalid_query", 400, "Invalid job log bounds.")
        result = await call(manager.log, job_id(request), int(offset), int(limit))
        return response(**result)

    return [Route("/api/v1/jobs", submit, methods=["POST"]),
            Route("/api/v1/comparisons/jobs", comparison_submit, methods=["POST"]),
            Route("/api/v1/jobs", listing, methods=["GET"]),
            Route("/api/v1/jobs/{job_id:str}", detail, methods=["GET"]),
            Route("/api/v1/jobs/{job_id:str}/log", log, methods=["GET"]),
            Route("/api/v1/jobs/{job_id:str}/cancel", cancel, methods=["POST"])]
