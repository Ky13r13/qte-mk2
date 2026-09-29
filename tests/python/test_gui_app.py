from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from qte.gui import create_app


REPOSITORY = Path(__file__).parents[2]
BASE_URL = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE_URL}


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


async def _login(client, app):
    response = await client.post("/api/v1/session", headers=ORIGIN, json={
        "schema_version": 1, "code": app.state.session_controller.access_code_for_testing,
    })
    assert response.status_code == 200
    return response.json()["request_token"]


def test_login_bootstrap_logout_and_shell_boundary():
    async def scenario():
        codes = []
        app = create_app(REPOSITORY, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with await _client(app) as client:
                assert (await client.get("/library")).status_code == 303
                token = await _login(client, app)
                assert len(codes) == 1
                replay = await client.post("/api/v1/session", headers=ORIGIN,
                    json={"schema_version": 1, "code": codes[0]})
                assert replay.status_code == 401
                missing_metadata = await client.get("/api/v1/session")
                assert missing_metadata.status_code == 403
                bootstrap = await client.get("/api/v1/session", headers={"Sec-Fetch-Site": "same-origin"})
                assert bootstrap.status_code == 200
                assert bootstrap.json()["request_token"] == token
                assert COOKIE_VALUE_NOT_PRESENT(bootstrap.text, client.cookies.get("qte_session"))
                logged_out = await client.delete("/api/v1/session", headers={**ORIGIN, "X-QTE-Token": token})
                assert logged_out.status_code == 204
                assert len(codes) == 2
                denied = await client.get("/api/v1/capabilities", headers={"X-QTE-Token": token})
                assert denied.status_code == 401
    asyncio.run(scenario())


def COOKIE_VALUE_NOT_PRESENT(body: str, cookie: str | None) -> bool:
    return cookie is None or cookie not in body


def test_login_request_schema_and_throttle_boundaries():
    bodies = [
        b'{"code":"x"}', b'{"schema_version":2,"code":"x"}',
        b'{"schema_version":true,"code":"x"}',
        b'{"schema_version":1,"code":"x","command":"x"}',
        b'{"schema_version":1,"schema_version":1,"code":"x"}', b'[]', b'{',
        b'{"schema_version":1,"code":NaN}',
    ]

    async def scenario():
        app = create_app(REPOSITORY, code_sink=lambda _: None)
        async with app.router.lifespan_context(app):
            async with await _client(app) as client:
                for body in bodies:
                    response = await client.post("/api/v1/session", headers={**ORIGIN, "Content-Type": "application/json"}, content=body)
                    assert response.status_code == 400
                wrong_type = await client.post("/api/v1/session", headers={**ORIGIN, "Content-Type": "text/plain"}, content="x")
                assert wrong_type.status_code == 415
                oversized = await client.post("/api/v1/session", headers={**ORIGIN, "Content-Type": "application/json"}, content=b"x" * 4097)
                assert oversized.status_code == 413
                for _ in range(5):
                    assert (await client.post("/api/v1/session", headers=ORIGIN, json={"schema_version": 1, "code": "wrong"})).status_code == 401
                assert (await client.post("/api/v1/session", headers=ORIGIN, json={"schema_version": 1, "code": "wrong"})).status_code == 429
    asyncio.run(scenario())


def test_host_origin_fetch_metadata_and_tokens_are_enforced():
    async def scenario():
        app = create_app(REPOSITORY, code_sink=lambda _: None)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
                wrong_host = await client.get("/login", headers={"Host": "attacker.invalid"})
                assert wrong_host.status_code == 400
                request = client.build_request("GET", "/login", headers=[("Host", "127.0.0.1:8765"), ("Host", "attacker.invalid")])
                assert (await client.send(request)).status_code == 400
                assert (await client.get("/login", headers={"Origin": "null"})).status_code == 403
                assert (await client.get("/login", headers={"Sec-Fetch-Site": "cross-site"})).status_code == 403
                assert (await client.get("/login", headers={"Sec-Fetch-Site": "none"})).status_code == 200
                assert (await client.get("/api/v1/session", headers={"Sec-Fetch-Site": "none"})).status_code == 403
                token = await _login(client, app)
                assert (await client.get("/api/v1/library")).status_code == 403
                assert (await client.get("/api/v1/library", headers={"X-QTE-Token": "wrong"})).status_code == 403
                assert (await client.get("/api/v1/library", headers={"X-QTE-Token": token})).status_code == 200
                assert (await client.delete("/api/v1/session", headers={"X-QTE-Token": token})).status_code == 403
                non_ascii_token = await client.get("/api/v1/library", headers=[(b"X-QTE-Token", "é".encode())])
                assert non_ascii_token.status_code == 400
                duplicate_token = client.build_request("GET", "/api/v1/library", headers=[
                    ("X-QTE-Token", token), ("X-QTE-Token", token)])
                assert (await client.send(duplicate_token)).status_code == 400
    asyncio.run(scenario())


def test_non_ascii_credentials_and_duplicate_headers_are_sanitized():
    async def scenario():
        app = create_app(REPOSITORY, code_sink=lambda _: None)
        async with app.router.lifespan_context(app):
            async with await _client(app) as client:
                for _ in range(5):
                    if _ < 4:
                        response = await client.post("/api/v1/session", headers=ORIGIN,
                            json={"schema_version": 1, "code": "é"})
                    else:
                        response = await client.post("/api/v1/session",
                            headers={**ORIGIN, "Content-Type": "application/json"},
                            content=b'{"schema_version":1,"code":"\\ud800"}')
                    assert response.status_code == 401
                assert (await client.post("/api/v1/session", headers=ORIGIN,
                    json={"schema_version": 1, "code": "é"})).status_code == 429

        app = create_app(REPOSITORY, code_sink=lambda _: None)
        async with app.router.lifespan_context(app):
            async with await _client(app) as client:
                request = client.build_request("POST", "/api/v1/session", headers=[
                    ("Origin", BASE_URL), ("Content-Type", "application/json"),
                    ("Content-Type", "application/json")], content=b"{}")
                assert (await client.send(request)).status_code == 400
                request = client.build_request("GET", "/library", headers=[
                    ("Cookie", "qte_session=one"), ("Cookie", "qte_session=two")])
                assert (await client.send(request)).status_code == 400
                request = client.build_request("GET", "/library", headers=[
                    (b"Cookie", b"qte_session=" + "é".encode())])
                assert (await client.send(request)).status_code == 400
    asyncio.run(scenario())


def test_expiry_and_restart_invalidate_session_once():
    async def scenario():
        now = [10.0]
        codes = []
        app = create_app(REPOSITORY, code_sink=codes.append, clock=lambda: now[0])
        async with app.router.lifespan_context(app):
            async with await _client(app) as client:
                token = await _login(client, app)
                old_cookie = client.cookies.get("qte_session")
                now[0] += 1801
                assert (await client.get("/api/v1/library", headers={"X-QTE-Token": token})).status_code == 401
                assert len(codes) == 2
                assert (await client.get("/api/v1/library", headers={"X-QTE-Token": token})).status_code == 401
                assert len(codes) == 2
        restarted = create_app(REPOSITORY, code_sink=lambda _: None)
        async with restarted.router.lifespan_context(restarted):
            async with await _client(restarted) as client:
                client.cookies.set("qte_session", old_cookie)
                assert (await client.get("/api/v1/library", headers={"X-QTE-Token": token})).status_code == 401
    asyncio.run(scenario())


def test_routes_queries_errors_and_headers():
    async def scenario():
        app = create_app(REPOSITORY, code_sink=lambda _: None)
        async with app.router.lifespan_context(app):
            async with await _client(app) as client:
                token = await _login(client, app)
                headers = {"X-QTE-Token": token}
                for query in ("offset=-1", "limit=0", "limit=201", "q=" + "x" * 201, "offset=true"):
                    assert (await client.get(f"/api/v1/library?{query}", headers=headers)).status_code == 400
                response = await client.get("/api/v1/capabilities", headers=headers)
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["x-content-type-options"] == "nosniff"
                assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
                assert next(x for x in response.json()["capabilities"] if x["id"] == "library")["state"] == "available"
                assert (await client.get("/api/v1/documents/not-a-document", headers=headers)).status_code == 404
                assert (await client.get("/api/v1/not-a-route", headers=headers)).status_code == 404
                assert (await client.post("/api/v1/library", headers={**headers, **ORIGIN})).status_code == 405
                assert (await client.get("/assets/../README.md")).status_code == 404
    asyncio.run(scenario())
