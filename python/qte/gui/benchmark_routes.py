"""Explicit bounded synthetic benchmark controls; passive reads never run work."""
import asyncio
import json
import re
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .benchmark_inputs import prepare_benchmark, validate_parameters
from .benchmark_evidence import bound_evidence, projection
from .dto import artifact_value
from .jobs import JobError
from .requests import json_request, pagination
from .security import SecurityError


def _record(manager, job_id):
    if type(job_id) is not str or re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
        raise JobError("job_not_found", 404, "Benchmark unavailable.")
    manager._open()
    with manager._connect() as db:
        row = db.execute("SELECT * FROM gui_jobs WHERE id=? AND kind='benchmark'", (job_id,)).fetchone()
    if row is None:
        raise JobError("job_not_found", 404, "Benchmark unavailable.")
    return dict(row)


def list_benchmarks(manager, offset, limit):
    manager._open()
    with manager._connect() as db:
        total = db.execute("SELECT count(*) FROM gui_jobs WHERE kind='benchmark'").fetchone()[0]
        rows = db.execute("SELECT * FROM gui_jobs WHERE kind='benchmark' ORDER BY sequence DESC LIMIT ? OFFSET ?",
                          (limit, offset)).fetchall()
    return {"items": [artifact_value(manager._dto(row)) for row in rows], "total": total, "offset": offset, "limit": limit}


def benchmark_detail(manager, job_id):
    row = _record(manager, job_id)
    result = {"job": artifact_value(manager._dto(row)), "evidence": {"state": "unavailable"}}
    if row["state"] not in {"succeeded", "failed", "canceled", "interrupted"}:
        return result
    try:
        if row["output_path"] != f"build/gui/jobs/{job_id}/artifacts":
            raise ValueError
        hashes = json.loads(row["snapshot"])["snapshot_hashes"]
        value = bound_evidence(manager.repository, job_id, hashes, row["source_identity"])
        shown = projection(value)
        if (_record(manager, job_id) != row
                or bound_evidence(manager.repository, job_id, hashes, row["source_identity"]) != value):
            raise ValueError
        result["evidence"] = shown
    except Exception:
        pass  # Invalid/partial output and raw errors are never exposed.
    return result


def benchmark_routes(manager, authenticate):
    lock = asyncio.Lock()

    async def call(method, *args):
        try:
            return await run_in_threadpool(method, *args)
        except JobError as exc:
            raise SecurityError(exc.code, exc.status, exc.message) from None

    def response(**value):
        return JSONResponse({"schema_version": 1, **value}, headers={"Cache-Control": "no-store"})

    async def listing(request):
        authenticate(request, token=True)
        return response(**await call(list_benchmarks, manager, *pagination(request)))

    def launch(payload):
        try:
            prepared = prepare_benchmark(manager.repository, payload["bars"], payload["strategy"])
        except Exception:
            raise JobError("benchmark_preflight_unavailable", 409, "Benchmark prerequisites are unavailable.") from None
        return manager.submit(uuid4().hex, "benchmark", prepared, payload["idempotency_key"], payload["timeout_seconds"])

    async def submit(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        payload = await json_request(request, {"bars", "strategy", "timeout_seconds", "idempotency_key"})
        try:
            validate_parameters(payload["bars"], payload["strategy"])
            if (type(payload["timeout_seconds"]) is not int or not 1 <= payload["timeout_seconds"] <= 1800
                    or type(payload["idempotency_key"]) is not str
                    or re.fullmatch(r"[A-Za-z0-9_-]{16,128}", payload["idempotency_key"]) is None):
                raise ValueError
        except ValueError:
            raise SecurityError("invalid_request", 400, "Invalid bounded benchmark request.") from None
        async with lock:
            job = await call(launch, payload)
        return response(job=artifact_value(job))

    async def detail(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        return response(**await call(benchmark_detail, manager, request.path_params["job_id"]))

    prefix = "/api/v1/system/benchmarks"
    return [Route(prefix, listing, methods=["GET"]), Route(prefix, submit, methods=["POST"]),
            Route(prefix + "/{job_id:str}", detail, methods=["GET"])]
