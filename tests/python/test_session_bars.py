from __future__ import annotations

import calendar
from datetime import datetime

import pytest

import qte
from qte.adapters import build_session_dataset
from qte.session_calendar import SessionDay, SessionSchedule

NS = 1_000_000_000
HOUR = 3_600 * NS
SOURCE_HASH = "a" * 64


def utc(day: str, hour: int = 0, minute: int = 0) -> int:
    value = datetime.strptime(f"{day} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
    return calendar.timegm(value.timetuple()) * NS


def schedule(days, *, source_id="declared-calendar", source_hash="b" * 64):
    return SessionSchedule(
        days[0].trading_date, days[-1].trading_date, source_id, source_hash,
        "historical_declared", days,
    )


def opened(day: str, close_hour: int = 21) -> SessionDay:
    return SessionDay(day, utc(day, 14, 30), utc(day, close_hour))


def bar(symbol: str, start: int, end: int, price: float = 100.0) -> qte.Bar:
    return qte.Bar(symbol, start, end, price, price + 2, price - 2, price + 1, 1000)


def build(bars, declared, *, kind="hourly", delay=0, source_id="bars"):
    return build_session_dataset(
        bars, schedule=declared, kind=kind, publication_delay_ns=delay,
        source_id=source_id, source_sha256=SOURCE_HASH, action_free=True,
    )


def test_daily_and_hourly_tail_buckets_are_matched_without_fill_or_aggregation():
    regular = opened("2025-11-26")
    short = opened("2025-11-27", 18)
    declared = schedule([regular, short])
    daily = [
        bar("SPY", regular.open_ns, regular.close_ns),
        bar("SPY", short.open_ns, short.close_ns, 110),
    ]
    daily_result = build(daily, declared, kind="daily")
    assert daily_result.dataset.bar_count == 2
    assert daily_result.dataset.source_id == "bars"
    assert daily_result.provenance.provider == "session_declared"
    assert daily_result.provenance.schema_id == "qte-session-bars-v1"
    assert daily_result.provenance.adapter_version == "1"
    assert daily_result.provenance.source_sha256 == SOURCE_HASH

    windows = declared.buckets("2025-11-26", kind="hourly")
    assert windows[-1].end_ns - windows[-1].start_ns == 30 * 60 * NS
    # Missing buckets are allowed and are not synthesized.
    hourly_result = build([
        bar("SPY", windows[0].start_ns, windows[0].end_ns),
        bar("SPY", windows[-1].start_ns, windows[-1].end_ns, 120),
    ], declared)
    assert hourly_result.dataset.bar_count == 2
    assumptions = hourly_result.provenance.assumptions
    assert f"calendar_snapshot_sha256={declared.snapshot_sha256}" in assumptions
    assert "calendar_evidence=historical_declared" in assumptions
    assert "execution=at_bar_start_v1" in assumptions
    assert "ordering=strict_per_symbol_v1" in assumptions
    assert "calendar_verification=declared-not-verified" in assumptions
    assert "adjustment=raw" in assumptions
    assert "action_free=true" in assumptions


def test_delayed_publication_replay_cannot_fill_at_an_already_processed_open():
    day = SessionDay("2025-01-02", utc("2025-01-02", 14), utc("2025-01-02", 18))
    declared = schedule([day])
    windows = declared.buckets(day.trading_date, kind="hourly")
    prices = [100, 110, 120, 130]
    bars = [bar("SPY", window.start_ns, window.end_ns, price)
            for window, price in zip(windows, prices, strict=True)]
    data = build(bars, declared, delay=HOUR).dataset

    class BuyFirstPublished(qte.Strategy):
        def __init__(self):
            super().__init__()
            self.sent = False

        def on_bar(self, context, completed):
            if not self.sent:
                context.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 1))
                self.sent = True

    result = qte.BacktestEngine(qte.BacktestConfig(1_000)).run(
        data, BuyFirstPublished())
    assert len(result.fills) == 1
    # The first bar is released at the third opening, so the already elapsed
    # second opening (110) is unavailable; execution may use the current open.
    assert result.fills[0].executed_price == 120


def test_mixed_daily_sessions_replay_and_sample_without_frequency_inference():
    days = [opened("2025-01-02"), SessionDay("2025-01-03"), opened("2025-01-04", 18)]
    declared = schedule(days)
    values = [bar("SPY", day.open_ns, day.close_ns, price)
              for day, price in ((days[0], 100), (days[2], 110))]
    data = build(values, declared, kind="daily").dataset

    class BuyOnce(qte.Strategy):
        def on_bar(self, ctx, value):
            if len(ctx.history("SPY")) == 1:
                ctx.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 1))

    result = qte.BacktestEngine(qte.BacktestConfig(1000)).run(data, BuyOnce())
    assert len(result.fills) == 1
    assert result.fills[0].effective_ns == days[2].open_ns
    sampling = qte.SamplingConfig([data.start_ns, *declared.session_close_grid()])
    assert [p.equity for p in qte.sample_equity(result, sampling)] == [1000, 1000, 1001]
    report = qte.analyze(result, qte.AnnualizationConfig(252), sampling)
    assert report.returns == pytest.approx([0, .001])
    assert report.annualized_volatility.value is not None
    assert qte.analyze(result).annualized_return.value is None


@pytest.mark.parametrize("change", [
    {"action_free": False},
    {"action_free": 1},
    {"source_id": ""},
    {"source_id": " padded "},
    {"source_id": "x\n"},
    {"source_id": "x" * 257},
    {"source_sha256": "A" * 64},
    {"source_sha256": "a" * 63},
    {"kind": "weekly"},
    {"publication_delay_ns": -1},
    {"publication_delay_ns": True},
])
def test_invalid_metadata_and_policy_are_rejected_even_for_empty_bars(change):
    declared = schedule([SessionDay("2025-01-02")])
    values = dict(
        schedule=declared, kind="hourly", publication_delay_ns=0,
        source_id="bars", source_sha256=SOURCE_HASH, action_free=True,
    )
    values.update(change)
    with pytest.raises(ValueError):
        build_session_dataset([], **values)


def test_closed_outside_and_misaligned_windows_are_rejected():
    declared = schedule([
        opened("2025-01-02"), SessionDay("2025-01-03"), opened("2025-01-04"),
    ])
    cases = [
        bar("SPY", utc("2025-01-03", 14), utc("2025-01-03", 15)),
        bar("SPY", utc("2025-01-05", 14), utc("2025-01-05", 15)),
        bar("SPY", utc("2025-01-02", 14, 31), utc("2025-01-02", 15, 31)),
    ]
    for item in cases:
        with pytest.raises(ValueError):
            build([item], declared)
    with pytest.raises(ValueError):
        build_session_dataset((item for item in cases), schedule=declared,
            source_id="bars", source_sha256=SOURCE_HASH, action_free=True)


def test_hash_commits_calendar_revision_delay_and_preserves_core_order_checks():
    first_schedule = schedule([opened("2025-01-02")])
    revised_schedule = schedule([opened("2025-01-02")], source_id="revision-two")
    window = first_schedule.buckets("2025-01-02")[0]
    bars = [bar("SPY", window.start_ns, window.end_ns)]
    baseline = build(bars, first_schedule).dataset
    assert baseline.hash != build(bars, revised_schedule).dataset.hash
    assert baseline.hash != build(bars, first_schedule, delay=1).dataset.hash

    duplicate = bars + bars
    with pytest.raises(ValueError):
        build(duplicate, first_schedule)
    windows = first_schedule.buckets("2025-01-02")
    reversed_bars = [
        bar("SPY", windows[1].start_ns, windows[1].end_ns),
        bar("SPY", windows[0].start_ns, windows[0].end_ns),
    ]
    with pytest.raises(ValueError):
        build(reversed_bars, first_schedule)
