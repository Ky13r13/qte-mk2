import asyncio
from datetime import datetime
import json

import httpx
import pytest

from qte.daily_brief import ARCHIVE, PUBLIC, due_sessions, publish_archive
from qte.gui.app import create_app


def capture(repo, day="2025-11-26", capture_id="hood-01"):
    path = repo/ARCHIVE/day/capture_id/"summary.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(session=day, prior_known_regime=dict(asof="2025-11-25", regime="range", reason="Mixed trend."),
                                    retrospective_outcome="Positive close", metrics={"SPX": {"last": 7000.0}}, daily_status="Observed cash-session bars.",
                                    events=[dict(time="2025-11-26T08:30:00-05:00", source="https://www.bls.gov/", event="Scheduled release.")])))
    return path


def test_completed_session_gap_and_half_day(tmp_path):
    capture(tmp_path)
    publish_archive(tmp_path)
    def at(text): return datetime.fromisoformat(text)
    assert due_sessions(tmp_path, at("2025-11-27T18:00:00-05:00")) == []
    assert due_sessions(tmp_path, at("2025-11-28T12:59:59-05:00")) == []
    assert due_sessions(tmp_path, at("2025-11-28T13:00:00-05:00")) == ["2025-11-28"]
    capture(tmp_path, "2025-12-01")
    publish_archive(tmp_path)
    assert due_sessions(tmp_path, at("2025-12-02T16:00:00-05:00")) == ["2025-11-28", "2025-12-02"]


def test_atomic_index_preserved_on_invalid_capture(tmp_path):
    capture(tmp_path)
    index = publish_archive(tmp_path)
    before = index.read_bytes()
    assert publish_archive(tmp_path).read_bytes() == before
    p = capture(tmp_path, capture_id="hood-02")
    data = json.loads(p.read_bytes())
    data["prior_known_regime"]["asof"] = data["session"]
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="predate"):
        publish_archive(tmp_path)
    assert index.read_bytes() == before


def test_authenticated_archive_read(tmp_path):
    capture(tmp_path)
    capture(tmp_path, capture_id="hood-02")
    publish_archive(tmp_path)
    app = create_app(tmp_path, code_sink=lambda _: None)

    async def check():
        origin = "http://127.0.0.1:8765"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as client:
            base = "/api/v1/daily-brief/"
            assert (await client.get(base+"index")).status_code == 401
            login = await client.post("/api/v1/session", headers={"Origin": origin}, json={
                "schema_version": 1, "code": app.state.session_controller.access_code_for_testing})
            auth = {"X-QTE-Token": login.json()["request_token"]}
            entries = (await client.get(base+"index", headers=auth)).json()["entries"]
            assert [e["capture"] for e in entries] == ["hood-02", "hood-01"]
            saved = (await client.get(base+entries[0]["id"], headers=auth)).json()
            assert saved["reason"] == "Mixed trend." and saved["news"][0]["source"] == "https://www.bls.gov/"
            assert (await client.get(base+"private.txt", headers=auth)).status_code == 404
            assert (await client.get("/research/daily-brief")).status_code == 200
            assert (await client.get("/assets/daily_brief.js")).status_code == 200
    asyncio.run(check())
