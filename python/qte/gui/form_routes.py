"""Read-only form metadata and authorized previews; no jobs start here."""
from __future__ import annotations

import asyncio
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .artifacts import ArtifactError, strict_json
from .dto import artifact_value
from .form_inputs import capture_form_inputs
from .job_inputs import JobInputError
from .requests import json_request
from .research_forms import FormError, form_options, prepare_form
from .security import SecurityError


def form_routes(catalog, authenticate):
    # One bounded preview at a time, on its own Catalog verification cache.
    lock = asyncio.Lock()

    def response(values, *, status=200):
        projected = artifact_value(values)
        # Versions are control-plane fields, never simulation numeric inputs.
        projected["schema_version"] = 1
        if "config_origin" in projected:
            projected["config_origin"]["form_schema_version"] = 1
        if projected.get("kind") == "strategy_lab":
            projected["effective_configuration"]["schema_version"] = 1
        return JSONResponse(projected, status_code=status, headers={"Cache-Control": "no-store"})

    def no_query(request):
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")

    def preview(payload):
        captured = capture_form_inputs(catalog.repository, payload["kind"], payload["config_origin"],
                                       payload["configuration"])
        # Authorization must precede even invalid financial/data diagnostics.
        lineage = catalog.authorize_job_inputs(captured)
        result = prepare_form(captured.kind, strict_json(captured.configuration_bytes), captured.data_bytes)
        return {**result, "captured_config_sha256": captured.config_sha256,
                "data_sha256": captured.data_sha256, "lineage": lineage}

    async def options(request):
        authenticate(request, token=True)
        no_query(request)
        return response(form_options())

    async def validate(request):
        authenticate(request, token=True)
        no_query(request)
        payload = await json_request(request, {"kind", "config_origin", "configuration"}, limit=16384)
        async with lock:
            try:
                result = await run_in_threadpool(preview, payload)
            except FormError as exc:
                return response({"error": {"code": exc.code, "field": exc.field,
                                           "message": exc.message, "request_id": uuid4().hex}}, status=400)
            except (JobInputError, ArtifactError) as exc:
                raise SecurityError(exc.code, exc.status, exc.message) from None
        return response(result)

    return [Route("/api/v1/research/form-options", options, methods=["GET"]),
            Route("/api/v1/configs/validate", validate, methods=["POST"])]
