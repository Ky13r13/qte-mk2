"""Point-in-time research replay of defined-risk SPXW debit verticals."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
import math

from .weekly_reversal import FIVE_MIN_NS, MAX_ENTRIES, YEAR_NS

DAY_NS = YEAR_NS // 365
QUOTE_AGE_NS = 30_000_000_000
WIDTHS = (5, 10, 20)
REGIMES = ("range", "reentry", "continuation", "shock", "news")
MULTIPLIER = 100


def _fields(row, expected, description):
    if type(row) is not dict or set(row) != set(expected):
        raise ValueError(f"invalid {description} fields")


def _finite(value, name):
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be finite")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _ns(value):
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise ValueError("invalid signed nanosecond timestamp")
    return value


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError("invalid session or contract identity")
    return value


@dataclass(frozen=True)
class _Contract:
    id: str
    right: str
    strike: int
    expiry: int
    available: int


@dataclass(frozen=True)
class _Quote:
    available: int
    bid: float
    ask: float
    bid_size: int
    ask_size: int


@dataclass(frozen=True)
class _Decision:
    reference: int
    available: int
    spot: float
    regime: str
    right: str | None
    distance_sigma: float
    peak_sigma: float


class _News:
    def __init__(self, flags):
        ordered = []
        for row in flags:
            _fields(row, ("timestamp_ns", "available_ns", "active"), "news")
            timestamp, available = _ns(row["timestamp_ns"]), _ns(row["available_ns"])
            if timestamp > available or type(row["active"]) is not bool:
                raise ValueError("invalid news observation")
            ordered.append((available, timestamp, row["active"]))
        ordered.sort()
        if len({(available, timestamp) for available, timestamp, _ in ordered}) != len(ordered):
            raise ValueError("duplicate news observation")
        self.times = []
        self.active = []
        most_recent = -(2**63)
        for available, timestamp, active in ordered:
            # A late release about an older event cannot re-open a cleared state.
            if timestamp >= most_recent:
                most_recent = timestamp
                self.times.append(available)
                self.active.append(active)

    def active_at(self, when):
        index = bisect_right(self.times, when) - 1
        return index >= 0 and self.active[index]


def _decisions(rows, start, end, available, anchor, move, news):
    observations = []
    previous_time = None
    for row in rows:
        _fields(row, ("timestamp_ns", "available_ns", "close"), "underlying observation")
        ts, observed, close = _ns(row["timestamp_ns"]), _ns(row["available_ns"]), _finite(row["close"], "close")
        if (not start <= ts <= end or ts > observed or close <= 0
                or previous_time is not None and ts <= previous_time):
            raise ValueError("invalid underlying observation or order")
        previous_time = ts
        if (ts - start) % FIVE_MIN_NS == 0:
            observations.append((ts, observed, close))
    peak = 0.0
    peak_side = 0
    for left, right in zip(observations, observations[1:]):
        prior_time, prior_observed, prior_spot = left
        timestamp, observed, spot = right
        if (observed < available or observed + FIVE_MIN_NS >= end or prior_observed > observed
                or observed - timestamp > FIVE_MIN_NS or prior_observed - prior_time > FIVE_MIN_NS
                or timestamp - prior_time != FIVE_MIN_NS):
            peak = 0.0
            peak_side = 0
            continue
        before, now = (prior_spot - anchor) / move, (spot - anchor) / move
        if not math.isfinite(before) or not math.isfinite(now):
            raise ValueError("non-finite expected-move distance")
        side = (now > 0) - (now < 0)
        same_side = before * now > 0
        distance = abs(now)
        if distance >= 1.5:
            peak = max(distance, abs(before) if same_side else 0.0,
                       peak if peak_side == side else 0.0)
            peak_side = side
        excursion_peak = max(distance, abs(before) if same_side else 0.0,
                             peak if peak_side == side else 0.0)
        reentry = same_side and abs(before) >= 1.5 and distance < 1.5
        if distance < 1.5:
            peak = 0.0
            peak_side = 0
        if news.active_at(observed):
            regime = "news"
        elif excursion_peak >= 2.25:
            regime = "shock"
        elif reentry:
            regime = "reentry"
        elif same_side and distance >= 1.5 and distance > abs(before):
            regime = "continuation"
        else:
            regime = "range"
        option_right = ("put" if side > 0 else "call") if reentry else None
        yield _Decision(timestamp, observed, spot, regime, option_right, distance, excursion_peak)


def _contracts(rows, start, end):
    contracts = {}
    keys = set()
    for row in rows:
        _fields(row, ("id", "right", "strike", "expiry_ns", "available_ns", "root", "exercise",
                      "settlement"), "SPXW contract")
        identity, expiry, available = _id(row["id"]), _ns(row["expiry_ns"]), _ns(row["available_ns"])
        strike, right = row["strike"], row["right"]
        if (row["root"] != "SPXW" or row["exercise"] != "european" or row["settlement"] != "pm_cash"
                or right not in ("call", "put") or type(strike) is not int or not 0 < strike <= 1_000_000
                or expiry <= end or not 5 * DAY_NS <= expiry - start <= 9 * DAY_NS
                or available > end or identity in contracts or (expiry, right, strike) in keys):
            raise ValueError("invalid or ambiguous 5–9 DTE European PM-cash SPXW contract")
        contracts[identity] = _Contract(identity, right, strike, expiry, available)
        keys.add((expiry, right, strike))
    return contracts


def _quotes(rows, contracts, start, end):
    series = defaultdict(dict)
    seen = set()
    for row in rows:
        _fields(row, ("contract_id", "timestamp_ns", "available_ns", "bid", "ask", "bid_size", "ask_size"),
                "option quote")
        identity, ts, available = _id(row["contract_id"]), _ns(row["timestamp_ns"]), _ns(row["available_ns"])
        bid, ask = _finite(row["bid"], "bid"), _finite(row["ask"], "ask")
        bid_size, ask_size = row["bid_size"], row["ask_size"]
        if (identity not in contracts or (identity, ts) in seen or not start <= ts <= end
                or ts > available or available < contracts[identity].available
                or not 0 <= bid <= ask <= 1_000_000 or ask <= 0
                or type(bid_size) is not int or type(ask_size) is not int or min(bid_size, ask_size) < 0):
            raise ValueError("invalid or duplicate option quote")
        seen.add((identity, ts))
        if available <= end and available - ts <= QUOTE_AGE_NS:
            series[identity][ts] = _Quote(available, bid, ask, bid_size, ask_size)
    return series


def _select(contracts, decision, start, width):
    visible = [contract for contract in contracts.values()
               if contract.right == decision.right and contract.available <= decision.available]
    if not visible:
        return None
    expiry = min((contract.expiry for contract in visible),
                 key=lambda when: (abs(when - (start + 7 * DAY_NS)), when))
    strikes = {contract.strike: contract for contract in visible if contract.expiry == expiry}
    strike = min(strikes, key=lambda value: (abs(value - decision.spot), value))
    short_strike = strike + (width if decision.right == "call" else -width)
    if short_strike in strikes:
        return strikes[strike], strikes[short_strike]
    return None


def _pair_series(quotes, long_leg, short_leg):
    long_series = quotes.get(long_leg.id, {})
    short_series = quotes.get(short_leg.id, {})
    timestamps = sorted(long_series.keys() & short_series.keys())
    return (timestamps, long_series, short_series)


def _pair_price(long_quote, short_quote, width, slippage, *, closing):
    if closing:
        bid = long_quote.bid * (1 - slippage)
        ask = short_quote.ask * (1 + slippage)
        net = bid - ask
        if not (long_quote.bid > 0 and min(long_quote.bid_size, short_quote.ask_size) >= 1
                and 0 <= net <= width):
            return None
        prices = bid, ask
    else:
        ask = long_quote.ask * (1 + slippage)
        bid = short_quote.bid * (1 - slippage)
        net = ask - bid
        if not (short_quote.bid > 0 and min(long_quote.ask_size, short_quote.bid_size) >= 1
                and 0 < net < width):
            return None
        prices = ask, bid
    return net, prices


def _first_pair(series, start, end, width, slippage, *, closing):
    """First observable executable synchronized pair, including quote latency."""
    times, long_series, short_series = series
    best = None
    for index in range(bisect_left(times, start), bisect_right(times, end)):
        ts = times[index]
        if best is not None and ts > best[0]:
            break
        long_quote, short_quote = long_series[ts], short_series[ts]
        available = max(long_quote.available, short_quote.available)
        if available > end:
            continue
        priced = _pair_price(long_quote, short_quote, width, slippage, closing=closing)
        if priced is None:
            continue
        net, prices = priced
        if best is None or (available, ts) < best[:2]:
            best = (available, ts, net, prices)
    return best


def expiry_payoff(right, long_strike, short_strike, settlement, debit_points, fee_dollars=0.0):
    """USD for one vertical held to official expiration settlement."""
    long_strike = _finite(long_strike, "long strike")
    short_strike = _finite(short_strike, "short strike")
    settlement = _finite(settlement, "settlement")
    debit_points = _finite(debit_points, "debit")
    fee_dollars = _finite(fee_dollars, "fees")
    width = abs(short_strike - long_strike)
    if (right not in ("call", "put") or min(long_strike, short_strike) <= 0 or settlement < 0
            or not 0 < debit_points < width or fee_dollars < 0
            or (short_strike > long_strike) != (right == "call")):
        raise ValueError("invalid debit vertical payoff")
    direction = 1 if right == "call" else -1
    value = max(0.0, direction * (settlement - long_strike)) - max(0.0, direction * (settlement - short_strike))
    return (value - debit_points) * MULTIPLIER - fee_dollars


def _summary(trades, unresolved):
    unresolved_risk = sum(row["risk_deployed"] for row in unresolved)
    risk = sum(row["risk_deployed"] for row in trades) + unresolved_risk
    realized = sum(row["net_pnl"] for row in trades)
    conservative = realized - unresolved_risk
    return {
        "entries": len(trades) + len(unresolved), "closed_trades": len(trades),
        "unresolved": len(unresolved), "realized_pnl": realized, "net_pnl": conservative,
        "risk_deployed": risk, "return_on_debit": conservative / risk if risk else None,
    }


def replay_spx_spreads(payload):
    """Replay fixed vertical widths across common observations and regimes."""
    required = {"sessions", "news_flags", "costs", "cost_scenario", "max_debit_dollars"}
    if type(payload) is not dict or set(payload) not in (required, required | {"multiplier"}):
        raise ValueError("invalid spread replay payload")
    if type(payload.get("multiplier", MULTIPLIER)) is not int or payload.get("multiplier", MULTIPLIER) != MULTIPLIER:
        raise ValueError("SPXW multiplier must be 100")
    scenario, costs = payload["cost_scenario"], payload["costs"]
    if type(costs) is not dict or scenario not in ("base", "stress") or scenario not in costs:
        raise ValueError("invalid cost scenario")
    costs = costs[scenario]
    _fields(costs, ("fee_per_contract", "spread_bps"), "costs")
    fee = _finite(costs["fee_per_contract"], "fee")
    slippage = _finite(costs["spread_bps"], "adverse execution bps") / 10_000
    max_debit = _finite(payload["max_debit_dollars"], "maximum debit")
    if not 0 <= fee <= 100 or not 0 <= slippage < 1 or max_debit <= 0:
        raise ValueError("invalid cost or risk parameters")
    news = _News(payload["news_flags"])
    session_ids = set()
    session_results = []
    regime_observations = {regime: 0 for regime in REGIMES}
    regime_opportunities = dict(regime_observations)
    candidates = [
        {"id": f"{'gated_' if gated else ''}reentry_{width}pt", "width_points": width,
         "gate": gated, "trades": [], "unresolved": [], "skipped": []}
        for gated in (False, True) for width in WIDTHS
    ]
    previous_end = None
    for session in payload["sessions"]:
        _fields(session, ("id", "open_ns", "end_ns", "opening_daily_iv", "bars", "contracts",
                          "option_quotes"), "session")
        sid, start, end = _id(session["id"]), _ns(session["open_ns"]), _ns(session["end_ns"])
        if sid in session_ids or start >= end or previous_end is not None and start < previous_end:
            raise ValueError("sessions must be chronological, distinct and nonoverlapping")
        session_ids.add(sid)
        previous_end = end
        daily = session["opening_daily_iv"]
        if daily is None:
            session_results.append({"id": sid, "open_ns": start, "end_ns": end, "anchor": None,
                                    "daily_expected_move": None, "status": "missing_same_day_iv",
                                    "regime_observations": {regime: 0 for regime in REGIMES},
                                    "regime_opportunities": {regime: 0 for regime in REGIMES}})
            continue
        _fields(daily, ("timestamp_ns", "available_ns", "forward", "iv", "expiry_ns"), "daily IV")
        ts, available, expiry = _ns(daily["timestamp_ns"]), _ns(daily["available_ns"]), _ns(daily["expiry_ns"])
        anchor, iv = _finite(daily["forward"], "anchor"), _finite(daily["iv"], "IV")
        if (not start <= ts <= available < expiry <= end or available - start > FIVE_MIN_NS
                or anchor <= 0 or not 0 < iv <= 5):
            raise ValueError("invalid as-of opening daily IV")
        move = anchor * iv * math.sqrt((expiry - ts) / YEAR_NS)
        if move <= 0 or not math.isfinite(move):
            raise ValueError("invalid implied daily expected move")
        decisions = list(_decisions(session["bars"], start, end, available, anchor, move, news))
        counts = {regime: 0 for regime in REGIMES}
        opportunities = dict(counts)
        for decision in decisions:
            counts[decision.regime] += 1
            regime_observations[decision.regime] += 1
            if decision.right is not None:
                opportunities[decision.regime] += 1
                regime_opportunities[decision.regime] += 1
        session_results.append({"id": sid, "open_ns": start, "end_ns": end, "anchor": anchor,
                                "daily_expected_move": move, "status": "replayed",
                                "regime_observations": counts, "regime_opportunities": opportunities})
        contracts = _contracts(session["contracts"], start, end)
        quotes = _quotes(session["option_quotes"], contracts, start, end)
        pairs = {}
        for candidate in candidates:
            _trade_session(candidate, sid, start, end, decisions, contracts, quotes, pairs,
                           news, fee, slippage, max_debit)
    if not session_results:
        raise ValueError("at least one spread replay session is required")
    for candidate in candidates:
        trades, unresolved = candidate["trades"], candidate["unresolved"]
        candidate["aggregate"] = _summary(trades, unresolved)
        candidate["regimes"] = {
            regime: {
                "observations": regime_observations[regime],
                "opportunities": regime_opportunities[regime],
                **_summary([row for row in trades if row["observable_regime"] == regime],
                           [row for row in unresolved if row["observable_regime"] == regime]),
            }
            for regime in REGIMES
        }
    return {
        "schema_version": 1, "experiment": "spxw_debit_verticals", "cost_scenario": scenario,
        "multiplier": MULTIPLIER, "costs": costs, "max_debit_dollars": max_debit,
        "sessions": session_results, "candidates": candidates, "regime_observations": regime_observations,
        "regime_opportunities": regime_opportunities,
        "assumptions": [
            "Five-minute closes identify same-side reentry inside 1.5 opening implied expected moves.",
            "Gated candidates skip active news and excursion peaks of 2.25 opening implied expected moves or more.",
            "The same visible ATM long strike anchors all widths. Weekly expiry nearest seven calendar days is chosen before the fill.",
            "Synchronized bid/ask snapshots bound paired fills with one contract of displayed size per leg.",
            "An entry outside the debit cap is canceled at the first observable executable pair. Leg fills are atomic in the model.",
            "Exit requires later synchronized quotes at least five minutes after the entry. An unresolved exit receives a zero-recovery scenario mark.",
            "Each session starts flat. Unresolved exposure stops entries for that session.",
        ],
    }


def _trade_session(candidate, sid, start, end, decisions, contracts, quotes, pairs,
                   news, fee, slippage, max_debit):
    busy_until = start
    entries = 0
    width = candidate["width_points"]
    for decision in decisions:
        if entries == MAX_ENTRIES:
            break
        if not decision.right or decision.available < busy_until:
            continue
        facts = {
            "variant": candidate["id"], "session": sid, "observable_regime": decision.regime,
            "signal_asof_ns": decision.reference, "decision_available_ns": decision.available,
            "signal_spot": decision.spot, "distance_sigma": decision.distance_sigma,
            "excursion_peak_sigma": decision.peak_sigma,
        }

        def skip(reason):
            candidate["skipped"].append({**facts, "reason": reason})

        if candidate["gate"] and decision.regime in ("news", "shock"):
            skip(f"gated_{decision.regime}")
            continue
        legs = _select(contracts, decision, start, width)
        if legs is None:
            skip("visible_leg_unavailable")
            continue
        long_leg, short_leg = legs
        pair_key = long_leg.id, short_leg.id
        if pair_key not in pairs:
            pairs[pair_key] = _pair_series(quotes, *legs)
        pair_series = pairs[pair_key]
        entry = _first_pair(pair_series, decision.available + 1,
                            min(end - FIVE_MIN_NS, decision.available + FIVE_MIN_NS),
                            width, slippage, closing=False)
        if entry is None:
            skip("executable_entry_unavailable")
            continue
        entry_ns, entry_quote, debit, (long_ask, short_bid) = entry
        if candidate["gate"] and news.active_at(entry_ns):
            skip("news_before_fill")
            continue
        risk = debit * MULTIPLIER + 4 * fee
        if risk > max_debit:
            skip("debit_cap")
            continue
        entries += 1
        deadline = entry_ns + FIVE_MIN_NS
        common = {**facts, "right": decision.right, "width_points": width, "expiry_ns": long_leg.expiry,
                  "long_contract_id": long_leg.id, "short_contract_id": short_leg.id,
                  "long_strike": long_leg.strike, "short_strike": short_leg.strike,
                  "entry_ns": entry_ns, "entry_quote_ns": entry_quote, "entry_debit_points": debit,
                  "entry_debit": debit * MULTIPLIER + 2 * fee,
                  "entry_long_ask": long_ask, "entry_short_bid": short_bid,
                  "risk_deployed": risk, "round_trip_fees": 4 * fee, "quantity": 1}
        exit_pair = _first_pair(pair_series, deadline, min(end, deadline + FIVE_MIN_NS),
                                width, slippage, closing=True)
        if exit_pair is None:
            candidate["unresolved"].append({**common, "deadline_ns": deadline,
                                            "reason": "executable_exit_unavailable",
                                            "conservative_recovery": 0.0})
            break
        exit_ns, exit_quote, credit, (long_bid, short_ask) = exit_pair
        busy_until = exit_ns
        gross = (credit - debit) * MULTIPLIER
        candidate["trades"].append({
            **common, "exit_ns": exit_ns, "exit_quote_ns": exit_quote, "hold_ns": exit_ns - entry_ns,
            "exit_credit_points": credit, "exit_long_bid": long_bid, "exit_short_ask": short_ask,
            "gross_pnl": gross, "fees": 4 * fee, "net_pnl": gross - 4 * fee,
        })
