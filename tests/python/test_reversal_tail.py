from copy import deepcopy
from datetime import datetime

import pytest

from qte.reversal_tail import NY, bands, entry_cost, exit_proceeds, reversals, tail_candidates, session_bars


def fixture():
    start = int(datetime(2025, 4, 1, 10, 1, tzinfo=NY).timestamp())
    closes = [100.0]*19+[94, 97, 98, 98, 98, 98, 98, 99]
    bars = [{"date": "2025-04-01", "time": start+i*60, "open": p-1,
             "high": p+1, "low": p-1, "close": p} for i, p in enumerate(closes)]
    daily = {"date": "2025-04-01", "regime": "range", "anchor": 100,
             "expected_move": 4, "lower2": 92, "vix": 20}
    return daily, bars


def test_reversal_uses_later_entry_five_minute_exit_and_costs():
    row, bars = fixture()
    trades, _ = reversals([row], {row["date"]: bars})
    t = next(t for t in trades if t["veto"] and t["scenario"] == "base_frozen_iv")
    assert t["entry_time"] == bars[21]["time"]
    assert t["exit_time"]-t["entry_time"] == 300
    assert t["entry_spot"] == 98 and t["exit_spot"] == 99
    assert t["points"] == 1 and t["mes_illustration"] == 0
    wide = next(x for x in trades if x["veto"] and x["scenario"] == "wide_frozen_iv")
    assert wide["pnl"] < t["pnl"]
    changed = deepcopy(bars)
    changed[-1].update(close=50, low=49, open=51, high=52)
    altered, _ = reversals([row], {row["date"]: changed})
    assert [(x["signal_time"], x["entry_time"], x["entry_spot"]) for x in altered] == [(x["signal_time"], x["entry_time"], x["entry_spot"]) for x in trades]


def test_selloff_veto_and_missing_exit_preserve_entered_trade():
    row, bars = fixture()
    row["regime"] = "stress_selloff"
    trades, _ = reversals([row], {row["date"]: bars})
    assert trades and not any(t["veto"] for t in trades)
    row["regime"] = "range"
    trades, _ = reversals([row], {row["date"]: bars[:-1]})
    assert trades and all(t["missing_exit"] and t["pnl"] == -t["max_loss"] for t in trades)
    assert all(t["mes_illustration"] is None for t in trades)


def test_bands_require_twenty_consecutive_observations():
    _, bars = fixture()
    bars.pop(10)
    assert all(b["bb_lower"] is None for b in bands(bars))


def test_tail_retains_losers_and_strikes_are_chosen_at_entry():
    row, _ = fixture()
    row.update(anchor=6000, vix=20)
    start = int(datetime(2025, 4, 1, 15, tzinfo=NY).timestamp())
    bars = [{"time": start+i*60, "close": 6000.0} for i in range(60)]
    flat, _ = tail_candidates([row], {row["date"]: bars}, [row["date"]])
    assert flat and all(t["close_pnl"] < 0 for t in flat)
    assert all(t["strike"] < t["entry_spot"] for t in flat)
    assert all(t["target_trigger"] is None for t in flat)
    fallen = deepcopy(bars)
    for b in fallen[30:]:
        b["close"] = 5900
    crash, _ = tail_candidates([row], {row["date"]: fallen}, [row["date"]])
    initial = lambda trades: [(t["strike"], t["entry_cost"], t["iv_scale"]) for t in trades if t["entry_time"] == start]
    assert initial(crash) == initial(flat)
    winners = [t for t in crash if t["target_exit_time"] is not None]
    assert winners and all(t["target_exit_time"] == t["target_trigger"]+60 for t in winners)
    assert all(t["peak_ratio"] >= 10 for t in winners)
    incomplete, skipped = tail_candidates([row], {row["date"]: bars[:-1]}, [row["date"]])
    assert not incomplete and skipped["incomplete_last_hour"] == 1


def test_price_rounding_and_net_tenfold_convention():
    assert entry_cost(.10) == 21.25
    assert exit_proceeds(0) == -1.25
    assert entry_cost(3.01) == 321.25
    assert exit_proceeds(3.21) == 308.75
    # 10 times proceeds is 900%, not 1,000%, net profit on the initial debit.
    assert (10*entry_cost(.10)-entry_cost(.10))/entry_cost(.10) == 9


def test_intraday_stress_latches_without_relabeling_earlier_bars():
    row, bars = fixture()
    bars[-2].update(open=90, high=91, low=89, close=90)
    result = session_bars(row, bars)
    assert all(not b["stress"] for b in result[:-2])
    assert all(b["stress"] and b["regime"] == "stress_selloff" for b in result[-2:])


def test_halfhour_screen_requires_both_observed_bars(tmp_path):
    import json
    from qte.adapters.robinhood_research import load_last_hour_screen
    bars = [{"begins_at": f"2025-04-01T19:{m}:00Z", "open_value": "100", "high_value": "101",
             "low_value": "98", "close_value": "99", "interpolated": m == "30"} for m in ("00", "30")]
    doc = {"request": {"interval": "30minute"}, "response": {"data": {"results": [
        {"symbol": "SPX", "interval": "30minute", "bars": bars}]}}}
    path = tmp_path/"screen-test.json"
    path.write_text(json.dumps(doc))
    assert load_last_hour_screen(tmp_path)[0] == []
    bars[-1]["interpolated"] = False
    path.write_text(json.dumps(doc))
    assert load_last_hour_screen(tmp_path)[0][0]["return"] == pytest.approx(-.01)
    bars[-1]["low_value"] = "nan"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="OHLC"):
        load_last_hour_screen(tmp_path)
