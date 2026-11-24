"""Authenticated local routes for explicit agent and offline research runs."""
from starlette.responses import JSONResponse
from starlette.routing import Route

from .dto import artifact_value
from .requests import json_request_one_of
from .security import SecurityError


def agent_routes(runs, authenticate):
    def check(request):
        authenticate(request, token=True)
        if runs is None:
            raise SecurityError("platform_unavailable", 503, "Research jobs are unavailable on this host.")
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")

    def response(payload, status=200):
        return JSONResponse({"schema_version": 1, **artifact_value(payload)},
                            status_code=status, headers={"Cache-Control": "no-store"})

    async def status(request):
        check(request)
        return response(runs.status())

    async def start(request):
        check(request)
        payload = await json_request_one_of(request, (({"roles"}, 1024), ({"roles", "experiment"}, 1024)))
        return response(runs.start(payload["roles"], payload.get("experiment", "straddle")), 202)

    async def detail(request):
        check(request)
        return response(runs.get(request.path_params["run_id"]))

    return [
        Route("/api/v1/agent-research/status", status, methods=["GET"]),
        Route("/api/v1/agent-research/run", start, methods=["POST"]),
        Route("/api/v1/agent-research/runs/{run_id:str}", detail, methods=["GET"]),
    ]
