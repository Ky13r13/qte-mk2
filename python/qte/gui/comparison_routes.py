"""Fixed comparison preview: no data-derived preview facts or job side effects."""
from __future__ import annotations

import asyncio

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .comparison_inputs import prepare_comparison_inputs
from .comparison_forms import comparison_form_options
from .job_inputs import JobInputError
from .requests import json_request
from .security import SecurityError


# Keeps the full declared slate possible without enlarging ordinary form envelopes.
COMPARISON_REQUEST_LIMIT = 1024 * 1024 + 4096


def comparison_routes(catalog, authenticate):
    lock = asyncio.Lock()

    async def options(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        return JSONResponse(comparison_form_options(), headers={"Cache-Control": "no-store"})

    async def preview(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        payload = await json_request(request, {"kind", "configuration"}, limit=COMPARISON_REQUEST_LIMIT)
        if payload["kind"] != "comparison_run":
            raise SecurityError("invalid_request", 400, "Invalid comparison request.")
        async with lock:
            try:
                prepared = await run_in_threadpool(prepare_comparison_inputs, catalog, payload["configuration"])
            except JobInputError as exc:
                raise SecurityError(exc.code, exc.status, exc.message) from None
        return JSONResponse({"schema_version": 1, "kind": "comparison_run",
            "preview_fingerprint": prepared.fingerprint, "holdout_history": "unknown",
            "assumptions": ["Validation checks consistency, not historical authenticity.",
                            "Final-test contents and outcomes remain withheld.",
                            "Preview does not reserve inputs or launch a strategy."]},
            headers={"Cache-Control": "no-store"})

    return [Route("/api/v1/comparisons/form-options", options, methods=["GET"]),
            Route("/api/v1/comparisons/preview", preview, methods=["POST"])]
