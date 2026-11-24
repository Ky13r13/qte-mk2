import hashlib
import json
import math
import random
from datetime import datetime

import pytest

from qte.reversal_tail import NY, reversals
from qte.s7_fixture import fixture as s7_fixture
from qte.s7_study import study as s7_study
from qte.signal_engine import (
    MINUTE_NS, MinuteBar, MinuteBarBuilder, RecorderFeed, RegimeObservation, SessionContext,
    SigmaReversal, SignalEngine, TrendDay, follow, main, run_session, slot_volumes, volume_baseline,
)

OPEN = int(datetime(2025, 4, 1, 9, 30, tzinfo=NY).timestamp()) * 10**9
CLOSE = OPEN + 390 * MINUTE_NS


def ctx(regime="range", anchor=100.0, move=4.0, vix=20.0, open_ns=OPEN, close_ns=CLOSE, date="2025-04-01"):
    return SessionContext(date, open_ns, close_ns, anchor, move, vix, regime)


def research_rows(bars):
    return [{"date": "2025-04-01", "time": b.start_ns // 10**9, "open": b.open, "high": b.high,
             "low": b.low, "close": b.close} for b in bars]


def bar_at(minute, o, h, l, c, volume=None, open_ns=OPEN):
    start = open_ns + minute * MINUTE_NS
    return MinuteBar(start, start + MINUTE_NS, o, h, l, c, volume)


def research_fixture_bars():
    # Same shape as tests/python/test_reversal_tail.py: a dip to -1.5 sigma that re-enters.
    closes = [100.0] * 19 + [94, 97, 98, 98, 98, 98, 98, 99]
    return [bar_at(31 + i, p - 1, p + 1, p - 1, p) for i, p in enumerate(closes)]


def random_session(seed, anchor=100.0, move=4.0):
    rng = random.Random(seed)
    price, bars = anchor, []
    for m in range(390):
        if rng.random() < 0.03:  # occasional sharp excursions toward the sigma bounds
            price += rng.choice((-1, 1)) * rng.uniform(1.5, 4.0)
        o = price
        price = max(1.0, price + rng.gauss(0, 0.35))
        hi, lo = max(o, price) + rng.uniform(0, 0.3), min(o, price) - rng.uniform(0, 0.3)
        bars.append(bar_at(m, o, hi, lo, price))
    return bars


def research_signal_times(context, bars):
    daily = dict(date="2025-04-01", regime=context.regime, anchor=context.anchor,
                 expected_move=context.expected_move, lower2=context.anchor - 2 * context.expected_move,
                 vix=context.vix)
    trades, _ = reversals([daily], {"2025-04-01": research_rows(bars)})
    return sorted({(t["bound"], t["signal_time"]) for t in trades if t["veto"]})


def engine_signal_times(context, bars):
    signals = run_session(context, bars, reversal=SigmaReversal(bounds=(1, 2)))
    return sorted((int(s.variant[0]), (s.decided_ns - MINUTE_NS) // 10**9)
                  for s in signals if s.status == "signal")


def test_reversal_matches_research_fixture():
    bars = research_fixture_bars()
    c = ctx()
    assert engine_signal_times(c, bars) == research_signal_times(c, bars)
    sig = next(s for s in run_session(c, bars, reversal=SigmaReversal()) if s.status == "signal")
    assert sig.direction == 1 and sig.option_right == "C" and sig.futures_side == "buy"
    assert sig.decided_ns == bars[20].end_ns  # knowable only at the bar's right edge
    assert sig.details["exit_by_ns"] - sig.details["entry_after_ns"] == 6 * MINUTE_NS


@pytest.mark.parametrize("seed", range(25))
@pytest.mark.parametrize("regime,vix", [("range", 18.0), ("trend_down", 27.0), ("high_vol_down", 30.0)])
def test_reversal_matches_research_on_random_sessions(seed, regime, vix):
    bars = random_session(seed)
    c = ctx(regime=regime, vix=vix)
    assert engine_signal_times(c, bars) == research_signal_times(c, bars)


def test_reversal_is_causal_prefix_invariant():
    bars = random_session(7)
    c = ctx()
    full = [s.record() for s in run_session(c, bars, reversal=SigmaReversal(sides=("long", "short")))]
    for cut in (60, 150, 300):
        changed = bars[:cut] + [bar_at(m, 50, 51, 49, 50) for m in range(cut, 390)]
        prefix = [s.record() for s in run_session(c, changed, reversal=SigmaReversal(sides=("long", "short")))]
        cutoff = bars[cut - 1].end_ns
        assert [r for r in prefix if r["decided_ns"] <= cutoff] == [r for r in full if r["decided_ns"] <= cutoff]


def test_stress_veto_and_unclassified_and_short_mirror():
    bars = research_fixture_bars()
    vetoed = run_session(ctx(regime="stress_selloff"), bars, reversal=SigmaReversal())
    assert vetoed and all(s.status == "filtered" and "stress_veto" in s.reasons for s in vetoed)
    assert run_session(ctx(regime="unclassified"), bars, reversal=SigmaReversal()) == []
    mirrored = [MinuteBar(b.start_ns, b.end_ns, 200 - b.close + 1, 200 - b.low, 200 - b.high, 200 - b.close)
                for b in bars]
    shorts = run_session(ctx(), mirrored, reversal=SigmaReversal(sides=("short",)))
    longs = run_session(ctx(), bars, reversal=SigmaReversal())
    assert [(s.decided_ns, s.status) for s in shorts] == [(s.decided_ns, s.status) for s in longs]
    assert all(s.direction == -1 and s.option_right == "P" for s in shorts)


def test_reversal_hold_must_finish_before_close():
    bars = research_fixture_bars()
    # Signal bar ends 15:57; entry 15:58 and a five-minute hold would end after 16:00.
    late = [MinuteBar(b.start_ns + 335 * MINUTE_NS, b.end_ns + 335 * MINUTE_NS, b.open, b.high, b.low, b.close)
            for b in bars if b.start_ns + 335 * MINUTE_NS < CLOSE]
    out = run_session(ctx(), late, reversal=SigmaReversal())
    assert out and all("hold_past_close" in s.reasons for s in out)
    assert research_signal_times(ctx(), late) == []
    on_time = [MinuteBar(b.start_ns + 330 * MINUTE_NS, b.end_ns + 330 * MINUTE_NS, b.open, b.high, b.low, b.close)
               for b in bars]
    assert engine_signal_times(ctx(), on_time) == research_signal_times(ctx(), on_time) != []


def s7_inputs():
    index, volume, schedule, options = s7_fixture("SPX")
    sessions = {d.trading_date: d for d in schedule.days if d.open_ns is not None}
    return index, volume, schedule, options, sessions


def s7_minutes(day, slots):
    bars = []
    for slot, (h, l, c) in sorted(slots.items()):
        for k in range(5):
            m = slot - 5 + k
            if k == 0:
                bars.append(bar_at(m, c, h, l, c, open_ns=day.open_ns))
            else:
                bars.append(bar_at(m, c, c, c, c, open_ns=day.open_ns))
    return bars


def s7_volume_minutes(day, slots):
    return [bar_at(slot - 5 + k, 1, 1, 1, 1, volume=v / 5, open_ns=day.open_ns)
            for slot, v in sorted(slots.items()) for k in range(5)]


def test_trend_day_matches_s7_research_screen():
    index, volume, schedule, options, sessions = s7_inputs()
    report = s7_study(index, volume, schedule, options, "SPX", 10_000, .65)
    expected = {r["date"]: r for r in report["rows"]}
    history = []
    for label in sorted(volume):
        day = sessions[label]
        if label in index:
            snap = options[label]["snapshots"][0]
            straddle = sum((b["bid"] + b["ask"]) / 2 for b in snap["books"])
            regimes = [RegimeObservation(r["source_ns"], r["available_ns"], r["vix"], r["vix3m"], r["gamma"])
                       for r in options[label]["regimes"]]
            c = SessionContext(label, day.open_ns, day.close_ns, 7000, 50, 20, "trend_up")
            out = run_session(c, s7_minutes(day, index[label]), volume_bars=s7_volume_minutes(day, volume[label]),
                              straddles=[(snap["available_ns"], straddle)], regimes=regimes,
                              baseline=volume_baseline(history), trend=TrendDay())
            actionable = [s for s in out if s.status == "signal"]
            sig = expected[label]["signal"]
            assert len(actionable) == 1
            assert actionable[0].decided_ns == sig["at_ns"]
            assert actionable[0].option_right == sig["right"]
            assert actionable[0].reference_price == sig["spot"]
            assert actionable[0].details["relative_volume"] == pytest.approx(sig["relative_volume"])
        history.append(slot_volumes(s7_volume_minutes(day, volume[label]), day.open_ns, day.close_ns))


def test_trend_day_filters_and_halts_on_missing_data():
    index, volume, schedule, options, sessions = s7_inputs()
    label = "2026-11-19"
    day = sessions[label]
    c = SessionContext(label, day.open_ns, day.close_ns, 7000, 50, 20, "trend_up")
    minutes = s7_minutes(day, index[label])
    # No straddle, no regime observation, no volume baseline: candidates are diagnostics only.
    out = run_session(c, minutes, trend=TrendDay())
    assert out and all(s.status == "filtered" for s in out)
    assert set(out[0].reasons) == {"missing_proxy_volume_baseline", "missing_atm_straddle", "missing_regime"}
    # A missing opening-range minute halts the session with no candidates.
    trend = TrendDay()
    assert run_session(c, minutes[:7] + minutes[8:], trend=trend) == []
    assert trend.status == "incomplete_opening_range"


def test_minute_builder_uses_source_time_drops_late_and_gaps():
    b = MinuteBarBuilder(OPEN, CLOSE, grace_ns=2 * 10**9)
    s = 10**9
    assert b.add_tick(OPEN + 1 * s, 100.0) == []
    assert b.add_tick(OPEN + 30 * s, 101.0) == []
    assert b.add_tick(OPEN + 59 * s, 99.5) == []
    [first] = b.add_tick(OPEN + 61 * s, 100.2)
    assert (first.open, first.high, first.low, first.close) == (100.0, 101.0, 99.5, 99.5)
    assert first.end_ns == OPEN + MINUTE_NS
    assert b.add_tick(OPEN + 58 * s, 150.0) == [] and b.late == 1  # cannot revise an emitted bar
    assert b.advance(OPEN + 121 * s) == []  # within grace
    [second] = b.advance(OPEN + 122 * s)
    assert second.start_ns == OPEN + MINUTE_NS
    [third] = b.add_tick(OPEN + 200 * s, 100.0) + b.add_tick(OPEN + 300 * s, 100.0)
    assert third.start_ns == OPEN + 3 * MINUTE_NS  # minute 2 had no ticks: gap, not fabricated
    b.reset()
    assert b.discarded == 1 and b.advance(CLOSE) == []
    assert b.add_tick(CLOSE + s, 100.0) == [] and b.outside == 1


def recorder_rows(bars):
    rows = [dict(event="subscribed", received_utc_ns=OPEN - 10**9)]
    for bar in bars:
        for offset, price in ((1, bar.open), (20, bar.high), (40, bar.low), (59, bar.close)):
            src = bar.start_ns + offset * 10**9
            rows.append(dict(event="underlying", symbol="$SPX", price=price, source_trade_ms=src // 10**6,
                             received_utc_ns=src + 50_000_000))
    return rows


def test_live_recorder_feed_matches_offline_replay(tmp_path):
    bars = research_fixture_bars()
    # Recorder ticks reproduce each bar's OHLC (open, high, low, close in order).
    bars = [MinuteBar(b.start_ns, b.end_ns, b.open, b.high, b.low, b.close) for b in bars]
    c = ctx()
    offline = [s.record() for s in run_session(c, bars, reversal=SigmaReversal(), trend=TrendDay())]
    quotes = tmp_path / "quotes.ndjson"
    quotes.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in recorder_rows(bars)))
    live = follow(quotes, c, tmp_path / "out", sleep=lambda _: None, clock=lambda: CLOSE + 3 * MINUTE_NS)
    assert [s.record() for s in live] == offline
    written = [json.loads(x) for x in (tmp_path / "out/signals.ndjson").read_text().splitlines()]
    assert written == offline and any(r["status"] == "signal" for r in written)


def test_disconnect_discards_partial_minute():
    c = ctx()
    feed = RecorderFeed(SignalEngine(c, reversal=SigmaReversal()))
    feed.apply(dict(event="underlying", price=100.0, source_trade_ms=(OPEN + 10**9) // 10**6, received_utc_ns=OPEN))
    feed.apply(dict(event="disconnect", received_utc_ns=OPEN + 2 * 10**9))
    feed.apply(dict(event="underlying", price=101.0, source_trade_ms=(OPEN + 70 * 10**9) // 10**6,
                    received_utc_ns=OPEN + 70 * 10**9))
    feed.finish()
    assert feed.builder.discarded == 1 and feed.engine.reversal.bars[0]["start_ns"] == OPEN + MINUTE_NS


def test_engine_rejects_unordered_misaligned_and_invalid_inputs():
    e = SignalEngine(ctx(), reversal=SigmaReversal())
    e.on_minute(OPEN + MINUTE_NS, bar_at(0, 100, 101, 99, 100))
    with pytest.raises(ValueError):
        e.on_minute(OPEN + MINUTE_NS, bar_at(0, 100, 101, 99, 100))
    with pytest.raises(ValueError):
        e.on_minute(OPEN + 3 * MINUTE_NS, bar_at(1, 100, 101, 99, 100))
    with pytest.raises(ValueError):
        e.on_minute(CLOSE + MINUTE_NS)
    with pytest.raises(ValueError):
        MinuteBar(OPEN, OPEN + MINUTE_NS, 100, 99, 98, 100)
    with pytest.raises(ValueError):
        ctx(regime="bullish")
    with pytest.raises(ValueError):
        ctx(move=math.nan)
    c = SessionContext.from_prior("2025-04-01", OPEN, CLOSE, 6000, 16, "range")
    assert c.expected_move == pytest.approx(6000 * 0.16 / math.sqrt(252))


def schwab_capture(path, symbol, bars):
    response = dict(symbol=symbol, empty=False, candles=[
        dict(datetime=b.start_ns // 10**6, open=b.open, high=b.high, low=b.low, close=b.close,
             volume=b.volume or 0) for b in bars])
    canonical = json.dumps(response, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    path.write_text(json.dumps(dict(
        provider="schwab", response=response, response_canonical_json_sha256=hashlib.sha256(canonical).hexdigest(),
        request=dict(operation_id="priceHistory", query_parameters=dict(
            symbol=symbol, frequencyType="minute", frequency=1, needExtendedHoursData=False,
            startDate=OPEN // 10**6, endDate=CLOSE // 10**6)))))


def test_replay_cli_is_deterministic(tmp_path):
    bars = research_fixture_bars()
    schwab_capture(tmp_path / "spx.json", "$SPX", bars)
    (tmp_path / "context.json").write_text(json.dumps(dict(schema="qte-signal-context-v1", sessions=[dict(
        trading_date="2025-04-01", open_ns=OPEN, close_ns=CLOSE, prior_close=100.0,
        prior_vix=4.0 * 100 * math.sqrt(252) / 100, regime="range")])))
    outputs = []
    for name in ("a", "b"):
        assert main(["replay", "--minutes", str(tmp_path / "spx.json"), "--context", str(tmp_path / "context.json"),
                     "--output", str(tmp_path / name)]) == 0
        outputs.append(((tmp_path / name / "signals.ndjson").read_bytes(),
                        (tmp_path / name / "manifest.json").read_bytes()))
    assert outputs[0] == outputs[1]
    manifest = json.loads(outputs[0][1])
    assert manifest["actionable"] >= 1 and len(manifest["sources"]) == 2
    # Output directories are never overwritten.
    assert main(["replay", "--minutes", str(tmp_path / "spx.json"), "--context", str(tmp_path / "context.json"),
                 "--output", str(tmp_path / "a")]) == 1


def test_qte_cli_dispatches_signals(tmp_path):
    from qte.cli import main as qte_main
    bars = research_fixture_bars()
    schwab_capture(tmp_path / "spx.json", "$SPX", bars)
    (tmp_path / "context.json").write_text(json.dumps(dict(schema="qte-signal-context-v1", sessions=[dict(
        trading_date="2025-04-01", open_ns=OPEN, close_ns=CLOSE, prior_close=100.0,
        prior_vix=4.0 * math.sqrt(252), regime="range")])))
    assert qte_main(["signals", "replay", "--minutes", str(tmp_path / "spx.json"), "--context",
                     str(tmp_path / "context.json"), "--output", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out/signals.ndjson").read_bytes()
