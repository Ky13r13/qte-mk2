from datetime import timedelta, date
import json
from pathlib import Path

import pytest

from qte import Bar
from qte.adapters.schwab_research import MINUTE_NS
from qte.session_calendar import SessionDay, SessionSchedule
from qte.s5_equity import equity_proxy
from qte.s5_spreads import replay_options
from qte.s5_study import observe_sessions, study
from qte.strategies.oversold_bounce import S5Config, S5Signal

ROOT = Path(__file__).resolve().parents[2]
DAY = "2026-10-14"
DAY_NS = 1440 * MINUTE_NS


def schedule():
    start = date.fromisoformat(DAY) - timedelta(days=199)
    # Explicit synthetic calendar: every date open, 13:30 UTC; warm up exactly 199 closes.
    days = []
    for i in range(206):
        d = start + timedelta(days=i)
        t = (d-date(1970,1,1)).days*DAY_NS + 810*MINUTE_NS
        days.append(SessionDay(d.isoformat(),t,t+390*MINUTE_NS))
    return SessionSchedule(days[0].trading_date, days[-1].trading_date, "fixture","0"*64,"synthetic",tuple(days))


def bars(day):
    result = []
    for i in range(390):
        start = day.open_ns + i*MINUTE_NS
        result.append(Bar("SPY",start,start+MINUTE_NS,200,202,200,200,10))
    return result


def test_signal_boundaries_and_nonmutating_partial_rsi():
    signal = S5Signal()
    for i in range(198):
        signal.finish_day(101,100)
    assert signal.snapshot(102,101,101) is None
    signal.finish_day(101,100)
    snap = signal.snapshot(102,101,101)
    assert snap["rsi2"] == 100 and snap["ibs"] == 0 and snap["signal"]
    assert signal.snapshot(102,101,101) == snap
    assert signal.snapshot(100,100,100)["rsi2"] == 50
    assert signal.snapshot(100,100,100)["ibs"] is None
    signal.finish_day(103,102)
    assert signal.snapshot(103,99,100)["rsi2"] == pytest.approx(100/3)
    with pytest.raises(ValueError):
        S5Config(ibs_entry=2)


def test_freeze_at_1545_and_keep_unresolved_events():
    sched = schedule()
    today = sched.day(DAY)
    daily = {d.trading_date:(101,100) for d in sched.days}
    m = {DAY:bars(today)}
    original = study(m,daily,sched,S5Config())
    row = original["rows"][0]
    assert row["features"]["signal"] and row["decision_ns"] == today.open_ns+375*MINUTE_NS
    assert row["rule_exit_reason"] == "end_of_capture" and row["returns"]["rule"] is None
    # Change the current official close and every post-cutoff price. Only forward marks change.
    daily[DAY] = 999,900
    for i,b in enumerate(m[DAY][375:],375):
        m[DAY][i] = Bar("SPY",b.start_ns,b.end_ns,900,999,850,900,10)
    updated = observe_sessions(m,daily,sched,S5Config())[0]
    assert updated["features"] == row["features"] and updated["mark_price"] == 900
    del m[DAY][300]
    assert observe_sessions(m,daily,sched,S5Config())[0]["status"] == "incomplete_prefix"


def test_native_one_position_timing_and_realized_exit():
    s = schedule()
    selected = s.days[199:201]
    m = {d.trading_date:bars(d) for d in selected}
    daily = {d.trading_date:(101,100) for d in s.days}
    result = study(m,daily,s,S5Config())
    proxy = equity_proxy(m,result["rows"],s)["cases"]["base"]
    assert len(proxy["fills"]) == 2 and proxy["closed_trades"] == 1
    assert [f["time_ns"] for f in proxy["fills"]] == [x["mark_ns"] for x in result["rows"]]
    assert proxy["pnl_usd"] < 0  # flat marks plus costs


def options():
    data = json.loads((ROOT/"examples/s5-synthetic-spread.json").read_bytes())
    now = data["sessions"][DAY]["available_ns"]
    report = dict(rows=[dict(date=DAY,decision_ns=now,mark_ns=now+MINUTE_NS,
                             standard_session=True,features=dict(signal=True,exit_signal=False))])
    return report,schedule(),data


def test_spread_reconciles_quotes_and_waits_for_later_stop_fill():
    args = options()
    run = replay_options(*args)
    [p] = run["probes"]
    assert p["entry_debit"] == 2 and p["exit_credit"] == pytest.approx(.88)
    assert p["exit_trigger_ns"] < p["exit_source_ns"] <= p["exit_ns"]
    assert p["reason"] == "half_debit_stop" and p["net_pnl_usd"] == pytest.approx(-114.60)
    assert args[-1]["evidence"] == "synthetic"


@pytest.mark.parametrize("case,expected", [
    ("gap","quote_monitoring_gap"),("stale","missing_fresh_decision_chain"),
    ("risk","one_lot_exceeds_risk_cap"),("missing","missing_chain_and_quotes"),
    ("delta","no_near_half_delta_long")])
def test_spread_fails_closed(case,expected):
    report,s,data = options()
    payload = data["sessions"][DAY]
    if case == "gap":
        for q in payload["quotes"][2:]:
            q["timestamp_ns"] += 3*MINUTE_NS
            q["available_ns"] += 3*MINUTE_NS
    elif case == "stale":
        payload["available_ns"] += 1
    elif case == "risk":
        data["max_risk_usd"] = 199
    elif case == "missing":
        data["sessions"].clear()
    else:
        payload["calls"][0]["delta"] = .8
    result = replay_options(report,s,data)
    assert result["probes"][0]["reason"] == expected and result["closed_only_pnl_usd"] is None


def test_overnight_sessions_and_expiry_force_a_later_executable_exit():
    report,s,tape = options()
    payload = tape["sessions"][DAY]
    opened = report["rows"][0]["mark_ns"]
    template = payload["quotes"][:2]
    payload["quotes"] = []
    days = s.days[199:202]
    for i,d in enumerate(days):
        cutoff = d.close_ns - 15 * MINUTE_NS
        for t in range(opened if i == 0 else d.open_ns, d.close_ns, MINUTE_NS):
            for q in template:
                payload["quotes"].append(dict(q,timestamp_ns=t,available_ns=t+1))
        if i:
            report["rows"].append(dict(date=d.trading_date,decision_ns=cutoff,mark_ns=cutoff+MINUTE_NS,
                    standard_session=True,features=dict(signal=False,exit_signal=False)))
    trade = replay_options(report,s,tape)["probes"][0]
    assert trade["reason"] == "expiry"
    assert trade["exit_trigger_ns"] == days[-1].close_ns-15*MINUTE_NS
    assert trade["exit_source_ns"] == trade["exit_trigger_ns"]+MINUTE_NS
    report["rows"][1]["features"]["exit_signal"] = True
    trade = replay_options(report,s,tape)["probes"][0]
    assert trade["reason"] == "spy_indicator"
    assert trade["exit_source_ns"] == days[1].close_ns-14*MINUTE_NS
