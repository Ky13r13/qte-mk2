"""Recorder replay to risk, cutoff, quality report and authenticated desk."""
import asyncio
from pathlib import Path

import httpx
import pytest

from qte.gui.app import create_app
from qte.live_demo import build_demo
from qte.json_input import strict_json

ROOT = Path(__file__).resolve().parents[2]


def test_demo_replays_through_real_apis_without_a_broker(tmp_path):
    pytest.importorskip("exchange_calendars")
    output = tmp_path / "build/live-desk/demo"
    build_demo(ROOT, output)
    other = build_demo(ROOT, tmp_path / "independent")
    for filename in ("quality.json","risk.json","capture/quotes.ndjson"):
        assert (output / filename).read_bytes() == (other / filename).read_bytes()
    report = strict_json((output / "quality.json").read_bytes())
    assert [o["status"] for o in report["orders"]] == [
        "paper_filled","paper_filled","insufficient_displayed_size","no_visible_quote"]
    with pytest.raises(ValueError,match="already exists"):
        build_demo(ROOT,output)
    app = create_app(tmp_path, code_sink=lambda _: None)
    async def query():
        origin = "http://127.0.0.1:8765"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=origin) as client:
            prefix = "/api/v1/live-desk/demo/"
            assert (await client.get(prefix+"quality")).status_code == 401
            login = await client.post("/api/v1/session",headers={"Origin":origin},json={
                "schema_version":1,"code":app.state.session_controller.access_code_for_testing})
            auth = {"X-QTE-Token":login.json()["request_token"]}
            for name in ("capture","session","quality","risk"):
                resp = await client.get(prefix+name,headers=auth)
                assert resp.status_code == 200, resp.text
            assert resp.json()["portfolio"]["liquidation_usd"] == 730
            assert (await client.get("/api/v1/live-desk/quality",headers=auth)).status_code == 404
            assert (await client.get("/research/live-desk?demo=1")).status_code == 200
            assert (await client.get("/assets/live_desk.js")).status_code == 200
    asyncio.run(query())
