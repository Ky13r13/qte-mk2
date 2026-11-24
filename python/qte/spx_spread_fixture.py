"""Synthetic SPXW quotes for testing vertical-spread replay mechanics."""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import random

from .gamma_surface import _black76
from .weekly_reversal_fixture import MINUTE, NY, SCENARIOS, SECOND, YEAR, _ns, _session_dates, _z_path

SEED = 18405
RATE = .04
DIVIDEND_YIELD = .013


def _quote(spot, strike, iv, years, right, width):
    forward = spot * math.exp((RATE - DIVIDEND_YIELD) * years)
    mid = _black76(forward, strike, iv, years, RATE, right)["price"]
    # Floor/ceil to nickel increments for this fixture. These are simulated
    # individual-leg markets, with the complex order modeled by the replay.
    bid = math.floor(max(0, mid - width / 2) * 20) / 20
    ask = math.ceil((mid + width / 2) * 20) / 20
    return bid, ask


def spx_fixture():
    """Supply observed-looking inputs; fixture case names stay in audit metadata."""
    rng = random.Random(SEED)
    sessions, news, metadata = [], [], {}
    for index, (day, (label, daily_iv, width)) in enumerate(zip(_session_dates(), SCENARIOS)):
        session = day.isoformat()
        start = _ns(datetime.combine(day, datetime.min.time(), tzinfo=NY).replace(hour=9, minute=30))
        end = start + 390 * MINUTE
        expiry_day = day + timedelta(days=7)
        expiry = _ns(datetime.combine(expiry_day, datetime.min.time(), tzinfo=NY).replace(hour=16))
        spot_open = 6000 + 5 * index
        expected_move = spot_open * daily_iv * math.sqrt((end - start) / YEAR)
        spot_rows = [spot_open]
        vol_rows = [daily_iv + .025]
        for minute in range(1, 391):
            z = _z_path(label, minute, starting_sign=(-1 if index % 2 else 1))
            if label in {"news_selloff", "late_news_update"} and 20 <= minute <= 40:
                # A brief reentry after a sell-off can look like a reversal.
                # Identical path construction with early vs delayed news tests
                # whether the flag existed before the fill.
                markers = ((20, -.4), (25, -1.25), (30, -1.8), (35, -1.15), (40, -2.0))
                for (start_minute, start_z), (end_minute, end_z) in zip(markers, markers[1:]):
                    if start_minute <= minute <= end_minute:
                        z = start_z + (end_z - start_z) * (minute - start_minute) / (end_minute - start_minute)
                        break
            spot_rows.append(round(spot_open + expected_move * (z + rng.uniform(-.025, .025)), 2))
            vol_rows.append(daily_iv + .025 + (.035 * min(1, abs(z) / 2)
                            if label in {"news_selloff", "late_news_update"} else 0))
        strikes = range(5 * math.floor((min(spot_rows) - 35) / 5),
                        5 * math.ceil((max(spot_rows) + 35) / 5) + 5, 5)
        ids = {(right, strike): f"SPXW-SYNTH-{expiry_day}-{right}-{strike}"
               for right in ("call", "put") for strike in strikes}
        contracts = [{"id": cid, "right": right, "strike": strike, "expiry_ns": expiry,
                      "available_ns": start, "root": "SPXW", "exercise": "european", "settlement": "pm_cash"}
                     for (right, strike), cid in ids.items()]
        bars, quotes = [], []
        if label in {"news_selloff", "late_news_update"}:
            reference = start + (18 if label == "news_selloff" else 27) * MINUTE
            available = reference + (2 * SECOND if label == "news_selloff" else 14 * MINUTE)
            news.extend(({"timestamp_ns": reference, "available_ns": available, "active": True},
                         {"timestamp_ns": end, "available_ns": end, "active": False}))
        for minute, (spot, vol) in enumerate(zip(spot_rows, vol_rows)):
            ts = start + minute * MINUTE
            bars.append({"timestamp_ns": ts, "available_ns": ts + SECOND, "close": spot})
            if minute == 390:
                continue
            quote_ns = ts + 5 * SECOND
            center = 5 * round(spot / 5)
            years = (expiry - quote_ns) / YEAR
            for near_strike in range(center - 55, center + 56, 5):
                for right in ("call", "put"):
                    cid = ids.get((right, near_strike))
                    if cid is None:
                        continue
                    bid, ask = _quote(spot, near_strike, vol, years, right, max(.10, width))
                    quotes.append({"contract_id": cid, "timestamp_ns": quote_ns,
                                   "available_ns": quote_ns + SECOND, "bid": bid, "ask": ask,
                                   "bid_size": 2 if bid else 0, "ask_size": 2})
        sessions.append({"id": session, "open_ns": start, "end_ns": end,
                         "opening_daily_iv": {"timestamp_ns": start, "available_ns": start + SECOND,
                                              "forward": spot_open, "iv": daily_iv, "expiry_ns": end},
                         "bars": bars, "contracts": contracts, "option_quotes": quotes})
        metadata[session] = {"fixture": label, "split": "first_eight" if index < 8 else "last_eight"}
    return sessions, news, metadata
