"""Authenticated route for the bounded offline gamma calculator."""
from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.routing import Route

from qte.gamma_surface import GammaInputError, calculate_gamma_surface

from .requests import json_request
from .security import SecurityError

_REQUEST_FIELDS = {
    "forward", "strike", "volatility_pct", "days", "rate_pct", "multiplier",
    "quantity", "option_type", "span_pct",
}


def gamma_routes(authenticate):
    async def surface(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        payload = await json_request(request, _REQUEST_FIELDS, limit=2048)
        try:
            result = calculate_gamma_surface(payload)
        except GammaInputError:
            raise SecurityError("invalid_request", 400, "Invalid gamma calculator input.") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return [Route("/api/v1/gamma/surface", surface, methods=["POST"])]
