"""Independently check signed close marks, model exposures and point-in-time risk."""
import json
import math
from pathlib import Path

import pytest

from qte.live_risk import main, position_risk
from qte.live_risk_quotes import apply_recorded_market

FIXTURE = Path(__file__).resolve().parents[2] / "examples/option-position-risk.json"


def book():
    return json.loads(FIXTURE.read_text())


def test_vertical_marks_and_causal_stresses(tmp_path):
    src = book()
    data = position_risk(src)
    assert [p["liquidation_usd"] for p in data["positions"]] == pytest.approx([1090, -360])
    assert data["portfolio"]["liquidation_usd"] == pytest.approx(730)
    down, elapsed, up = data["scenarios"]
    assert down["model_pnl_usd"] < elapsed["model_pnl_usd"] < up["model_pnl_usd"]
    assert data["portfolio"]["model_value_usd"] != data["portfolio"]["liquidation_usd"]
    assert all(s["status"] == "priced" for s in data["scenarios"])
    out = tmp_path / "result.json"
    assert main([str(FIXTURE), "--output", str(out)]) == 0
    assert json.loads(out.read_text())["market_input"] == "supplied_snapshot"
    with pytest.raises(SystemExit):
        main([str(FIXTURE), "--output", str(out)])


def test_european_put_call_parity_for_spot_greeks_and_discounted_payoff():
    src = book()
    call, put = src["positions"]
    put.update(contract="SPXW  261120P06500000", iv=call["iv"], quantity=-call["quantity"])
    data = position_risk(src)["portfolio"]
    t = 1 / (365 * 24)
    discounted_asset = 6500 * math.exp(-src["dividend_yield"] * t)
    discounted_strike = 6500 * math.exp(-src["rate"] * t)
    assert data["model_value_usd"] == pytest.approx(200 * (discounted_asset - discounted_strike))
    assert data["delta_usd_per_point"] == pytest.approx(200 * math.exp(-src["dividend_yield"] * t))
    assert data["gamma_usd_per_point2"] == pytest.approx(0, abs=1e-12)


def test_etf_early_exercise_and_expiry_scenario():
    src = book()
    src["underlying"] = "SPY"
    src["spot"]["price"] = 50.
    leg = src["positions"][0]
    leg.update(product="ETF", contract="SPY   261120P00100000", quantity=-1)
    src["positions"] = [leg]
    src["scenarios"] = [dict(spot_change_pct=1, iv_change_points=0, elapsed_minutes=5),
                        dict(spot_change_pct=0, iv_change_points=0, elapsed_minutes=60)]
    report = position_risk(src)
    assert report["portfolio"]["model_value_usd"] == pytest.approx(-5000)
    assert report["portfolio"]["delta_usd_per_point"] == pytest.approx(100)
    assert report["scenarios"][0]["model_pnl_usd"] == pytest.approx(50)
    assert report["scenarios"][1]["status"] == "settlement_required"
    assert report["scenarios"][1]["model_pnl_usd"] is None


def test_thin_stale_missing_and_future_quotes():
    src = book()
    leg = src["positions"][1]
    leg["quote"]["ask_size"] = 1
    report = position_risk(src)
    assert report["positions"][1]["mark_status"] == "insufficient_size"
    assert report["positions"][1]["supported_quantity"] == 1
    assert report["portfolio"]["liquidation_usd"] is None
    assert report["portfolio"]["model_value_usd"] is not None
    leg["quote"]["source_ns"] -= 2_000_000_000
    src["spot"] = None
    report = position_risk(src)
    assert report["positions"][1]["mark_status"] == "stale"
    assert report["portfolio"]["model_value_usd"] is None
    assert report["scenarios"][0]["status"] == "missing_spot"
    leg["quote"]["received_ns"] = src["asof_ns"] + 1
    with pytest.raises(ValueError, match="source_ns <="):
        position_risk(src)


def test_journal_asof_epoch_and_invalidation():
    src = book()
    t = src["asof_ns"]
    key = src["positions"][0]["contract"]
    events = [
        dict(event="subscribed", epoch=1, received_utc_ns=t-1_000_000),
        dict(event="underlying", symbol="$SPX", price=6400, epoch=1, source_trade_ms=t//1_000_000-1,
             received_utc_ns=t),
        dict(event="quote", contract=key, bid=4., ask=5., bid_size=3, ask_size=4, epoch=1,
             source_quote_ms=t//1_000_000, received_utc_ns=t),
        dict(event="disconnect", epoch=1, received_utc_ns=t+1),
    ]
    out = position_risk(apply_recorded_market(src, events))
    assert out["spot"] == 6400
    assert out["positions"][0]["liquidation_usd"] == 800
    assert out["positions"][1]["mark_status"] == "missing"
    invalid = events[:3] + [dict(event="unavailable", contract=key, received_utc_ns=t, epoch=1)]
    assert apply_recorded_market(src, invalid)["positions"][0]["quote"] is None
    src["asof_ns"] += 2
    events.append(dict(event="subscribed", epoch=2, received_utc_ns=t+2))
    out = position_risk(apply_recorded_market(src, events))
    assert out["spot_status"] == "missing"
    assert all(p["mark_status"] == "missing" for p in out["positions"])


@pytest.mark.parametrize(("field", "value"), [("product", "ETF"), ("contract", "SPXW  261121C06500000"),
                                            ("quantity", 0), ("iv", float("nan"))])
def test_mixed_product_ambiguous_terms_and_nonfinite_rejected(field, value):
    src = book()
    src["positions"][0][field] = value
    with pytest.raises(ValueError):
        position_risk(src)


def test_live_settings_stores_only_assumptions_and_revalues_current_receipts():
    from qte.live_risk_quotes import live_settings, risk_from_status

    src = book()
    t = src["asof_ns"]
    settings = live_settings(src, t)
    assert not {"asof_ns", "spot"} & settings.keys()
    assert all("quote" not in p for p in settings["positions"])
    key = src["positions"][0]["contract"]
    status = dict(schema="schwab_live_status_v1", epoch=2, connected=True, received_utc_ns=t,
                  max_age_ms=100, contracts=[key], underlying=dict(
                      event="underlying", symbol="$SPX", epoch=2, received_utc_ns=t,
                      price=6475, source_trade_ms=t//1_000_000),
                  quotes=[dict(event="quote", contract=key, epoch=2, received_utc_ns=t,
                               source_quote_ms=t//1_000_000, bid=3, ask=3.2, ask_size=1, bid_size=2)])
    report = risk_from_status(settings, status, t)
    assert report["market_input"] == "live_recorder"
    assert report["spot"] == 6475
    assert report["positions"][0]["liquidation_usd"] == 600
    assert report["positions"][1]["mark_status"] == "missing"
    stale = risk_from_status(settings, status, t + 101_000_000)
    assert stale["capture_state"] == stale["spot_status"] == "stale"
    assert stale["portfolio"]["model_value_usd"] is None
    assert stale["positions"][0]["liquidation_usd"] is None
    status["quotes"][0]["epoch"] = 1
    with pytest.raises(ValueError, match="epoch"):
        risk_from_status(settings, status, t)
    disconnected = risk_from_status(settings, dict(status, connected=False), t)
    assert disconnected["capture_state"] == "disconnected"
    assert disconnected["spot"] is None and disconnected["positions"][0]["quote_observation"] is None


def test_desk_commands_publish_distinct_static_and_dynamic_files(tmp_path, monkeypatch):
    src = book()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").touch()
    (tmp_path / "python/qte").mkdir(parents=True)
    monkeypatch.setattr("qte.live_risk.time.time_ns", lambda: src["asof_ns"])
    assert main([str(FIXTURE), "--desk-snapshot"]) == 0
    assert main([str(FIXTURE), "--desk-live"]) == 0
    desk = tmp_path / "build/live-desk"
    assert json.loads((desk / "risk.json").read_text())["market_input"] == "supplied_snapshot"
    settings = json.loads((desk / "risk-settings.json").read_text())
    assert "spot" not in settings
    assert settings["schema"] == "qte-live-risk-settings-v1"
    assert main([str(desk / "risk-settings.json"), "--desk-live"]) == 0
