from copy import deepcopy
import json

import pytest

from qte.cli import main
from qte.core_reversion_fixture import fixture
from qte.core_reversion_study import study
from qte.strategies.core_reversion import Minute, VwapRules, gap_setup, observations, underlying_exit
from qte.volatility import MINUTE_NS as M

DATE = "2026-11-20"


def result(strategy, inputs, cash=10000):
    return study(strategy, *inputs, cash, .02, .65)


@pytest.mark.parametrize("strategy,right,reason", [("S1", "C", "vwap_touch"), ("S2", "P", "half_gap_fill")])
def test_public_demo_determinism_and_execution(tmp_path, strategy, right, reason):
    reports = []
    for name in ("first", "second"):
        dest = tmp_path/name
        assert main([strategy.lower()+"-study", "--demo", "--output", str(dest)]) == 0
        reports.append(json.loads((dest/"report.json").read_text()))
    assert reports[0] == reports[1]
    row = reports[0]["rows"][0]
    assert row["candidates"][0]["right"] == right
    trade = row["trade"]
    assert trade["reason"] == reason
    assert (trade["debit"], trade["credit"], trade["fees_usd"]) == (1.05, 1.45, 2.60)
    assert trade["decision_ns"] < trade["entry_source_ns"] <= trade["entry_ns"]
    assert trade["entry_ns"] < trade["exit_trigger_ns"] < trade["exit_source_ns"]
    assert trade["net_pnl_usd"] == 37.40 == reports[0]["net_pnl_usd"]


@pytest.mark.parametrize("change,reason", [
    ("late", "missing_regime"), ("gamma", "positive_gamma_unconfirmed"),
    ("vix", "vix_ineligible_or_missing"), ("macro", "macro_blackout_or_unknown"),
    ("top10", "top10_catalyst_or_unknown"), ("calendar", "missing_catalyst_calendar")])
def test_gap_admission_requires_known_filters(change, reason):
    data = fixture("S2")
    payload = data[-1][DATE]
    regime = payload["regimes"][0]
    calendar = payload["calendar"][0]
    when = regime["source_ns"]
    if change == "late":
        regime["available_ns"] = when+1
    elif change == "gamma":
        regime["gamma"] = "negative"
    elif change == "vix":
        regime["vix"] = 31
    elif change == "macro":
        calendar["macro_times_ns"] = [when+10*M]
    elif change == "top10":
        calendar["top10_catalyst"] = None
    else:
        calendar["available_ns"] = when+1
    row = result("S2", data)["rows"][0]
    assert reason in row["candidates"][0]["rejected_by"]
    assert "trade" not in row


def test_gap_needs_prior_scheduled_close_and_rejects_target_previously_hit():
    data = fixture("S2")
    data[2]["2026-11-20"] = 1234
    assert result("S2", data)["rows"][0]["previous_close"] == 7000
    del data[2]["2026-11-19"]
    assert result("S2", data)["rows"][0]["status"] == "missing_prior_session_close"
    data[2]["2026-11-19"] = 7000
    start = data[3].day(DATE).open_ns + 2*M
    op, h, _, c = data[0][DATE][start]
    data[0][DATE][start] = (op, h, 7021, c)
    assert result("S2", data)["rows"][0]["status"] == "target_already_touched"


def test_gap_down_call_uses_mirrored_extreme_and_inclusive_boundary():
    data = fixture("S2")
    day = data[3].day(DATE)
    rows = {}
    for n in range(1, 16):
        at = day.open_ns+n*M
        rows[at] = Minute(at, 997.15, 996.8 if n == 1 else 997., 997.1)
    setup, facts = gap_setup(day, rows, 997, 1000)
    assert setup[0]["right"] == "C" and facts["gap_pct"] == -.3
    assert setup[0]["target"] == 998.5
    rows[at] = Minute(at, 997.2, 996.79, 997.1)
    assert gap_setup(day, rows, 997, 1000)[1]["status"] == "extended_opening_extreme"


def test_weighted_vwap_and_contiguous_prefix():
    data = fixture("S1")
    day = data[3].day(DATE)
    start = day.open_ns
    bars = {start: (90, 90, 90, 90), start+M: (100, 105, 96, 99), start+2*M: (100, 100, 100, 100)}
    volumes = {start: 1, start+M: 3}
    rows = observations(day, bars, volumes, "S1")
    assert rows[start+2*M].vwap == 97.5
    assert start+3*M not in rows
    assert rows[start+2*M].z is None
    with pytest.raises(ValueError):
        VwapRules(2, 3, 2)


def test_wide_quote_risk_cap_and_late_chain():
    data = fixture("S1")
    assert result("S1", data, 1000)["rows"][0]["trade"]["reason"] == "one_lot_exceeds_budget"
    snap = data[-1][DATE]["snapshots"][0]
    snap["books"][1]["ask"] = 1.5
    assert result("S1", data)["rows"][0]["trade"]["reason"] == "untradeable_entry_pair"
    snap["available_ns"] += 1
    assert result("S1", data)["rows"][0]["trade"]["reason"] == "missing_decision_chain"


def test_successful_quote_replay_requires_synchronized_pair_at_both_ends():
    data = fixture("S2")
    snaps = data[-1][DATE]["snapshots"]
    entry = snaps[1]["books"].pop()
    row = result("S2", data)["rows"][0]
    assert row["trade"]["reason"] == "unfilled_entry_limit"
    snaps[1]["books"].append(entry)
    del snaps[2:]
    report = result("S2", data)
    assert report["unresolved"] and report["net_pnl_usd"] is None


def test_new_catalyst_declaration_cancels_pending_entry():
    data = fixture("S2")
    p = data[-1][DATE]
    cal = deepcopy(p["calendar"][0])
    cal.update(available_ns=p["regimes"][0]["source_ns"]+1, top10_catalyst=True)
    p["calendar"].append(cal)
    assert result("S2", data)["rows"][0]["trade"]["reason"] == "entry_gate_withdrawn"


@pytest.mark.parametrize("bid,reason", [(1.50, "half_debit_stop"), (5.00, "profit_target")])
def test_option_exit_requires_quote_strictly_after_trigger(bid, reason):
    data = fixture("S1")
    snaps = data[-1][DATE]["snapshots"]
    # A new quote between the entry fill and completion of the next minute bar.
    changed = deepcopy(snaps[1])
    changed["source_ns"] += M//10
    changed["available_ns"] = changed["source_ns"]
    changed["books"][0].update(bid=bid, ask=bid+.05)
    snaps.insert(2, changed)
    trade = result("S1", data)["rows"][0]["trade"]
    assert trade["reason"] == reason
    assert trade["exit_trigger_ns"] == changed["available_ns"] < trade["exit_source_ns"]


def test_price_stop_has_priority_on_ambiguous_bar_and_skips_pre_fill_wick():
    both = Minute(0, 110, 89, 100)
    sig = dict(right="P", stop=105, target=90)
    assert underlying_exit("S2", sig, both, both, True) == "opening_extreme_stop"
    assert underlying_exit("S2", sig, both, both, False) is None
    stop = Minute(1, 110, 89, 100, vwap=101, z=3)
    assert underlying_exit("S1", dict(right="C", stop_z=3), stop, stop, True) == "z_stop"
