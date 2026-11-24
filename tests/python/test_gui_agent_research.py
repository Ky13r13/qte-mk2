"""Research roles are opt-in and cannot replace deterministic evidence."""
import asyncio
import json

import httpx
import pytest

from qte.gui import agent_provider
from qte.gui.agent_research import ResearchRuns, run_research
from qte.gui.app import create_app


class _Headers:
    def get_content_type(self):
        return "application/json"


class _Response:
    headers = _Headers()
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def read(self, limit):
        assert limit == agent_provider.OLLAMA_RESPONSE_LIMIT + 1
        return self.payload


def test_ollama_chat_is_fixed_loopback_bounded_nonstream_json(monkeypatch):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return _Response(json.dumps({"model": "qwen3:8b", "message": {
                "role": "assistant", "content": "candidate: confirmation_fixed"
            }, "done": True}).encode())
    monkeypatch.setattr(agent_provider, "build_opener", lambda *_: Opener())
    result = agent_provider.consult_ollama(None, "bounded instructions", "public fixture", model="qwen3:8b")
    request, timeout = calls[0]
    assert request.full_url == "http://127.0.0.1:11434/api/chat"
    assert request.method == "POST" and timeout == 95
    assert request.get_header("Authorization") is None
    payload = json.loads(request.data)
    assert payload["model"] == "qwen3:8b" and payload["stream"] is False
    assert payload["messages"] == [{"role": "system", "content": "bounded instructions"},
                                   {"role": "user", "content": "public fixture"}]
    assert result["model"] == "qwen3:8b" and result["text"].startswith("candidate:")


def test_ollama_rejects_unsafe_model_and_sanitizes_transport(monkeypatch):
    for model in ("", "../escape", "bad model", "x" * 129, "remote:cloud", "remote:latest-cloud", None):
        with pytest.raises(ValueError):
            agent_provider.validate_ollama_model(model)
    class Opener:
        def open(self, *_args, **_kwargs):
            raise OSError("secret socket detail")
    monkeypatch.setattr(agent_provider, "build_opener", lambda *_: Opener())
    with pytest.raises(agent_provider.AgentProviderError) as caught:
        agent_provider.consult_ollama(None, "instructions", "context", model="gemma3:4b")
    assert str(caught.value) == "Local model transport or response failed."


def test_ollama_configuration_never_reads_openai_key_on_status(monkeypatch, tmp_path):
    import qte.gui.agent_research as research
    monkeypatch.setattr(research, "read_agent_key", lambda *_: pytest.fail("read OpenAI key"))
    runs = ResearchRuns(tmp_path, True, ollama_model="llama3.2:3b")
    try:
        status = runs.status()
        assert status["enabled"] is True
        assert status["provider"] == "ollama"
        assert "llama3.2:3b" in status["provider_label"]
        assert "checked on run" in status["provider_label"]
    finally:
        runs.close()


def test_proposals_are_blind_and_challengers_receive_measured_evidence():
    calls = []

    def provider(key, instruction, context):
        calls.append(context)
        if len(calls) == 1:
            text = "candidate: confirmation_fixed\nReject if after-cost P&L is no higher."
        elif len(calls) == 2:
            text = "candidate: confirmation_no_chase_fixed\nReject if no-chase misses too many moves."
        else:
            text = "Counterexample: wide costs."
        return dict(text=text, model="test", session_id=None, turn_id=None, session_cleanup="deleted")

    report = run_research(("strategy_a", "strategy_b"), key="test", provider=provider)
    assert len(calls) == 4
    assert all("case_results" not in context for context in calls[:2])
    assert all("case_results" in context for context in calls[2:])
    assert [note["phase"] for note in report["agents"]] == ["proposal", "proposal", "rebuttal", "rebuttal"]
    assert report["backtest_sha256"] == run_research()["backtest_sha256"]
    assert any(row["negative_net_pnl"] for row in report["counterexamples"])


def test_provider_limit_stops_further_requests_and_keeps_the_backtest():
    calls = []

    def provider(*args):
        calls.append(args)
        raise agent_provider.AgentProviderError("Agent session did not complete (usage_limit_exceeded).")

    report = run_research(("strategy_a", "strategy_b", "exposure"), key="test", provider=provider)
    assert len(calls) == 1
    assert [note["state"] for note in report["agents"]] == ["failed", "skipped", "skipped"]
    assert report["backtest"]["trades"]


@pytest.mark.parametrize("experiment", ["straddle", "weekly_reversal", "options_master"])
def test_local_endpoint_requires_auth_and_persists_offline_evidence(tmp_path, experiment):
    app = create_app(tmp_path, code_sink=lambda _: None)
    origin = "http://127.0.0.1:8765"
    base = "/api/v1/agent-research"

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as client:
            assert (await client.get(f"{base}/status")).status_code == 401
            login = await client.post("/api/v1/session", headers={"Origin": origin}, json={
                "schema_version": 1, "code": app.state.session_controller.access_code_for_testing,
            })
            auth = {"X-QTE-Token": login.json()["request_token"], "Origin": origin}
            status = (await client.get(f"{base}/status", headers=auth)).json()
            assert len(status["roles"]) == 5 and status["enabled"] is False
            for roles, code in ((["exposure"], 403), (["bogus"], 400), (["exposure", "exposure"], 400)):
                rejected = await client.post(f"{base}/run", headers=auth, json={"schema_version": 1, "roles": roles})
                assert rejected.status_code == code
            created = await client.post(f"{base}/run", headers=auth,
                                        json={"schema_version": 1, "roles": [], "experiment": experiment})
            assert created.status_code == 202
            run_id = created.json()["id"]
            for _ in range(1000):
                status = await client.get(f"{base}/runs/{run_id}", headers=auth)
                if status.json()["state"] not in {"queued", "running"}:
                    break
                await asyncio.sleep(.01)
            record = status.json()
            assert record["state"] == "completed"
            assert record["report"]["agents"] == []
            evidence = record["report"]["backtest"]
            if experiment == "weekly_reversal":
                assert evidence["evidence"] == "synthetic_mechanics_only"
                assert len(evidence["runs"]["base"]["candidates"]) == 10
                assert evidence["runs"]["base"]["candidates"][0]["trades"]
            elif experiment == "options_master":
                assert evidence["evidence"] == "synthetic_mechanics_only"
                assert evidence["master"]["operational_candidate_id"] == "cash"
                assert int(evidence["validation"]["stress"]["closed_trades"]) > 0
            else:
                assert evidence["candidates"][0]["aggregate"]["trades"] == "3"
            reopened = ResearchRuns(tmp_path, False)
            try:
                assert reopened.status()["latest_run_id"] == run_id
                assert reopened.get(run_id)["report"]["backtest_sha256"] == record["report"]["backtest_sha256"]
            finally:
                reopened.close()
    try:
        asyncio.run(scenario())
    finally:
        app.state.research_runs.close()


def test_master_model_nominates_registered_rule_using_development_data_only():
    calls = []

    def provider(_key, instruction, context):
        evidence = json.loads(context)
        calls.append((instruction, evidence))
        assert "validation" not in evidence
        assert "split" not in evidence["protocol"]
        assert "source_sha256" not in evidence["protocol"]
        return dict(text="candidate: confirmed_fade_1_5sigma\nStress test the losses on reentry.",
                    model="test", session_id=None, turn_id=None, session_cleanup="deleted")

    report = run_research(["strategy_a"], provider=provider, experiment="options_master")
    assert len(calls) == 2
    assert report["picks"] == [{"role": "strategy_a", "candidate": "confirmed_fade_1_5sigma"}]
    assert report["backtest"]["revisions"][0]["proposed_by"] == "strategy_a"
    assert report["backtest"]["revisions"][0]["accepted"] is True
    assert report["backtest"]["master"]["live_enabled"] is False
