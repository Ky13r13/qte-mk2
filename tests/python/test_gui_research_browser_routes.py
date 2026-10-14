"""HTTP shell/asset routing checks, not a substitute for browser acceptance."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from qte.gui import create_app
from test_gui_app import BASE_URL, _login


def test_research_deep_links_require_session_and_never_launch(tmp_path, monkeypatch):
    from qte.gui.jobs import JobManager

    def no_launch(*args, **kwargs):
        raise AssertionError("Reading a browser route must not submit a job")

    monkeypatch.setattr(JobManager, "submit", no_launch)
    paths = ("/research/new/run", "/research/new/lab", "/research/jobs/" + "a" * 32)

    async def scenario():
        app = create_app(tmp_path, code_sink=lambda _: None)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client:
                for path in paths:
                    denied = await client.get(path)
                    assert denied.status_code == 303
                    assert denied.headers["location"] == "/login"
                await _login(client, app)
                for path in paths:
                    page = await client.get(path)
                    assert page.status_code == 200
                    assert 'src="/assets/app.js"' in page.text
                    policy = page.headers["content-security-policy"]
                    assert "default-src 'self'" in policy
                    assert "script-src 'self'" in policy
                    assert "frame-ancestors 'none'" in policy
                assert (await client.get("/research/jobs/a/extra")).status_code == 404
        assert not (tmp_path / "build/gui/jobs").exists()

    asyncio.run(scenario())


def test_form_assets_are_exact_allowlisted_local_files(tmp_path):
    static = Path(__file__).parents[2] / "python/qte/gui/static"

    async def scenario():
        app = create_app(tmp_path, code_sink=lambda _: None)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client:
                for name in ("research_forms.js", "research_jobs.js"):
                    response = await client.get("/assets/" + name)
                    assert response.status_code == 200
                    assert response.content == (static / name).read_bytes()
                    assert response.headers["content-type"].startswith("text/javascript")
                for name in ("not-allowed.js", "index.html", "job_worker.py"):
                    assert (await client.get("/assets/" + name)).status_code == 404

    asyncio.run(scenario())


def test_specific_research_routes_precede_generic_artifact_handler():
    script = (Path(__file__).parents[2] / "python/qte/gui/static/app.js").read_text()
    generic = script.index('else if (path.startsWith("/research/"))')
    for route in ('path === "/research/new/run"', 'path === "/research/new/lab"',
                  'path.startsWith("/research/jobs/")'):
        assert script.index(route) < generic
    assert 'jobViews?.dispose(); activeView += 1' in script
    assert 'jobViews.recent(jobs, jobsError, (next) => loadResearch(offset, next))' in script
    assert 'error.field = payload.error.field.slice(0, 160)' in script
