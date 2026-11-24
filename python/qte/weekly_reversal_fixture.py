"""Designed scenarios for the American weekly-options replay.

The scenario labels describe how paths were constructed and stay outside the
strategy inputs. These fixtures exercise timing, filters and execution math.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
import random
from zoneinfo import ZoneInfo

from .american_option import american_option_price

SECOND = 1_000_000_000
MINUTE = 60 * SECOND
DAY = 24 * 60 * MINUTE
YEAR = 365 * DAY
SEED = 7514
STEPS = 40
NY = ZoneInfo("America/New_York")

SCENARIOS = (
    ("liquid_reversal", .20, .10),
    ("liquid_reversal", .26, .12),
    ("quiet_inside_bands", .17, .08),
    ("news_selloff", .27, .16),
    ("unlabeled_continuation", .22, .14),
    ("wide_spreads", .23, .46),
    ("breakout_after_reentry", .24, .13),
    ("late_news_update", .25, .15),
    ("liquid_reversal", .18, .10),
    ("liquid_reversal", .24, .13),
    ("quiet_inside_bands", .21, .11),
    ("news_selloff", .29, .20),
    ("unlabeled_continuation", .23, .16),
    ("wide_spreads", .20, .52),
    ("breakout_after_reentry", .27, .16),
    ("late_news_update", .24, .16),
)


def _ns(when: datetime) -> int:
    return int(when.astimezone(timezone.utc).timestamp()) * SECOND


def _session_dates():
    # Two groups of eight weekdays within February 2025, avoiding Presidents' Day.
    start = date(2025, 2, 3)
    for offset in range(25):
        day = start + timedelta(days=offset)
        if day.weekday() < 5 and day != date(2025, 2, 17):
            yield day


def _z_path(case: str, minute: int, *, starting_sign: int) -> float:
    if case == "quiet_inside_bands":
        return .49 * math.sin(minute / 10)
    if case in {"news_selloff", "unlabeled_continuation", "late_news_update"}:
        # Late-news case has a first reflex bounce that can lure an early fade.
        if case in {"news_selloff", "late_news_update"} and 31 <= minute <= 40:
            return -1.35 + .065 * (minute - 31)
        slide = max(0, minute - 15)
        return -min(4.6, .075 * slide) + .10 * math.sin(minute / 3)
    cycle, offset = divmod(minute, 51)
    direction = starting_sign * (-1 if cycle % 2 else 1)
    peak = (1.30, 1.82, 2.10, 1.54, 1.93, 1.42, 1.70, 1.35)[cycle % 8]
    if offset <= 15:
        return direction * peak * offset / 15
    if case == "breakout_after_reentry" and 24 <= offset <= 40:
        return direction * min(2.4, .7 + .19 * (offset - 24))
    return direction * max(0.0, peak - .17 * (offset - 15))


def _strike_price(spot, strike, years, iv, spread, option_type):
    mid = american_option_price(spot, strike, iv, years, .04, .006, option_type, steps=STEPS)
    bid = math.floor(max(.01, mid - spread / 2) * 100) / 100
    ask = math.ceil((mid + spread / 2) * 100) / 100
    return bid, max(ask, bid + .01)


def fixture_inputs():
    rng = random.Random(SEED)
    sessions, news, metadata = [], [], {}
    for index, (day, (case, daily_iv, spread)) in enumerate(zip(_session_dates(), SCENARIOS)):
        opened = _ns(datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY))
        closed = opened + 390 * MINUTE
        expiry_day = day + timedelta(days=7)
        # Approximate listed weekly expiries with a seven-calendar-day horizon.
        expiry = _ns(datetime(expiry_day.year, expiry_day.month, expiry_day.day, 16, tzinfo=NY))
        session = day.isoformat()
        metadata[session] = {"case": case, "split": "design" if index < 8 else "evaluation"}
        anchor = round(450 + 1.1 * index, 2)
        expected_move = anchor * daily_iv * math.sqrt((closed - opened) / YEAR)
        strikes = {"call": math.ceil(anchor + 2), "put": math.floor(anchor - 2)}
        contracts = {right: f"SYNTH-{session}-{right}-{strike}" for right, strike in strikes.items()}
        bars, quotes = [], []
        if case in {"news_selloff", "late_news_update"}:
            reference = opened + (18 if case == "news_selloff" else 27) * MINUTE
            published = reference + (2 * SECOND if case == "news_selloff" else 14 * MINUTE)
            news += [{"timestamp_ns": reference, "available_ns": published, "active": True},
                     {"timestamp_ns": closed, "available_ns": closed, "active": False}]
        for minute in range(391):
            ts = opened + minute * MINUTE
            z = _z_path(case, minute, starting_sign=(-1 if index % 2 else 1))
            spot = anchor if minute == 0 else round(anchor + expected_move * (z + rng.uniform(-.035, .035)), 2)
            bars.append({"timestamp_ns": ts, "available_ns": ts + SECOND, "close": spot})
            iv = daily_iv + .025
            if case in {"news_selloff", "late_news_update"}:
                iv += .055 * min(1., abs(z) / 2)
            quote_ts = ts + 5 * SECOND
            if quote_ts > closed:
                break
            for right in ("call", "put"):
                bid, ask = _strike_price(spot, strikes[right], (expiry - quote_ts) / YEAR, iv, spread, right)
                quotes.append({"contract_id": contracts[right], "timestamp_ns": quote_ts,
                               "available_ns": quote_ts + SECOND, "bid": bid, "ask": ask})
        sessions.append({"id": session, "open_ns": opened, "end_ns": closed,
                         "opening_daily_iv": {"timestamp_ns": opened, "available_ns": opened + SECOND,
                                              "forward": anchor, "iv": daily_iv, "expiry_ns": closed},
                         "weekly": {"call_contract_id": contracts["call"], "put_contract_id": contracts["put"],
                                    "expiry_ns": expiry},
                         "bars": bars, "option_quotes": quotes})
    return sessions, news, metadata
