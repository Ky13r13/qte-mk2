"""SPXW replay regressions using hand-priced matched pairs."""
from copy import deepcopy
from hashlib import sha256
import json
from math import sqrt

import pytest

from qte.spx_spreads import DAY_NS, FIVE_MIN_NS, YEAR_NS, expiry_payoff, replay_spx_spreads
from qte.spx_spread_study import captured_report


def _fixture(right="call"):
    end = 5 * FIVE_MIN_NS
    direction = 1 if right == "call" else -1
    strikes = (6000 - direction * 25, 6000 - direction * 20, 6000 - direction * 15, 6000 - direction * 5)
    contracts = [{"id": f"option-{strike}", "right": right, "strike": strike,
                  "expiry_ns": 7 * DAY_NS, "available_ns": 0, "root": "SPXW",
                  "exercise": "european", "settlement": "pm_cash"} for strike in strikes]
    bars = [{"timestamp_ns": index * FIVE_MIN_NS, "available_ns": index * FIVE_MIN_NS + 1,
             "close": value} for index, value in enumerate([6000, 6000 - direction * 40, strikes[0], 6000])]
    quotes = []
    entry = 2 * FIVE_MIN_NS + 5
    exit_time = entry + FIVE_MIN_NS + 2
    for ts in (entry, exit_time):
        for index, strike in enumerate(strikes):
            bid = (5.1 if ts == entry else 6.0) if index == 0 else 3.0 - 0.3 * (index - 1)
            quotes.append({"contract_id": f"option-{strike}", "timestamp_ns": ts, "available_ns": ts + 1,
                           "bid": bid, "ask": bid + .1, "bid_size": 1, "ask_size": 1})
    session = {
        "id": "s", "open_ns": 0, "end_ns": end,
        "opening_daily_iv": {"timestamp_ns": 0, "available_ns": 1, "forward": 6000,
                             "iv": 20 / (6000 * sqrt(end / YEAR_NS)), "expiry_ns": end},
        "contracts": contracts, "bars": bars, "option_quotes": quotes,
    }
    return {
        "sessions": [session], "news_flags": [], "cost_scenario": "base",
        "costs": {"base": {"fee_per_contract": 1, "spread_bps": 0},
                  "stress": {"fee_per_contract": 2, "spread_bps": 20}},
        "max_debit_dollars": 500,
    }


def _candidate(result, name="reentry_5pt"):
    return next(row for row in result["candidates"] if row["id"] == name)


@pytest.mark.parametrize("right", ["call", "put"])
def test_spread_fills_all_widths_share_atm_anchor_and_account_for_legs(right):
    data = _fixture(right)
    result = replay_spx_spreads(data)
    assert replay_spx_spreads(data) == result
    candidate_trades = []
    for width in (5, 10, 20):
        row = _candidate(result, f"reentry_{width}pt")
        assert len(row["trades"]) == 1 and not row["unresolved"]
        trade = row["trades"][0]
        candidate_trades.append(trade)
        assert (trade["short_strike"] - trade["long_strike"]) == (width if right == "call" else -width)
        assert trade["entry_quote_ns"] > trade["decision_available_ns"]
        assert trade["exit_quote_ns"] >= trade["entry_ns"] + FIVE_MIN_NS
        assert trade["observable_regime"] == "reentry"
        assert row["regimes"]["reentry"]["entries"] == 1
    assert len({trade["long_strike"] for trade in candidate_trades}) == 1
    assert candidate_trades[0]["entry_debit_points"] == pytest.approx(2.2)
    assert candidate_trades[0]["risk_deployed"] == pytest.approx(224)
    assert candidate_trades[0]["net_pnl"] == pytest.approx(66)
    stressed = deepcopy(data)
    stressed["cost_scenario"] = "stress"
    assert _candidate(replay_spx_spreads(stressed))["aggregate"]["net_pnl"] < 66


def test_news_veto_cannot_see_late_publication_and_preserves_attribution():
    data = _fixture()
    available = 2 * FIVE_MIN_NS + 2
    data["news_flags"] = [{"timestamp_ns": FIVE_MIN_NS, "available_ns": available, "active": True}]
    row = _candidate(replay_spx_spreads(data), "gated_reentry_5pt")
    assert not row["trades"] and row["skipped"][0]["reason"] == "news_before_fill"
    data["news_flags"][0]["available_ns"] = available + 20
    assert _candidate(replay_spx_spreads(data), "gated_reentry_5pt")["aggregate"]["entries"] == 1
    data["news_flags"][0]["available_ns"] = available - 2
    result = replay_spx_spreads(data)
    assert _candidate(result)["regimes"]["news"]["entries"] == 1
    gated = _candidate(result, "gated_reentry_5pt")
    assert gated["regimes"]["news"]["entries"] == 0 and gated["skipped"][0]["reason"] == "gated_news"


def test_selected_short_must_be_visible_and_match_liquidation_date():
    data = _fixture()
    session = data["sessions"][0]
    session["contracts"][1]["available_ns"] = 2 * FIVE_MIN_NS + 2
    assert _candidate(replay_spx_spreads(data))["skipped"][0]["reason"] == "visible_leg_unavailable"
    session["contracts"][1]["available_ns"] = 0
    session["contracts"][1]["expiry_ns"] = 8 * DAY_NS
    assert _candidate(replay_spx_spreads(data))["skipped"][0]["reason"] == "visible_leg_unavailable"
    session["contracts"][1]["exercise"] = "american"
    with pytest.raises(ValueError, match="European"):
        replay_spx_spreads(data)


def test_unresolved_exit_does_not_book_a_fill_or_realize_a_loss():
    data = _fixture()
    quotes = data["sessions"][0]["option_quotes"]
    quotes[4]["bid_size"] = 0  # no displayed long-leg bid for the eligible exit
    row = _candidate(replay_spx_spreads(data))
    summary = row["aggregate"]
    assert len(row["unresolved"]) == 1 and not row["trades"]
    assert summary["entries"] == 1 and summary["closed_trades"] == 0
    assert summary["realized_pnl"] == 0
    assert summary["net_pnl"] == pytest.approx(-224)
    data["max_debit_dollars"] = 222
    capped = _candidate(replay_spx_spreads(data))
    assert capped["skipped"][0]["reason"] == "debit_cap"
    assert capped["aggregate"]["entries"] == 0


def test_skip_missing_daily_iv_and_close_price_geometry():
    data = _fixture()
    data["sessions"][0]["opening_daily_iv"] = None
    result = replay_spx_spreads(data)
    assert result["sessions"][0]["status"] == "missing_same_day_iv"
    assert _candidate(result)["aggregate"]["entries"] == 0
    assert expiry_payoff("call", 6000, 6010, 5990, 3, 4) == -304
    assert expiry_payoff("call", 6000, 6010, 6003, 3, 4) == -4
    assert expiry_payoff("call", 6000, 6010, 6020, 3, 4) == 696
    assert expiry_payoff("put", 6000, 5990, 5980, 3, 4) == 696
    with pytest.raises(ValueError, match="debit vertical"):
        expiry_payoff("put", 6000, 6010, 5990, 3)


def test_stale_or_unsynchronized_leg_cannot_supply_entry():
    data = _fixture()
    quotes = data["sessions"][0]["option_quotes"]
    quotes[1]["available_ns"] += 31_000_000_000
    row = _candidate(replay_spx_spreads(data))
    assert not row["trades"] and row["skipped"][0]["reason"] == "executable_entry_unavailable"
    quotes[1]["available_ns"] = quotes[1]["timestamp_ns"]
    quotes[1]["timestamp_ns"] -= 1
    row = _candidate(replay_spx_spreads(data))
    assert not row["trades"] and row["skipped"][0]["reason"] == "executable_entry_unavailable"


def test_declared_shock_is_observed_without_future_labels():
    data = _fixture()
    data["sessions"][0]["bars"][1]["close"] = 5940
    result = replay_spx_spreads(data)
    assert result["regime_opportunities"]["shock"] == 1
    assert _candidate(result)["regimes"]["shock"]["entries"] == 1
    gated = _candidate(result, "gated_reentry_5pt")
    assert gated["regimes"]["shock"]["observations"] == 2
    assert gated["regimes"]["shock"]["entries"] == 0
    assert gated["skipped"][0]["reason"] == "gated_shock"


def test_capture_distinguishes_missing_iv_from_a_year_with_observations(tmp_path):
    session = _fixture()["sessions"][0]
    missing = {**session, "id": "missing", "open_ns": DAY_NS, "end_ns": DAY_NS + session["end_ns"],
               "opening_daily_iv": None, "bars": [], "contracts": [], "option_quotes": []}
    rows = [{"schema_version": 1, "product": "SPXW", "source": "test fixture", "cadence": "1m"},
            {"session": session, "news_flags": []}, {"session": missing, "news_flags": []}]
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    path = tmp_path / "capture.jsonl"
    path.write_bytes(raw)

    report = captured_report(path)
    assert report["source_sha256"] == sha256(raw).hexdigest()
    coverage = report["coverage"]
    assert coverage["years"] == ["1969", "1970"]  # Exchange-local dates, across midnight UTC.
    assert coverage["missing_same_day_atm_iv"] == {"missing": "missing_same_day_atm_iv"}
    assert coverage["regime_observations_by_year"]["1970"] == {}
    candidate = _candidate(report)
    breakdown = next(row for row in candidate["breakdowns"]
                     if row["dimension"] == "year_and_regime" and row["key"] == ["1969", "reentry"])
    assert breakdown["trades"] == 1
    assert breakdown["closed_net_pnl"] == pytest.approx(65)

    path.write_bytes(raw + b"null\n")
    with pytest.raises(ValueError, match="each SPXW capture line"):
        captured_report(path)
