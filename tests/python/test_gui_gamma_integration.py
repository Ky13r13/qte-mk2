"""Gamma calculator integration: real session and platform boundaries."""
import asyncio
from pathlib import Path

import httpx
import pytest

import qte.gui.app as app_module


@pytest.mark.parametrize("full_gui", [True, False])
def test_gamma_is_authenticated_offline_and_available_on_limited_hosts(monkeypatch, full_gui):
    monkeypatch.setattr(app_module, "full_gui_supported", lambda: full_gui)
    app = app_module.create_app(Path(__file__).parents[2], code_sink=lambda _: None)
    origin = "http://127.0.0.1:8765"
    payload = dict(schema_version=1, forward=20000, strike=20000,
                   volatility_pct=20, days=7, rate_pct=4, multiplier=20,
                   quantity=1, option_type="call", span_pct=10)

    async def scenario():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as client:
                assert (await client.get("/gamma")).status_code == 303
                assert (await client.post("/api/v1/gamma/surface", headers={"Origin": origin}, json=payload)).status_code == 401
                login = await client.post("/api/v1/session", headers={"Origin": origin}, json={
                    "schema_version": 1, "code": app.state.session_controller.access_code_for_testing})
                assert login.status_code == 200
                auth = {"X-QTE-Token": login.json()["request_token"]}
                assert (await client.get("/gamma", headers={"Sec-Fetch-Site": "none"})).status_code == 200
                asset = await client.get("/assets/gamma.js")
                assert asset.status_code == 200 and "createGammaLab" in asset.text
                capabilities = (await client.get("/api/v1/capabilities", headers=auth)).json()["capabilities"]
                assert next(item for item in capabilities if item["id"] == "gamma_calculator")["state"] == "available"
                assert (await client.post("/api/v1/gamma/surface", headers=auth, json=payload)).status_code == 403
                assert (await client.post("/api/v1/gamma/surface", headers={**auth, "Origin": "http://evil.invalid"}, json=payload)).status_code == 403
                result = await client.post("/api/v1/gamma/surface", headers={**auth, "Origin": origin}, json=payload)
                assert result.status_code == 200
                assert result.headers["cache-control"] == "no-store"
                assert "connect-src 'self'" in result.headers["content-security-policy"]
                assert result.json()["mode"] == "model_only"
                assert result.json()["point"]["gamma"] > 0
    asyncio.run(scenario())
