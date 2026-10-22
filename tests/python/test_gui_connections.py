from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import os

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

import qte.brokers as brokers
from qte.brokers import readonly
from qte.gui import create_app
from qte.gui.connection_routes import connection_routes
import qte.gui.connections as connections
from qte.gui.connections import connection_status
from qte.gui.security import SecurityError


class NamesOnlyEnvironment(Mapping):
    def __init__(self, present=()):
        self.present = frozenset(present)

    def __contains__(self, key):
        raise AssertionError("membership must not fetch environment values")

    def __getitem__(self, key):
        raise AssertionError("credential values must not be read")

    def get(self, key, default=None):
        raise AssertionError("credential values must not be read")

    def __iter__(self):
        return iter(self.present)

    def __len__(self):
        return len(self.present)


def test_status_uses_presence_only_and_empty_values_are_never_ready():
    absent = NamesOnlyEnvironment()
    result = connection_status(absent)
    assert result["schema_version"] == 1
    assert result["mode"] == "offline_prerequisites_only"
    assert result["read_enabled"] is False
    assert result["private_response_retention"] is False
    assert [item["credential_key_present"] for item in result["connections"]] == [False, False]
    assert all("credential_key_absent" in item["blockers"] for item in result["connections"])

    present = NamesOnlyEnvironment({
        "SCHWAB_ACCESS_TOKEN", "ROBINHOOD_CRYPTO_API_KEY", "UNEXPECTED_SECRET_NAME",
    })
    result = connection_status(present)
    assert [item["credential_key_present"] for item in result["connections"]] == [True, True]
    assert all(item["read_enabled"] is False for item in result["connections"])
    assert all(item["credential_validity"] == "not_checked" for item in result["connections"])
    assert all(item["authentication"] == "not_verified" for item in result["connections"])
    assert all("credential_key_absent" not in item["blockers"] for item in result["connections"])
    assert "UNEXPECTED_SECRET_NAME" not in json.dumps(result)

    # Membership treats an explicitly empty value as present, but never ready.
    empty = connection_status({"SCHWAB_ACCESS_TOKEN": "", "ROBINHOOD_CRYPTO_API_KEY": ""})
    assert all(item["credential_key_present"] for item in empty["connections"])
    assert all(not item["read_enabled"] for item in empty["connections"])


def test_default_os_environ_path_never_decodes_values(monkeypatch):
    def forbidden_decode(_):
        raise AssertionError("environment values must not be decoded")

    raw = {
        b"SCHWAB_ACCESS_TOKEN": b"schwab-value-never-decode",
        b"ROBINHOOD_CRYPTO_API_KEY": b"robinhood-value-never-decode",
        b"UNEXPECTED_SECRET_NAME": b"unexpected-value-never-decode",
    }
    environment = os._Environ(raw, os.fsencode, os.fsdecode, os.fsencode, forbidden_decode)
    monkeypatch.setattr(connections.os, "environ", environment)
    result = connections.connection_status()
    assert [item["credential_key_present"] for item in result["connections"]] == [True, True]
    encoded = json.dumps(result)
    assert "never-decode" not in encoded
    assert "UNEXPECTED_SECRET_NAME" not in encoded


def _app():
    def authenticate(request: Request, *, token: bool):
        if request.headers.get("x-test-auth") != "yes":
            raise SecurityError("authentication_required", 401, "Authentication required.")
        assert token is True

    async def security_error(_, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status, headers={"Cache-Control": "no-store"})

    return Starlette(routes=connection_routes(authenticate), exception_handlers={SecurityError: security_error})


def test_routes_are_authenticated_bounded_and_always_offline():
    async def scenario():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            auth = {"X-Test-Auth": "yes"}
            assert (await client.get("/api/v1/connections")).status_code == 401
            status = await client.get("/api/v1/connections", headers=auth)
            assert status.status_code == 200
            assert status.headers["cache-control"] == "no-store"
            assert status.json()["read_enabled"] is False
            assert (await client.get("/api/v1/connections?x=1", headers=auth)).status_code == 400
            assert (await client.post("/api/v1/connections", headers=auth)).status_code == 405
            assert (await client.post("/api/v1/connections/schwab/read", json={
                "schema_version": 1, "operation": "anything"})).status_code == 401
            assert (await client.post("/api/v1/connections/unknown/read", headers=auth, json={
                "schema_version": 1, "operation": "accounts"})).status_code == 404
            assert (await client.post("/api/v1/connections/schwab/read?x=1", headers=auth, json={
                "schema_version": 1, "operation": "anything"})).status_code == 400
            for provider, operation in (("schwab", "ContractOperation_1"), ("robinhood_crypto", "accounts")):
                denied = await client.post(f"/api/v1/connections/{provider}/read", headers=auth, json={
                    "schema_version": 1, "operation": operation})
                assert denied.status_code == 409
                assert denied.json()["error"]["code"] == "broker_reads_disabled"
                assert denied.headers["cache-control"] == "no-store"
            assert (await client.get("/api/v1/connections/schwab/read", headers=auth)).status_code == 405
    asyncio.run(scenario())


def test_read_request_rejects_unknown_malformed_duplicate_nonfinite_and_oversize():
    async def scenario():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-Test-Auth": "yes"}) as client:
            url = "/api/v1/connections/robinhood_crypto/read"
            invalid_operations = ["unknown", "", "1account", "account-name", "a" * 161, 3, True]
            for operation in invalid_operations:
                response = await client.post(url, json={"schema_version": 1, "operation": operation})
                assert response.status_code == 400
                assert response.json()["error"]["code"] == "invalid_operation"
            bodies = [
                b'{"schema_version":1,"operation":"accounts","extra":1}',
                b'{"schema_version":1,"operation":"accounts","operation":"orders"}',
                b'{"schema_version":1,"operation":NaN}',
                b'[]',
            ]
            for body in bodies:
                response = await client.post(url, content=body, headers={"Content-Type": "application/json"})
                assert response.status_code == 400
            assert (await client.post(url, content=b"x", headers={"Content-Type": "text/plain"})).status_code == 415
            oversized = b'{"schema_version":1,"operation":"' + b"a" * 1000 + b'"}'
            assert len(oversized) > 1024
            assert (await client.post(url, content=oversized,
                headers={"Content-Type": "application/json"})).status_code == 413
    asyncio.run(scenario())


def test_repeated_status_and_denied_reads_do_not_construct_clients_or_touch_files(monkeypatch):
    import builtins

    def forbidden(*args, **kwargs):
        raise AssertionError("offline connection routes must not touch files or construct clients")

    monkeypatch.setattr(builtins, "open", forbidden)

    async def scenario():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                    headers={"X-Test-Auth": "yes"}) as client:
            for _ in range(3):
                assert (await client.get("/api/v1/connections")).status_code == 200
                assert (await client.post("/api/v1/connections/schwab/read", json={
                    "schema_version": 1, "operation": "GetAccounts"})).status_code == 409
    asyncio.run(scenario())


def test_integrated_app_boundary_is_authenticated_no_store_and_has_no_side_effects(
        tmp_path, monkeypatch, capsys):
    schwab_secret = "schwab-secret-sentinel-never-reflect"
    robinhood_secret = "robinhood-secret-sentinel-never-reflect"
    monkeypatch.setenv("SCHWAB_ACCESS_TOKEN", schwab_secret)
    monkeypatch.setenv("ROBINHOOD_CRYPTO_API_KEY", robinhood_secret)

    def forbidden(*args, **kwargs):
        raise AssertionError("connection inspection must not construct or call a broker client")

    monkeypatch.setattr(readonly, "RobinhoodCryptoReadOnlyClient", forbidden)
    monkeypatch.setattr(readonly, "SchwabReadOnlyClient", forbidden)
    monkeypatch.setattr(brokers, "RobinhoodCryptoReadOnlyClient", forbidden)
    monkeypatch.setattr(brokers, "SchwabReadOnlyClient", forbidden)
    monkeypatch.setattr(readonly, "_transport", forbidden)
    monkeypatch.setattr(readonly, "_get", forbidden)

    codes = []
    repository = tmp_path / "repository"
    repository.mkdir()
    before = set(repository.rglob("*"))
    app = create_app(repository, code_sink=codes.append)

    async def scenario():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            base_url = "http://127.0.0.1:8765"
            origin = {"Origin": base_url}
            async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
                assert (await client.get("/api/v1/connections")).status_code == 401
                login = await client.post("/api/v1/session", headers=origin, json={
                    "schema_version": 1, "code": codes[0],
                })
                assert login.status_code == 200
                token = login.json()["request_token"]
                headers = {"X-QTE-Token": token}

                missing_token = await client.get("/api/v1/connections")
                assert missing_token.status_code == 403
                assert missing_token.headers["cache-control"] == "no-store"
                wrong_host = await client.get("/api/v1/connections", headers={
                    **headers, "Host": "attacker.invalid",
                })
                assert wrong_host.status_code == 400
                assert wrong_host.headers["cache-control"] == "no-store"
                wrong_origin = await client.get("/api/v1/connections", headers={
                    **headers, "Origin": "http://attacker.invalid",
                })
                assert wrong_origin.status_code == 403
                assert wrong_origin.headers["cache-control"] == "no-store"

                read_url = "/api/v1/connections/schwab/read"
                unauthenticated_read = await client.post(read_url, headers=origin, json={
                    "schema_version": 1, "operation": "GetAccounts",
                })
                assert unauthenticated_read.status_code == 403
                assert unauthenticated_read.headers["cache-control"] == "no-store"
                missing_origin = await client.post(read_url, headers=headers, json={
                    "schema_version": 1, "operation": "GetAccounts",
                })
                assert missing_origin.status_code == 403
                assert missing_origin.headers["cache-control"] == "no-store"
                wrong_read_host = await client.post(read_url, headers={
                    **headers, **origin, "Host": "attacker.invalid",
                }, json={"schema_version": 1, "operation": "GetAccounts"})
                assert wrong_read_host.status_code == 400
                assert wrong_read_host.headers["cache-control"] == "no-store"

                bodies = []
                for _ in range(3):
                    status = await client.get("/api/v1/connections", headers=headers)
                    assert status.status_code == 200
                    assert status.headers["cache-control"] == "no-store"
                    bodies.append(status.text)
                    assert all(item["credential_key_present"] for item in status.json()["connections"])

                    denied = await client.post(read_url,
                        headers={**headers, **origin}, json={
                            "schema_version": 1, "operation": "GetAccounts",
                        })
                    assert denied.status_code == 409
                    assert denied.headers["cache-control"] == "no-store"
                    assert denied.json()["error"]["code"] == "broker_reads_disabled"
                    bodies.append(denied.text)

                reflected = "".join(bodies)
                assert schwab_secret not in reflected
                assert robinhood_secret not in reflected
                json.loads(bodies[0])

    asyncio.run(scenario())
    assert set(repository.rglob("*")) == before
    captured = capsys.readouterr()
    assert schwab_secret not in captured.out + captured.err
    assert robinhood_secret not in captured.out + captured.err
