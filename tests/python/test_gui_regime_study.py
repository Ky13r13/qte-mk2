import asyncio
import json

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from qte.gui.regime_routes import regime_routes
from qte.gui.security import SecurityError


def test_regime_report_auth_fixed_paths_and_no_symlink_reads(tmp_path):
    def authenticate(request, *, token):
        assert token
        if request.headers.get("x-test-auth") != "yes":
            raise SecurityError("authentication_required", 401, "Authentication required")
    async def handler(_, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)
    root=tmp_path/"build"/"regime-study"
    root.mkdir(parents=True)
    (root/"summary.json").write_text(json.dumps({"schema_version": 1}))
    app=Starlette(routes=regime_routes(tmp_path, authenticate), exception_handlers={SecurityError:handler})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
            path="/api/v1/regime-study/summary"
            assert (await c.get(path)).status_code == 401
            headers={"x-test-auth":"yes"}
            response=await c.get(path,headers=headers)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert (await c.get(path+"?path=secret",headers=headers)).status_code == 400
            assert (await c.get("/api/v1/regime-study/not-registered",headers=headers)).status_code == 404
            tail=tmp_path/"build"/"reversal-tail-study"
            tail.mkdir()
            (tail/"summary.json").write_text('{"status":"modeled"}')
            assert (await c.get("/api/v1/regime-study/tail-summary",headers=headers)).json() == {"status":"modeled"}
            assert (await c.get("/api/v1/regime-study/tail-screen",headers=headers)).status_code == 404
            for malformed in ('{"pnl":1,"pnl":2}', '{"pnl":1e999}', '{"result":"\\ud800"}'):
                (root/"summary.json").write_text(malformed)
                invalid = await c.get(path, headers=headers)
                assert invalid.status_code == 422
                assert invalid.json() == {"error": "invalid_artifact"}
            (root/"summary.json").unlink()
            (root/"summary.json").symlink_to(tmp_path/"outside.json")
            (tmp_path/"outside.json").write_text('{"secret":true}')
            assert (await c.get(path,headers=headers)).status_code == 403
    asyncio.run(scenario())
