from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from qte.adapters.robinhood_research import load_captures
from qte.regime_study import (classify, features, guard, option_trade, run_intraday,
                              walk_forward)


def data():
    out = {s: {} for s in ("SPX", "VIX", "SPY")}
    d = date(2020, 1, 1)
    for i in range(340):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        day = d.isoformat()
        p = 4000+i
        out["SPX"][day] = dict(date=day, open=p-1, high=p+3, low=p-2, close=p)
        out["SPY"][day] = dict(date=day, open=p/10-.1, high=p/10+.3, low=p/10-.2, close=p/10, volume=100+i)
        out["VIX"][day] = dict(close=20)
        d += timedelta(days=1)
    return out


def test_features_use_only_prior_information_and_fixed_formula():
    original = data()
    a = features(original)
    index = 315
    changed = deepcopy(original)
    for row in a[index:]:
        day = row["date"]
        changed["SPX"][day].update(open=9000, high=10000, low=8000, close=9500)
        changed["VIX"][day]["close"] = 90
        changed["SPY"][day]["volume"] = 10**12
    b = features(changed)
    for key in ("regime", "reason", "sma300", "ema20", "flow", "adjusted_plus", "expected_move", "asof"):
        assert a[index][key] == b[index][key]
    r = a[index]
    assert r["adjusted_plus"] == pytest.approx(r["ema20"]+.5*r["atr14"]*r["flow"])
    assert r["adjusted_minus"] == pytest.approx(r["ema20"]-.5*r["atr14"]*r["flow"])
    assert r["asof"] < r["date"]
    assert a[299]["regime"] == "unclassified"


def test_stress_override_and_reversal_guards():
    assert classify({"close": 90, "open": 100}, 100, 105, 40, -3, False)[0] == "stress_selloff"
    row = dict(regime="range", lower2=90, upper2=110, sma300=95)
    assert not guard(row, -1, 89)
    assert not guard(row, -1, 95)
    assert not guard(row, 1, 111)
    assert guard(row, 1, 100)
    row["regime"] = "stress_selloff"
    assert guard(row, -1, 89)
    assert not guard(row, 1, 89)


@pytest.mark.parametrize("vehicle", ["long", "credit", "cash_put"])
def test_option_cashflows_cost_stress_and_loss_bounds(vehicle):
    base = option_trade(5000, 5050, .2, .2, 1, vehicle, 6.5, "base_frozen_iv")
    wide = option_trade(5000, 5050, .2, .2, 1, vehicle, 6.5, "wide_frozen_iv")
    assert base["pnl"] == pytest.approx(base["exit_value"]-base["entry_debit"]-base["fees"])
    assert wide["pnl"] < base["pnl"]
    crash = option_trade(5000, .01, .2, .2, 1, vehicle, 6.5, "base_frozen_iv")
    assert crash["pnl"] >= -crash["max_loss"]-1e-8
    assert (base["gamma"] > 0) if vehicle == "long" else True
    if vehicle == "cash_put":
        assert base["gamma"] < 0 and base["collateral"] >= 500_000


def test_adapter_excludes_interpolation_and_respects_minute_availability(tmp_path):
    bar = dict(begins_at="2025-04-03T13:30:00Z", open_value="10", high_value="12", low_value="9", close_value="11")
    capture = {"request": {"interval": "minute"}, "response": {"data": {"results": [
        {"symbol": "SPX", "bars": [bar, {**bar, "begins_at": "2025-04-03T13:31:00Z", "interpolated": True}]}]}}}
    (tmp_path/"one.json").write_text(json.dumps(capture))
    _, minute, provenance = load_captures(tmp_path)
    bars = minute["SPX"]["2025-04-03"]
    assert len(bars) == 1
    from datetime import datetime
    assert bars[0]["time"] == int(datetime.fromisoformat("2025-04-03T13:31:00+00:00").timestamp())
    assert provenance["excluded"]["SPX_minute_interpolated"] == 1
    capture["response"]["data"]["results"][0]["bars"][0]["high_value"] = "8"
    (tmp_path/"two.json").write_text(json.dumps(capture))
    with pytest.raises(ValueError, match="Invalid OHLC"):
        load_captures(tmp_path)


def test_walk_forward_never_selects_using_test_year_outcome():
    training = [dict(date=f"2015-03-{i%28+1:02}", scenario="base_frozen_iv", regime="range", family="sma20", vehicle="long", pnl=10,
                     max_loss=100, collateral=100) for i in range(30)]
    holdout = dict(training[0], date="2016-04-01", pnl=-100)
    first = walk_forward(training+[holdout])
    second = walk_forward(training+[{**holdout, "pnl": 1_000_000}])
    a = next(r for r in first if r["year"] == 2016 and r["regime"] == "range")
    b = next(r for r in second if r["year"] == 2016 and r["regime"] == "range")
    assert a["selected"] == b["selected"] == "sma20 / long"
    assert a["pnl"] == -100 and b["pnl"] == 1_000_000


def test_missing_exit_does_not_erase_entry_and_five_minute_hold():
    from datetime import datetime
    start = int(datetime.fromisoformat("2025-04-03T13:30:00+00:00").timestamp())
    row = dict(date="2025-04-03", regime="trend_up", lower2=4000, upper2=6000, sma300=4200, anchor=5000, expected_move=50, vix=20)
    bars = [dict(date=row["date"], time=start+(i+1)*60, open=5000+i, close=5000+i+.5, high=5001+i, low=4999+i) for i in range(45)]
    full, _ = run_intraday([row], {"SPX": {row["date"]: bars}, "SPY": {}})
    trade = next(t for t in full if t["family"] == "minute_ema" and t["vehicle"] == "long" and t["scenario"] == "base_frozen_iv")
    assert trade["entry_time"] > trade["signal_time"]
    assert trade["exit_time"]-trade["entry_time"] == 300
    missing, _ = run_intraday([row], {"SPX": {row["date"]: [b for b in bars if b["time"] != trade["exit_time"]]}, "SPY": {}})
    unresolved = next(t for t in missing if t["family"] == "minute_ema" and t["vehicle"] == "long" and t["scenario"] == "base_frozen_iv")
    assert unresolved["entry_time"] == trade["entry_time"]
    assert unresolved["unresolved"]
    assert unresolved["pnl"] == -unresolved["max_loss"]
