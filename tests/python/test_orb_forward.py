import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from qte import orb_forward as of

NY = ZoneInfo("America/New_York")
MIN = of.MINUTE


def at(hh, mm, day="2026-11-24"):
    return int(datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}").replace(tzinfo=NY).timestamp()) * 10**9


DAY, PREVIOUS = of.session(at(12, 0))


def candle(start_ns, o, h, l, c, v=1000.0):
    return dict(datetime=start_ns // 10**6, open=o, high=h, low=l, close=c, volume=v)


def spx_series(path):
    """SPX 5-minute candles for the session from a list of closes (first two form the range)."""
    rows, prev = [], path[0]
    for k, c in enumerate(path):
        rows.append(candle(DAY.open_ns + k * 5 * MIN, prev, max(prev, c) + 0.5, min(prev, c) - 0.5, c))
        prev = c
    return rows


def spy_series(heavy_slot=None, volume=1000.0, heavy=3000.0):
    rows = []
    for d in PREVIOUS[-20:] + [DAY]:
        for k in range(78):
            v = heavy if (d is DAY and k == heavy_slot) else volume
            rows.append(candle(d.open_ns + k * 5 * MIN, 600, 601, 599, 600, v))
    return rows


def test_evaluate_signal_volume_filter_and_deadline():
    spx = of.candles(dict(response=dict(candles=spx_series([7000, 7002] + [7001] * 3 + [7010] + [7010] * 40))), at(12, 0))
    spy = of.candles(dict(response=dict(candles=spy_series(heavy_slot=5))), at(12, 0))
    d = of.evaluate(DAY, PREVIOUS, spx, spy, 7000.0, 15.0, at(12, 0))
    assert d["status"] == "signal" and d["side"] == 1 and d["bar_start_ns"] == DAY.open_ns + 25 * MIN
    quiet = of.candles(dict(response=dict(candles=spy_series(heavy_slot=None))), at(12, 0))
    assert of.evaluate(DAY, PREVIOUS, spx, quiet, 7000.0, 15.0, at(12, 0))["status"] == "no_signal"
    assert of.evaluate(DAY, PREVIOUS, spx, quiet, 7000.0, 15.0, at(10, 30))["status"] == "no_signal_yet"
    wide = of.candles(dict(response=dict(candles=spx_series([7000, 7060] + [7030] * 40))), at(12, 0))
    assert of.evaluate(DAY, PREVIOUS, wide, spy, 7000.0, 15.0, at(12, 0))["status"] == "range_too_wide"


def test_candles_exclude_the_bar_still_forming():
    rows = spx_series([7000, 7001, 7002])
    done = of.candles(dict(response=dict(candles=rows)), DAY.open_ns + 12 * MIN)
    assert sorted(done) == [DAY.open_ns, DAY.open_ns + 5 * MIN]


def test_exit_rules_midpoint_time_and_close():
    entry = dict(entry_ns=DAY.open_ns + 31 * MIN, side=1, midpoint=7001.0)
    falling = of.candles(dict(response=dict(candles=spx_series([7000, 7002] + [7010] * 6 + [6999] + [6999] * 60))), at(15, 0))
    assert of.exit_reason(entry, falling, DAY, at(15, 0))[0] == "midpoint_stop"
    flat = of.candles(dict(response=dict(candles=spx_series([7000, 7002] + [7010] * 70))), at(15, 0))
    reason, end = of.exit_reason(entry, flat, DAY, at(15, 0))
    assert reason == "time_stop" and end - entry["entry_ns"] >= 60 * MIN


def chain(spot, bid, ask, right="C"):
    field = "callExpDateMap" if right == "C" else "putExpDateMap"
    strike = 5 * round(spot / 5)
    row = dict(symbol=f"SPXW  261124{right}0{strike * 1000:07d}", bid=bid, ask=ask, volatility=20.0,
               settlementType="P", multiplier=100)
    return dict(response={"underlyingPrice": spot, field: {"2026-11-24:0": {f"{strike:.1f}": [row]}}})


def daily(close, days=("2026-11-20", "2026-11-23")):
    rows = [dict(datetime=int(datetime.fromisoformat(d).replace(tzinfo=ZoneInfo("America/Chicago")).timestamp() * 1000),
                 open=close, high=close + 1, low=close - 1, close=close, volume=0) for d in days]
    return dict(response=dict(candles=rows))


def test_cycle_enters_at_ask_exits_at_bid_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(of.truth_db, "TRUTH_ROOT", tmp_path)
    entries, exits = tmp_path / "entries.ndjson", tmp_path / "exits.ndjson"
    path = [7000, 7002] + [7001] * 3 + [7010] + [7010] * 70
    quotes = {"bid": 9.8, "ask": 10.2}

    def fake(directory, now, label):
        directory.mkdir(parents=True)
        files = {"spx-5m": dict(response=dict(candles=spx_series(path))),
                 "spy-5m": dict(response=dict(candles=spy_series(heavy_slot=5))),
                 "spx-1d": daily(7000.0), "vix-1d": daily(15.0),
                 "spx-chain-0dte": chain(7010.0, quotes["bid"], quotes["ask"])}
        for name, doc in files.items():
            (directory / f"{name}.json").write_text(json.dumps(doc))

    first = of.cycle(at(10, 2), capture_fn=fake, entries=entries, exits=exits)
    assert first["status"] == "entered" and first["paid"] == 10.2 and first["side"] == 1
    assert of.cycle(at(10, 30), capture_fn=fake, entries=entries, exits=exits)["status"] == "holding"
    quotes.update(bid=12.0, ask=12.4)
    out = of.cycle(at(11, 5), capture_fn=fake, entries=entries, exits=exits)
    assert out["status"] == "exited" and out["reason"] == "time_stop" and out["sold"] == 12.0
    assert out["pnl_usd"] == pytest.approx((12.0 - 10.2) * 100 - 2 * 1.25)
    assert of.cycle(at(11, 10), capture_fn=fake, entries=entries, exits=exits)["status"] == "finished"
    s = of.status(entries, exits)
    assert s["trades"] == 1 and s["decision"] == "pending" and s["total_pnl_usd"] == out["pnl_usd"]


def test_late_signal_is_recorded_as_missed(tmp_path, monkeypatch):
    monkeypatch.setattr(of.truth_db, "TRUTH_ROOT", tmp_path)
    path = [7000, 7002] + [7001] * 3 + [7010] + [7010] * 70

    def fake(directory, now, label):
        directory.mkdir(parents=True)
        for name, doc in {"spx-5m": dict(response=dict(candles=spx_series(path))),
                          "spy-5m": dict(response=dict(candles=spy_series(heavy_slot=5))),
                          "spx-1d": daily(7000.0), "vix-1d": daily(15.0),
                          "spx-chain-0dte": chain(7010.0, 9.8, 10.2)}.items():
            (directory / f"{name}.json").write_text(json.dumps(doc))
    out = of.cycle(at(10, 40), capture_fn=fake, entries=tmp_path / "e.ndjson", exits=tmp_path / "x.ndjson")
    assert out["status"] == "missed"
