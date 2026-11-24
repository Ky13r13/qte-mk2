"""Authenticated reads of operator-published desk data. Replay has its own namespace."""
import time

from starlette.responses import JSONResponse
from starlette.routing import Route

from qte.json_input import strict_json
from qte.live_risk_quotes import risk_from_status
from qte.market_sessions import entry_window, schedule_from_dict
from .safeio import read_registered_file, SafeReadError
from .security import SecurityError

FILES = {"capture": ("capture/status.json", "schwab_live_status_v1"),
         "quality": ("quality.json", "qte-execution-quality-v1"),
         "risk": ("risk.json", "qte-option-position-risk-v1")}


def live_desk_routes(repository, authenticate):
    async def read(request):
        authenticate(request, token=True)
        name = request.path_params["name"]
        demo = "/demo/" in request.url.path
        if name not in {*FILES, "session", "live-risk"} or request.query_params or (demo and name == "live-risk"):
            raise SecurityError("not_found", 404, "Unknown live desk resource.")
        prefix = "build/live-desk/demo/" if demo else "build/live-desk/"
        def load(path, schema=None):
            data = strict_json(read_registered_file(repository, prefix + path))
            if type(data) is not dict or (schema and data.get("schema") != schema):
                raise ValueError("Unexpected snapshot schema")
            return data
        try:
            if name == "live-risk":
                settings = load("risk-settings.json", "qte-live-risk-settings-v1")
                status = load(*FILES["capture"])
                data = risk_from_status(settings, status, time.time_ns())
            elif name == "session":
                data = load("session.json")
                if not demo:
                    schedule = schedule_from_dict(data.pop("schedule"))
                    now = time.time_ns()
                    data = dict(entry_window(schedule, **data, now_ns=now), asof_ns=now)
            else:
                data = load(*FILES[name])
        except SafeReadError as exc:
            raise SecurityError(exc.code, exc.status, "No readable " + name + " publication.") from None
        except (ValueError, KeyError, TypeError):
            raise SecurityError("invalid_artifact", 422, "Invalid " + name + " data. Check contract expiry and schedule coverage.") from None
        return JSONResponse(data, headers={"Cache-Control": "no-store"})
    return [Route("/api/v1/live-desk/{name:str}", read, methods=["GET"]),
            Route("/api/v1/live-desk/demo/{name:str}", read, methods=["GET"])]
