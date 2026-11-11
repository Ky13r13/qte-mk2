import asyncio
import json

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from qte.gui.gamma_routes import gamma_routes
from qte.gui.security import SecurityError


def _payload():
    return {"schema_version": 1, "forward": 5000, "strike": 5000,
            "volatility_pct": 20, "days": 30, "rate_pct": 5,
            "multiplier": 50, "quantity": 1, "option_type": "call", "span_pct": 10}


def _app():
    def authenticate(request: Request, *, token: bool):
        if request.headers.get("x-test-auth") != "yes":
            raise SecurityError("authentication_required", 401, "Authentication required.")
        assert token is True

    async def security_error(_, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status, headers={"Cache-Control": "no-store"})

    return Starlette(routes=gamma_routes(authenticate),
                     exception_handlers={SecurityError: security_error})


def test_route_requires_auth_rejects_query_and_returns_private_bounded_result():
    async def scenario():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/v1/gamma/surface", json=_payload())).status_code == 401
            headers = {"X-Test-Auth": "yes"}
            query = await client.post("/api/v1/gamma/surface?x=1", headers=headers, json=_payload())
            assert query.status_code == 400
            response = await client.post("/api/v1/gamma/surface", headers=headers, json=_payload())
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            body = response.json()
            assert body["mode"] == "model_only"
            assert body["model"] == "black76_european"
            assert len(body["surface"]["gamma"]) == 25
            assert len(body["surface"]["gamma"][0]) == 41
    asyncio.run(scenario())


def test_route_uses_strict_fixed_json_and_performs_no_external_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("calculator must not perform external I/O")

    monkeypatch.setattr(httpx, "get", forbidden)

    async def scenario():
        transport = httpx.ASGITransport(app=_app())
        headers = {"X-Test-Auth": "yes", "Content-Type": "application/json"}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            url = "/api/v1/gamma/surface"
            bad = [
                ({**_payload(), "unknown": 1}, 400),
                ({**_payload(), "forward": True}, 400),
                ({**_payload(), "quantity": 1.5}, 400),
            ]
            for payload, status in bad:
                assert (await client.post(url, headers=headers, json=payload)).status_code == status
            duplicate = json.dumps(_payload())[:-1] + ',"forward":5000}'
            assert (await client.post(url, headers=headers, content=duplicate)).status_code == 400
            nonfinite = json.dumps(_payload()).replace('5000', '1e309', 1)
            assert (await client.post(url, headers=headers, content=nonfinite)).status_code == 400
            huge = {**_payload(), "forward": 10 ** 1000}
            huge_response = await client.post(url, headers=headers, json=huge)
            assert huge_response.status_code == 400
            assert huge_response.json()["error"]["code"] == "invalid_request"
            assert (await client.post(url, headers=headers, content=b"{}" * 1100)).status_code == 413
            assert (await client.post(url, headers={"X-Test-Auth": "yes"}, content=b"{}")).status_code == 415
            assert (await client.get(url, headers=headers)).status_code == 405
    asyncio.run(scenario())
