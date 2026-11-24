import json
from decimal import Decimal

import pytest

from qte.cli import main
from qte.s7_execution import Account
from qte.s7_fixture import fixture
from qte.s7_study import study
from qte.s7_tape import parse_session, select, straddle_move, regime_at
from qte.volatility import MINUTE_NS


def inputs():
    return fixture("SPX")


def run(data):
    return study(*data, "SPX", 10_000, .65)


def test_demo_through_cli_has_fill_accounting_and_determinism(tmp_path):
    results = []
    for name in ("a", "b"):
        dest = tmp_path/name
        assert main(["s7-study", "--demo", "--output", str(dest)]) == 0
        results.append(json.loads((dest/"report.json").read_bytes()))
    a, b = results
    assert a == b
    up, down = [r["trade"] for r in a["rows"]]
    assert [(f["quantity"], f.get("reason")) for f in up["fills"]] == [
        (4, None), (1, "3x"), (1, "6x"), (2, "time")]
    assert down["fills"][-1]["reason"] == "range_midpoint"
    assert down["fills"][-1]["price"] == .10
    assert a["net_pnl_usd"] == 278.30
    assert down["cash_after_usd"] == 10278.30
    # The first trade raised equity enough for five contracts on day two.
    assert down["fills"][0]["quantity"] == 5
    assert down["remaining_monthly_budget_usd"] == 814.15


def test_budget_uses_starting_month_equity_and_does_not_refill():
    a = Account(Decimal(10_000))
    assert a.budget("2026-11-02", 21) == 2*Decimal(1000)/21
    a.remaining = Decimal(12)
    a.cash = Decimal(20_000)
    assert a.budget("2026-11-30", 1) == 12
    assert a.budget("2026-12-01", 20) == 200
    assert a.remaining == 2000


def test_strict_volume_and_lagged_warmup():
    idx, volumes, sched, options = inputs()
    first = min(idx)
    volumes[first][35] = 1500
    row = study(idx, volumes, sched, options, "SPX", 10000, .65)["rows"][0]
    assert row["breakouts"][0]["relative_volume"] == 1.5
    assert "weak_volume" in row["breakouts"][0]["reasons"]
    previous = sorted(d for d in volumes if d < first)[-1]
    del volumes[previous][35]
    row = study(idx, volumes, sched, options, "SPX", 10000, .65)["rows"][0]
    assert "missing_proxy_volume_baseline" in row["breakouts"][0]["reasons"]


def test_delayed_and_estimated_regime_and_stale_chains():
    idx, _, sched, opt = inputs()
    label = min(idx)
    day = sched.day(label)
    at = day.open_ns + 35*MINUTE_NS
    opt[label]["regimes"][0].update(gamma="estimated_negative", vix=20.4, vix3m=20)
    tape = parse_session(opt[label], day, "SPX")
    assert regime_at(tape, day.open_ns, at) is None
    opt[label]["regimes"][0].update(vix=22, available_ns=at+1)
    tape = parse_session(opt[label], day, "SPX")
    assert regime_at(tape, day.open_ns, at) is None
    assert regime_at(tape, day.open_ns, at+1) is True
    assert regime_at(tape, day.open_ns, at+10*MINUTE_NS) is None
    assert straddle_move(tape, day.open_ns+30*MINUTE_NS, 7000) == pytest.approx(6.1)
    assert straddle_move(tape, day.open_ns+32*MINUTE_NS, 7000) is None
    assert select(tape, at, 7002, "P") is None
    assert select(tape, at, 7010, "C") is None


def test_missing_data_reports_candidates_without_fabricating_pnl():
    idx, vol, sched, _ = inputs()
    r = study(idx, vol, sched, {}, "SPX", 10000, .65)
    assert r["closed"] == 0 and r["net_pnl_usd"] is None
    assert r["rows"][0]["breakouts"][0]["reasons"] == ["missing_atm_straddle", "missing_regime"]


def test_future_quote_cannot_fill_after_signal_using_old_source():
    data = inputs()
    payload = data[3][min(data[0])]
    snaps = payload["snapshots"]
    # Only pre-decision quotes and quotes after the entry limit expires remain.
    payload["snapshots"] = snaps[:2] + snaps[4:]
    row = run(data)["rows"][0]
    assert row["trade"]["reason"] == "unfilled_entry_limit"
    assert row["trade"]["fills"] == []


def test_unresolved_quote_gap_blocks_following_session():
    data = inputs()
    snaps = data[3][min(data[0])]["snapshots"]
    del snaps[3:9]
    r = run(data)
    assert r["unresolved"] and r["net_pnl_usd"] is None
    assert r["rows"][0]["trade"]["reason"] == "quote_monitoring_gap"
    assert r["rows"][1]["trade"]["reason"] == "prior_unresolved_position"


def test_synthetic_xsp_contracts_and_small_lots():
    r = study(*fixture("XSP"), "XSP", 2200, .65)
    up = r["rows"][0]["trade"]
    assert up["contract"].startswith("XSP   ")
    assert [(f["quantity"], f.get("reason")) for f in up["fills"]] == [(1, None), (1, "time")]


def test_option_expiry_root_and_negative_delta_validation():
    data = inputs()
    day = data[2].day(max(data[0]))
    payload = data[3][day.trading_date]
    with pytest.raises(ValueError, match="same-day"):
        parse_session(payload, day, "XSP")
    payload["snapshots"][1]["books"][0]["delta"] = .12
    with pytest.raises(ValueError, match="delta"):
        parse_session(payload, day, "SPX")


def test_entry_price_and_displayed_size_bound_the_fill():
    data = inputs()
    snap = data[3][min(data[0])]["snapshots"][2]
    book = snap["books"][0]
    book["ask_size"] = 1
    trade = run(data)["rows"][0]["trade"]
    assert trade["fills"][0]["quantity"] == 1
    book["bid"], book["ask"] = .20, .21
    # The next observation at the deadline can fill at the committed 0.20 limit.
    trade = run(data)["rows"][0]["trade"]
    assert trade["fills"][0]["price"] == .20
    assert trade["fills"][0]["at_ns"] > snap["available_ns"]


def test_expiry_requires_official_pm_settlement_and_monitoring():
    data = inputs()
    day = data[2].day(min(data[0]))
    payload = data[3][day.trading_date]
    snaps = payload["snapshots"]
    # No bids from the first 3x target onward. Keep quoting through expiry.
    from copy import deepcopy
    for s in snaps[4:]:
        s["books"][0].update(bid=0, ask=.05, bid_size=0)
    while snaps[-1]["available_ns"] < day.close_ns:
        following = deepcopy(snaps[-1])
        following["source_ns"] += MINUTE_NS
        following["available_ns"] += MINUTE_NS
        snaps.append(following)
    assert run(data)["rows"][0]["trade"]["reason"] == "missing_official_settlement"
    payload["settlement"] = 7009
    trade = run(data)["rows"][0]["trade"]
    assert trade["fills"][-1]["reason"] == "pm_cash_settlement"
    assert trade["fills"][-1]["price"] == 0
    assert trade["net_pnl_usd"] == -82.60


def test_opening_width_equal_to_threshold_is_ineligible():
    data = inputs()
    d = min(data[0])
    data[0][d][5] = (7002, 6999, 7000)
    for b in data[3][d]["snapshots"][0]["books"]:
        b.update(bid=2.50, ask=2.50)
    data[0][d][35] = (7003, 7002, 7003)
    row = run(data)["rows"][0]
    assert row["breakouts"][0]["reasons"] == ["wide_range"]


def test_price_improvement_cannot_grow_submitted_order():
    data = inputs()
    payload = data[3][min(data[0])]
    payload["snapshots"][2]["books"][0].update(bid=.095, ask=.10)
    trade = run(data)["rows"][0]["trade"]
    assert trade["fills"][0]["price"] == .10
    assert trade["fills"][0]["quantity"] == trade["order_quantity"] == 4


def test_interpolated_minutes_preserve_date_without_supplying_prices(tmp_path):
    from qte.adapters.robinhood_research import read_index_minutes

    capture = dict(source="Robinhood MCP get_index_historicals", request={"interval": "minute"},
                   response={"data": {"results": [dict(symbol="SPX", interval="minute",
                             bars=[dict(begins_at="2026-06-05T13:30:00Z", interpolated=True)])]}})
    path = tmp_path / "index.json"
    path.write_text(json.dumps(capture))
    sessions, digest = read_index_minutes(path, "SPX")
    assert sessions == {"2026-06-05": {}}
    assert len(digest) == 64
