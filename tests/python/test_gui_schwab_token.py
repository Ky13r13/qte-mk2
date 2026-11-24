import asyncio
import hashlib
import json
import os
import time

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from qte.brokers import schwab_auth
from qte.gui.schwab_token_routes import CollectJob, schwab_token_routes
from qte.gui.security import SecurityError

KEY, SECRET = "app-key-123", "app-secret-xyz-SENSITIVE"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "AGENTS.md").write_text("")
    store = schwab_auth.TokenStore(tmp_path)
    token = dict(access_token="old-access", refresh_token="refresh-1", token_type="Bearer", expires_in=1800)
    store.save(dict(schema_version=1, app_sha256=schwab_auth.app_fingerprint(KEY), callback=schwab_auth.CALLBACK,
                    obtained_at=1000.0, expires_at=2800.0, token=token))
    return tmp_path


def make_app(repo, transport, clock, job=None):
    def authenticate(request: Request, *, token: bool):
        if request.headers.get("x-test-auth") != "yes":
            raise SecurityError("authentication_required", 401, "Authentication required.")

    async def handler(_, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)
    routes = schwab_token_routes(repo, authenticate, transport=transport, clock=clock, collect_job=job)
    return Starlette(routes=routes, exception_handlers={SecurityError: handler})


def run(app, coro):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"X-Test-Auth": "yes"}) as client:
            return await coro(client)
    return asyncio.run(go())


def fake_transport(calls, status=200):
    def transport(request):
        calls.append(request)
        body = json.dumps(dict(access_token="new-access", token_type="Bearer", expires_in=1800)).encode()
        return schwab_auth.ReadResponse(status, body)
    return transport


def test_status_reports_expiry_only_and_requires_auth(repo):
    app = make_app(repo, None, clock=lambda: 2000.0)

    async def scenario(client):
        ok = await client.get("/api/v1/schwab/token")
        denied = await client.get("/api/v1/schwab/token", headers={"X-Test-Auth": "no"})
        return ok, denied
    ok, denied = run(app, scenario)
    assert denied.status_code == 401
    body = ok.json()
    assert body["token"] == dict(present=True, expires_at=2800.0, seconds_left=800, expired=False)
    assert "access" not in ok.text and "refresh-1" not in ok.text


def test_refresh_exchanges_once_saves_and_never_echoes_secrets(repo, caplog):
    calls = []
    app = make_app(repo, fake_transport(calls), clock=lambda: 5000.0)

    async def scenario(client):
        return await client.post("/api/v1/schwab/token/refresh",
                                 json=dict(schema_version=1, app_key=KEY, app_secret=SECRET))
    response = run(app, scenario)
    assert response.status_code == 200, response.text
    assert response.json()["refreshed"] is True and response.json()["expires_at"] == 6800.0
    assert len(calls) == 1 and b"refresh-1" in calls[0].body
    saved = schwab_auth.TokenStore(repo).load()
    assert saved["token"]["access_token"] == "new-access" and saved["token"]["refresh_token"] == "refresh-1"
    on_disk = b"".join(p.read_bytes() for p in (repo / ".cache").rglob("*") if p.is_file())
    assert SECRET.encode() not in on_disk and SECRET not in response.text and SECRET not in caplog.text


def test_wrong_key_bad_shape_and_provider_failure_change_nothing(repo):
    calls = []
    app = make_app(repo, fake_transport(calls), clock=lambda: 5000.0)
    before = schwab_auth.TokenStore(repo).load()

    async def scenario(client):
        url = "/api/v1/schwab/token/refresh"
        wrong = await client.post(url, json=dict(schema_version=1, app_key="other-key", app_secret=SECRET))
        missing = await client.post(url, json=dict(schema_version=1, app_key=KEY))
        extra = await client.post(url, json=dict(schema_version=1, app_key=KEY, app_secret=SECRET, x=1))
        blank = await client.post(url, json=dict(schema_version=1, app_key=" ", app_secret=SECRET))
        query = await client.post(url + "?x=1", json=dict(schema_version=1, app_key=KEY, app_secret=SECRET))
        return wrong, missing, extra, blank, query
    wrong, missing, extra, blank, query = run(app, scenario)
    assert wrong.status_code == 409 and "differs" in wrong.json()["error"]["message"]
    assert (missing.status_code, extra.status_code, blank.status_code, query.status_code) == (400, 400, 400, 400)
    assert calls == [] and schwab_auth.TokenStore(repo).load() == before
    assert SECRET not in wrong.text

    failing = make_app(repo, fake_transport([], status=401), clock=lambda: 5000.0)

    async def provider(client):
        return await client.post("/api/v1/schwab/token/refresh",
                                 json=dict(schema_version=1, app_key=KEY, app_secret=SECRET))
    denied = run(failing, provider)
    assert denied.status_code == 409 and SECRET not in denied.text
    assert schwab_auth.TokenStore(repo).load() == before


def test_collect_requires_a_valid_token_and_is_single_flight(repo):
    started = []

    def slow():
        started.append(1)
        time.sleep(0.2)
        return dict(requests=2, ok=2, errors=0, derived_series=1, capture_dir="data/truth/captures/schwab/x")
    job = CollectJob(repo, runner=slow)
    expired = make_app(repo, None, clock=lambda: 9999.0, job=job)

    async def blocked(client):
        return await client.post("/api/v1/schwab/collect", json=dict(schema_version=1))
    assert run(expired, blocked).status_code == 409 and started == []

    valid = make_app(repo, None, clock=lambda: 2000.0, job=job)

    async def twice(client):
        first = await client.post("/api/v1/schwab/collect", json=dict(schema_version=1))
        second = await client.post("/api/v1/schwab/collect", json=dict(schema_version=1))
        return first.json(), second.json()
    first, second = run(valid, twice)
    assert first["started"] is True and second["started"] is False
    for _ in range(50):
        if job.snapshot()["state"] == "done":
            break
        time.sleep(0.02)
    assert job.snapshot()["state"] == "done" and job.snapshot()["ok"] == 2 and started == [1]
