from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from qte.forward_outcomes import forward_move, session_distribution, study
from qte.reversal_tail import NY


def fixture(day="2025-04-01"):
    start = datetime.fromisoformat(day).replace(hour=10, minute=1, tzinfo=NY)
    closes = [100.0]*19+[94, 97, 98]+[99.0]*45
    bars = [{"time": int(start.timestamp())+i*60, "open": p-1,
             "high": p+1, "low": p-1, "close": p} for i, p in enumerate(closes)]
    daily = {"date": day, "asof": (start-timedelta(days=1)).date().isoformat(),
             "regime": "range", "anchor": 100, "expected_move": 4,
             "lower2": 92, "vix": 20}
    return daily, bars


def test_delayed_anchor_exact_horizons_and_interior_gaps():
    row, bars = fixture()
    result = study([row], {row["date"]: bars})
    out = [r for r in result["outcomes"] if r["bound"] == 1 and r["veto"]]
    assert len(out) == 4
    assert {r["signal_time"] for r in out} == {bars[20]["time"]}
    assert all(r["entry_spot"] == 98 and r["exit_spot"] == 99 for r in out)
    assert [r["exit_time"]-r["entry_time"] for r in out] == [60, 300, 900, 1800]
    assert all(r["return_bps"] == pytest.approx(10000/98) for r in out)
    assert all(r["close_mae_bps"] == 0 for r in out)
    by_time = {b["time"]: b for b in bars}
    del by_time[bars[23]["time"]]
    assert forward_move(by_time, bars[20]["time"], 1)["status"] == "observed"
    assert forward_move(by_time, bars[20]["time"], 5)["status"] == "missing_path"
    del by_time[bars[26]["time"]]
    assert forward_move(by_time, bars[20]["time"], 5)["status"] == "missing_exit"
    del by_time[bars[21]["time"]]
    assert forward_move(by_time, bars[20]["time"], 5)["status"] == "missing_entry"


def test_missing_entry_does_not_erase_signal_and_late_horizon_not_carried_overnight():
    row, bars = fixture()
    original = study([row], {row["date"]: bars})["outcomes"]
    del bars[21]
    changed = study([row], {row["date"]: bars})["outcomes"]
    assert [r["event_id"] for r in changed] == [r["event_id"] for r in original]
    assert all(r["status"] == "missing_entry" for r in changed)
    late = int(datetime(2025, 4, 1, 15, 40, tzinfo=NY).timestamp())
    assert forward_move({}, late, 30)["status"] == "outside_cutoff"


def test_controls_use_only_prior_sessions_and_future_does_not_change_selection():
    pairs = [fixture(f"2025-04-0{i}") for i in range(1, 6)]
    rows = [r for r, _ in pairs]
    minutes = {r["date"]: bars for r, bars in pairs}
    result = study(rows, minutes)
    fourth = [r for r in result["outcomes"] if r["date"] == "2025-04-04"]
    assert fourth and all(r["baseline"]["dates"] == ["2025-04-01", "2025-04-02", "2025-04-03"] for r in fourth)
    assert all(r["baseline"]["status"] == "matched" for r in fourth)
    assert all(r["baseline"]["status"] == "insufficient_prior_sessions"
               for r in result["outcomes"] if r["date"] < "2025-04-04")
    altered = deepcopy(minutes)
    for b in altered["2025-04-05"]:
        b.update(open=49, low=48, high=51, close=50)
    future_changed = study(rows, altered)
    assert [r for r in future_changed["outcomes"] if r["date"] <= "2025-04-04"] == [
        r for r in result["outcomes"] if r["date"] <= "2025-04-04"]
    # Same-session outcome changes affect outcomes, not the earlier selection or reference.
    for b in altered["2025-04-04"][22:]:
        b.update(open=49, low=48, high=51, close=50)
    same_day = [r for r in study(rows, altered)["outcomes"] if r["date"] == "2025-04-04"]
    assert [(r["event_id"], r["baseline"]) for r in same_day] == [(r["event_id"], r["baseline"]) for r in fourth]
    assert same_day[0]["return_bps"] < 0 < fourth[0]["return_bps"]


def test_session_weighting_and_invalid_availability():
    rows = [{"date": "2025-04-01", "bps": 10}]*5+[{"date": "2025-04-02", "bps": -20}]
    assert session_distribution(rows, "bps")["mean"] == -5
    daily, bars = fixture()
    daily["asof"] = daily["date"]
    with pytest.raises(ValueError, match="precede"):
        study([daily], {daily["date"]: bars})
    daily["asof"] = "2025-03-31"
    bars.insert(1, bars[0])
    with pytest.raises(ValueError, match="Unique increasing"):
        study([daily], {daily["date"]: bars})


@pytest.mark.parametrize("mismatch", ["regime", "clock"])
def test_unmatched_regime_or_time_cannot_supply_reference(mismatch):
    pairs = [fixture(f"2025-04-0{i}") for i in range(1, 5)]
    for daily, bars in pairs[:3]:
        if mismatch == "regime":
            daily["regime"] = "trend_up"
        else:
            for bar in bars:
                bar["time"] += 2*3600
    result = study([r for r, _ in pairs], {r["date"]: b for r, b in pairs})
    target = [r for r in result["outcomes"] if r["date"] == "2025-04-04"]
    assert target and all(r["baseline"]["sessions"] == 0 for r in target)
    assert all(r["baseline"]["mean_bps"] is None for r in target)
