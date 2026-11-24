"""Authenticated access to the daily agent's published research briefs."""
import re
from starlette.responses import JSONResponse
from starlette.routing import Route
from qte.json_input import strict_json
from .safeio import read_registered_file, SafeReadError
from .security import SecurityError


def daily_brief_routes(repository, authenticate):
    async def read(request):
        authenticate(request, token=True)
        name = request.path_params["name"]
        if request.query_params or (name != "index" and not re.fullmatch("[0-9a-f]{64}", name)):
            raise SecurityError("not_found", 404, "Unknown daily brief.")
        try:
            data = strict_json(read_registered_file(repository, f"build/daily-briefs/{name}.json"))
            expected = "qte-daily-brief-index-v1" if name == "index" else "qte-daily-brief-v1"
            if not isinstance(data, dict) or data.get("schema") != expected:
                raise ValueError("Invalid brief")
        except SafeReadError as exc:
            raise SecurityError(exc.code, exc.status, "Daily brief unavailable.") from None
        except (ValueError, UnicodeError):
            raise SecurityError("invalid_artifact", 422, "Invalid daily brief publication.") from None
        return JSONResponse(data, headers={"Cache-Control": "no-store"})
    return [Route("/api/v1/daily-brief/{name:str}", read, methods=["GET"])]
