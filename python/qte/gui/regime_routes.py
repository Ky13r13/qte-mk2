"""Read-only, authenticated views of the fixed local regime study export."""
import re

from starlette.responses import JSONResponse
from starlette.routing import Route

from .artifacts import strict_json
from .safeio import SafeReadError, read_registered_file
from .security import SecurityError


def regime_routes(repository, authenticate):
    async def read(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        name = request.path_params["name"]
        if not re.fullmatch(r"summary|intervals|manifest|year-[12][0-9]{3}|minute-[12][0-9]{3}-[01][0-9]-[0-3][0-9]|tail-(summary|manifest|day-[12][0-9]{3}-[01][0-9]-[0-3][0-9])", name):
            raise SecurityError("not_found", 404, "Unknown regime study resource.")
        relative = f"build/reversal-tail-study/{name[5:]}.json" if name.startswith("tail-") else f"build/regime-study/{name}.json"
        try:
            raw = read_registered_file(repository, relative)
            result = strict_json(raw)
        except SafeReadError as exc:
            raise SecurityError(exc.code, exc.status, "Regime study unavailable. Generate the local research report first.") from None
        except (ValueError, UnicodeError):
            raise SecurityError("invalid_artifact", 422, "Regime report is invalid.") from None
        if type(result) is not dict:
            raise SecurityError("invalid_artifact", 422, "Regime report must be an object.")
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return [Route("/api/v1/regime-study/{name:str}", read, methods=["GET"])]
