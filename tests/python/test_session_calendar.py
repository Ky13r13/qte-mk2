"""Declared, offline session fixtures; no official-calendar inference is tested."""
from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

import qte
import qte.session_calendar as calendar_module
from qte.adapters.common import interval_for_timeframe
from qte.session_calendar import SessionDay, SessionSchedule, SessionWindow


NS = 1_000_000_000
HOUR = 3_600 * NS
DAY = 86_400 * NS
INT64_MIN = -(1 << 63)
EPOCH = date(1970, 1, 1)
SHA = "a" * 64


def utc(day: str, hour: int = 0, minute: int = 0, second: int = 0) -> int:
    """Independent, integer UTC arithmetic (no timezone database or float time)."""
    return ((date.fromisoformat(day) - EPOCH).days * DAY
            + hour * HOUR + minute * 60 * NS + second * NS)


def opened(day: str, hour: int = 14, minute: int = 30, hours: int = 6, tail: int = 30) -> SessionDay:
    start = utc(day, hour, minute)
    return SessionDay(day, start, start + hours * HOUR + tail * 60 * NS)


def schedule(days, start=None, end=None, **changes) -> SessionSchedule:
    return SessionSchedule(
        start or days[0].trading_date, end or days[-1].trading_date,
        changes.pop("source_id", "synthetic-us-session-fixture"),
        changes.pop("source_sha256", SHA), changes.pop("evidence", "synthetic"),
        changes.pop("days", days), **changes,
    )


@pytest.fixture
def dst_schedule() -> SessionSchedule:
    # These are declared UTC fixtures.  The March shift is not inferred here.
    return schedule([
        opened("2025-03-07", 14, 30), SessionDay("2025-03-08"), SessionDay("2025-03-09"),
        opened("2025-03-10", 13, 30),
    ])


def test_hourly_and_daily_windows_are_open_anchored_and_complete(dst_schedule):
    regular = dst_schedule.buckets("2025-03-07")
    assert len(regular) == 7
    assert [(item.start_ns, item.end_ns) for item in regular] == [
        (utc("2025-03-07", 14, 30) + index * HOUR,
         utc("2025-03-07", 14, 30) + min(index + 1, 6) * HOUR if index < 6
         else utc("2025-03-07", 21)) for index in range(7)
    ]
    assert regular[-1].end_ns - regular[-1].start_ns == 30 * 60 * NS
    daily = dst_schedule.buckets("2025-03-07", kind="daily")
    assert daily == (SessionWindow("2025-03-07", utc("2025-03-07", 14, 30), utc("2025-03-07", 21), utc("2025-03-07", 21)),)
    assert dst_schedule.buckets("2025-03-08", kind="daily") == ()


def test_short_and_exact_hour_sessions_have_no_missing_or_empty_tail():
    short = schedule([opened("2025-07-03", 13, 30, 3, 30)])
    short_windows = short.buckets("2025-07-03")
    assert len(short_windows) == 4
    assert short_windows[-1].end_ns - short_windows[-1].start_ns == 30 * 60 * NS
    exact = schedule([SessionDay("2025-07-07", utc("2025-07-07", 13), utc("2025-07-07", 17))])
    assert [(window.start_ns, window.end_ns) for window in exact.buckets("2025-07-07")] == [
        (utc("2025-07-07", hour), utc("2025-07-07", hour + 1)) for hour in range(13, 17)
    ]


def test_declared_dst_holidays_early_close_and_exceptional_closure_are_not_verified():
    spring = schedule([opened("2025-03-07", 14, 30), SessionDay("2025-03-08"), SessionDay("2025-03-09"), opened("2025-03-10", 13, 30)])
    fall = schedule([opened("2025-10-31", 13, 30), SessionDay("2025-11-01"), SessionDay("2025-11-02"), opened("2025-11-03", 14, 30)])
    declared = schedule([SessionDay("2025-11-27"), opened("2025-11-28", 14, 30, 3, 30),
                         SessionDay("2025-11-29"), SessionDay("2025-11-30"), SessionDay("2025-12-01")])
    assert spring.day("2025-03-10").open_ns == utc("2025-03-10", 13, 30)
    assert fall.day("2025-11-03").open_ns == utc("2025-11-03", 14, 30)
    assert [spring.day(day).open_ns for day in ("2025-03-08", "2025-03-09")] == [None, None]
    assert declared.evidence == "synthetic"
    assert declared.buckets("2025-11-27") == declared.buckets("2025-12-01") == ()
    assert len(declared.buckets("2025-11-28")) == 4


@pytest.mark.parametrize("value", ["2025-2-03", "2025-02-30", 20250203, None])
def test_date_labels_are_canonical(value):
    with pytest.raises(ValueError):
        SessionDay(value)


@pytest.mark.parametrize("open_ns,close_ns", [
    (True, utc("2025-01-02", 16)), (utc("2025-01-02", 15), False),
    (1.5, utc("2025-01-02", 16)), (utc("2025-01-02", 16), utc("2025-01-02", 15)),
    (1 << 63, (1 << 63) + 1), (utc("2025-01-02", 23), utc("2025-01-03", 0, 1)),
])
def test_session_day_rejects_partial_reversed_wrong_date_and_non_int64(open_ns, close_ns):
    with pytest.raises(ValueError):
        SessionDay("2025-01-02", open_ns, close_ns)
    with pytest.raises(ValueError):
        SessionDay("2025-01-02", open_ns, None)


@pytest.mark.parametrize("args", [
    ("2025-01-02", True, utc("2025-01-02", 1), utc("2025-01-02", 1)),
    ("2025-01-02", utc("2025-01-02", 1), utc("2025-01-02", 1), utc("2025-01-02", 1)),
    ("2025-01-02", utc("2025-01-02", 23), utc("2025-01-03", 0, 1), utc("2025-01-03", 0, 1)),
])
def test_window_rejects_invalid_order_types_and_cross_date(args):
    with pytest.raises(ValueError):
        SessionWindow(*args)


@pytest.mark.parametrize("available", [utc("2025-01-02", 1) - 1, 1.5, True, 1 << 63])
def test_window_rejects_availability_before_end_and_non_int64(available):
    with pytest.raises(ValueError):
        SessionWindow("2025-01-02", utc("2025-01-02"), utc("2025-01-02", 1), available)


def test_schedule_requires_complete_ordered_inclusive_coverage_and_declared_metadata():
    first, second = SessionDay("2025-01-02"), SessionDay("2025-01-03")
    cases = [
        lambda: schedule([first], "2025-01-02", "2025-01-03"),
        lambda: schedule([first, first], "2025-01-02", "2025-01-03"),
        lambda: schedule([second, first]),
        lambda: schedule([second, first], "2025-01-02", "2025-01-03"),
        lambda: schedule([first, SessionDay("2025-01-04")], "2025-01-02", "2025-01-03"),
        lambda: schedule([first, second], "2025-01-03", "2025-01-02"),
        lambda: SessionSchedule("2025-01-02", "2025-01-02", "", SHA, "synthetic", [first]),
        lambda: schedule([first], source_id=" trimmed"),
        lambda: schedule([first], source_id="bad\nsource"),
        lambda: schedule([first], source_id="x" * 257),
        lambda: schedule([first], source_sha256="A" * 64),
        lambda: schedule([first], source_sha256="g" * 64),
        lambda: schedule([first], source_sha256="a" * 63),
        lambda: schedule([first], evidence="official"),
    ]
    for make in cases:
        with pytest.raises(ValueError):
            make()


def test_wrong_length_builtin_list_is_rejected_before_any_tuple_copy(monkeypatch):
    def forbidden_copy(_):
        raise AssertionError("an invalid schedule must not copy its days")

    monkeypatch.setattr(calendar_module, "tuple", forbidden_copy, raising=False)
    with pytest.raises(ValueError):
        SessionSchedule("2025-01-02", "2025-01-03", "synthetic", SHA, "synthetic", [SessionDay("2025-01-02")])


@pytest.mark.parametrize("days", [[], ["not-a-day"], iter([SessionDay("2025-01-02")]), {SessionDay("2025-01-02")}])
def test_schedule_rejects_empty_wrong_entry_and_non_sequence_days(days):
    with pytest.raises(ValueError):
        SessionSchedule("2025-01-02", "2025-01-02", "synthetic", SHA, "synthetic", days)


def test_schedule_rejects_coverage_over_the_documented_cap_before_day_handling():
    with pytest.raises(ValueError):
        SessionSchedule("2000-01-01", "2101-01-01", "synthetic", SHA, "synthetic", [])


def test_closed_and_missing_dates_are_distinct_and_invalid_bucket_arguments_fail_first(dst_schedule):
    assert dst_schedule.day("2025-03-08").open_ns is None
    with pytest.raises(ValueError):
        dst_schedule.day("2025-03-11")
    for kwargs in ({"kind": "weekly"}, {"publication_delay_ns": -1}, {"publication_delay_ns": True}):
        with pytest.raises(ValueError):
            dst_schedule.buckets("2025-03-08", **kwargs)


def test_delays_are_exact_checked_and_may_cross_a_date_boundary_when_declared():
    item = schedule([SessionDay("2025-01-02", utc("2025-01-02", 23), utc("2025-01-02", 23, 30))])
    window = item.buckets("2025-01-02", kind="daily", publication_delay_ns=HOUR)[0]
    assert window.available_at_ns == utc("2025-01-03", 0, 30)
    for delay in (1.0, True, -1, 1 << 63):
        with pytest.raises(ValueError):
            item.buckets("2025-01-02", publication_delay_ns=delay)
    edge = schedule([SessionDay("2262-04-11", (1 << 63) - HOUR, (1 << 63) - 1)])
    with pytest.raises(ValueError):
        edge.buckets("2262-04-11", publication_delay_ns=1)


def test_negative_epoch_and_exclusive_midnight_are_valid_but_later_next_day_is_not():
    pre_epoch = SessionDay("1969-12-31", utc("1969-12-31", 23), utc("1970-01-01"))
    assert schedule([pre_epoch]).buckets("1969-12-31", kind="daily")[0].end_ns == 0
    with pytest.raises(ValueError):
        SessionDay("1969-12-31", utc("1969-12-31", 23), utc("1970-01-01", 0, 0, 1))


def test_int64_minimum_and_one_nanosecond_delay_remain_exact():
    label = (EPOCH + timedelta(days=INT64_MIN // DAY)).isoformat()
    edge = SessionDay(label, INT64_MIN, INT64_MIN + 1)
    assert edge.close_ns - edge.open_ns == 1
    item = schedule([SessionDay("2025-01-02", utc("2025-01-02"), utc("2025-01-02") + 1)])
    assert item.buckets("2025-01-02", kind="daily", publication_delay_ns=1)[0].available_at_ns == utc("2025-01-02") + 2


def test_schedule_owns_day_list_is_frozen_and_hashes_all_declared_fields():
    supplied = [opened("2025-01-02")]
    item = schedule(supplied)
    before = item.snapshot_sha256
    supplied.clear()
    assert len(item.days) == 1 and item.snapshot_sha256 == before
    with pytest.raises(FrozenInstanceError):
        item.days[0].open_ns = 0
    with pytest.raises(FrozenInstanceError):
        item.source_id = "changed"
    start = utc("2025-01-02")
    window = SessionWindow("2025-01-02", start, start + 1, start + 1)
    with pytest.raises(FrozenInstanceError):
        window.available_at_ns = start + 2
    variants = [
        schedule([SessionDay("2025-01-02")]),
        schedule([opened("2025-01-02", 14, 30, 6, 29)]),
        schedule([opened("2025-01-02")], source_id="another-source"),
        schedule([opened("2025-01-02")], source_sha256="b" * 64),
        schedule([opened("2025-01-02")], evidence="historical_declared"),
    ]
    assert item.snapshot_sha256 == schedule([opened("2025-01-02")]).snapshot_sha256
    assert all(item.snapshot_sha256 != other.snapshot_sha256 for other in variants)


def test_close_grid_omits_closed_days_and_keeps_early_close():
    item = schedule([opened("2025-11-27", 14, 30), SessionDay("2025-11-28"),
                     SessionDay("2025-11-29"), SessionDay("2025-11-30"), opened("2025-12-01", 14, 30, 3, 30)])
    assert item.session_close_grid() == (utc("2025-11-27", 21), utc("2025-12-01", 18))
    all_closed = schedule([SessionDay("2025-12-06"), SessionDay("2025-12-07")])
    assert all_closed.buckets("2025-12-06") == ()
    assert all_closed.session_close_grid() == ()


def test_existing_core_still_requires_fixed_duration_bars_and_adapters_reject_calendar_timeframes():
    bars = [
        qte.Bar("SPY", 0, HOUR, 1, 1, 1, 1, 1),
        qte.Bar("SPY", HOUR, HOUR + 30 * 60 * NS, 1, 1, 1, 1, 1),
    ]
    with pytest.raises(ValueError):
        qte.Dataset.from_bars(bars, interval_ns=HOUR, source_id="mixed-duration-calendar-regression")
    with pytest.raises(ValueError, match="calendar bars"):
        interval_for_timeframe("1Day")
