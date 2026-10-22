"""Authenticated, offline-only broker connection routes."""
from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.routing import Route

from .connections import connection_status
from .requests import json_request
from .security import SecurityError

_OPERATION = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_ROBINHOOD_OPERATIONS = frozenset({"accounts", "trading_pairs", "holdings", "orders"})


def connection_routes(authenticate):
    """Build routes that describe prerequisites but can never perform a read."""

    async def status(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        return JSONResponse(connection_status(), headers={"Cache-Control": "no-store"})

    async def read(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        connection_id = request.path_params["connection_id"]
        if connection_id not in {"schwab", "robinhood_crypto"}:
            raise SecurityError("connection_not_found", 404, "Connection unavailable.")
        payload = await json_request(request, {"operation"}, limit=1024)
        operation = payload["operation"]
        if (type(operation) is not str or len(operation) > 160
                or _OPERATION.fullmatch(operation) is None):
            raise SecurityError("invalid_operation", 400, "Invalid connection operation.")
        if connection_id == "robinhood_crypto" and operation not in _ROBINHOOD_OPERATIONS:
            raise SecurityError("invalid_operation", 400, "Invalid connection operation.")
        raise SecurityError("broker_reads_disabled", 409, "Broker reads are disabled.")

    prefix = "/api/v1/connections"
    return [
        Route(prefix, status, methods=["GET"]),
        Route(prefix + "/{connection_id:str}/read", read, methods=["POST"]),
    ]
