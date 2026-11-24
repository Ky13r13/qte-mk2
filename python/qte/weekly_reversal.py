"""Causal five-minute weekly-option reversal replay over supplied observations."""
from __future__ import annotations

import math
import statistics

YEAR_NS = 365 * 24 * 60 * 60 * 1_000_000_000
FIVE_MIN_NS = 5 * 60 * 1_000_000_000
MAX_ENTRIES = 5
MAX_QUOTE_WAIT_NS = FIVE_MIN_NS

CANDIDATE_DEFINITIONS = (
    {"id": "naive_fade_1sigma", "mode": "fade", "threshold": 1.0, "confirmation": "none"},
    {"id": "confirmed_fade_1sigma", "mode": "fade", "threshold": 1.0, "confirmation": "completed_reentry"},
    {"id": "confirmed_fade_1_5sigma", "mode": "fade", "threshold": 1.5, "confirmation": "completed_reentry"},
    {"id": "confirmed_fade_2sigma", "mode": "fade", "threshold": 2.0, "confirmation": "completed_reentry"},
    {"id": "reject_beyond_2sigma", "mode": "fade", "threshold": 1.0, "confirmation": "completed_reentry", "reject_above": 2.0},
    {"id": "regime_gated_confirmed_1sigma", "mode": "fade", "threshold": 1.0, "confirmation": "completed_reentry", "regime_gate": True},
    {"id": "regime_gated_confirmed_1_5sigma", "mode": "fade", "threshold": 1.5, "confirmation": "completed_reentry", "regime_gate": True, "shock_threshold": 2.25},
    {"id": "tight_spread_regime_reentry", "mode": "fade", "threshold": 1.0, "confirmation": "completed_reentry", "regime_gate": True, "max_spread_fraction": .04},
    {"id": "trend_follow_1sigma", "mode": "trend", "threshold": 1.0, "confirmation": "completed_continuation"},
    {"id": "cash", "mode": "cash"},
)


def _invalid(message: str) -> ValueError: return ValueError(message)
def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value): raise _invalid(f"{name} must be finite")
    return float(value)
def _timestamp(value, name):
    if type(value) is not int or not -(2**63) <= value < 2**63: raise _invalid(f"{name} must be signed 64-bit")
    return value


def _quote(rows, contract, after, *, end_ns, needs_bid=False):
    """First observable new quote, within the fill window."""
    for row in rows:
        if (row["contract_id"] == contract and row["timestamp_ns"] > after
                and (not needs_bid or row["bid"] > 0)
                and row["available_ns"] <= min(end_ns, after + MAX_QUOTE_WAIT_NS)):
            return row
    return None


def _known_news(flags, at):
    latest = max((row for row in flags if row["available_ns"] <= at),
                 key=lambda row: (row["timestamp_ns"], row["available_ns"]), default=None)
    return latest is not None and latest["active"]


def _latest_known(rows, contract, at):
    values = [row for row in rows if row["contract_id"] == contract and row["available_ns"] <= at]
    return max(values, key=lambda row: row["timestamp_ns"], default=None)


def _aggregate(trades, unresolved):
    net = sum(row["net_pnl"] for row in trades) - sum(row["risk_deployed"] for row in unresolved)
    risk = sum(row["risk_deployed"] for row in trades) + sum(row["risk_deployed"] for row in unresolved)
    return {"closed_trades": len(trades), "unresolved": len(unresolved), "net_pnl": net,
            "risk_deployed": risk, "return_on_debit": None if not risk else net / risk}


def _validate(payload):
    if not isinstance(payload, dict) or set(payload) != {"sessions", "news_flags", "costs", "cost_scenario", "max_debit_dollars", "multiplier"}:
        raise _invalid("unsupported replay payload")
    if payload["cost_scenario"] not in {"base", "stress"} or not isinstance(payload["costs"], dict): raise _invalid("cost scenario is invalid")
    costs = payload["costs"].get(payload["cost_scenario"])
    if not isinstance(costs, dict) or set(costs) != {"fee_per_contract", "spread_bps"}: raise _invalid("costs are invalid")
    fee, spread = _number(costs["fee_per_contract"], "fee"), _number(costs["spread_bps"], "spread")
    cap, multiplier = _number(payload["max_debit_dollars"], "max debit"), payload["multiplier"]
    if fee < 0 or not 0 <= spread < 10_000 or cap <= 0 or type(multiplier) is not int or multiplier <= 0: raise _invalid("risk terms are invalid")
    return costs, cap, multiplier


def replay_weekly_reversal(payload: dict, *, candidate_id: str | None = None) -> dict:
    """Replay fixed candidates from point-in-time supplied bars and option quotes."""
    costs, cap, multiplier = _validate(payload)
    flags = payload["news_flags"]
    if not isinstance(flags, list): raise _invalid("news flags are invalid")
    for row in flags:
        if not isinstance(row, dict) or set(row) != {"timestamp_ns", "available_ns", "active"} or type(row["active"]) is not bool:
            raise _invalid("news flag is invalid")
        timestamp, available = _timestamp(row["timestamp_ns"], "news timestamp"), _timestamp(row["available_ns"], "news availability")
        if available < timestamp: raise _invalid("news availability precedes timestamp")
    if len({(row["timestamp_ns"], row["available_ns"]) for row in flags}) != len(flags):
        raise _invalid("duplicate news state timestamp")
    definitions = tuple(item for item in CANDIDATE_DEFINITIONS
                        if candidate_id is None or item["id"] == candidate_id)
    if not definitions:
        raise ValueError("unknown replay candidate")
    output = {item["id"]: {"trades": [], "unresolved": [], "skipped": []} for item in definitions}
    sessions_out = []
    seen = set()
    previous_end = None
    if not isinstance(payload["sessions"], list) or not 1 <= len(payload["sessions"]) <= 1000:
        raise _invalid("expected 1–1000 sessions")
    for session in payload["sessions"]:
        required = {"id", "open_ns", "end_ns", "opening_daily_iv", "weekly", "bars", "option_quotes"}
        if not isinstance(session, dict) or set(session) != required or not isinstance(session["id"], str): raise _invalid("session is invalid")
        open_ns, end_ns = _timestamp(session["open_ns"], "open"), _timestamp(session["end_ns"], "end")
        if (open_ns >= end_ns or previous_end is not None and open_ns < previous_end
                or not session["id"] or len(session["id"]) > 100 or session["id"] in seen):
            raise _invalid("session bounds or identities are invalid")
        previous_end = end_ns
        seen.add(session["id"])
        session_flags = [row for row in flags if row["available_ns"] <= end_ns]
        daily, weekly = session["opening_daily_iv"], session["weekly"]
        if not isinstance(daily, dict) or set(daily) != {"timestamp_ns", "available_ns", "forward", "iv", "expiry_ns"}: raise _invalid("daily proxy is invalid")
        if not isinstance(weekly, dict) or set(weekly) != {"call_contract_id", "put_contract_id", "expiry_ns"}: raise _invalid("weekly contract is invalid")
        for key in ("timestamp_ns", "available_ns", "expiry_ns"): _timestamp(daily[key], key)
        forward, iv = _number(daily["forward"], "forward"), _number(daily["iv"], "iv")
        if (forward <= 0 or not 0 < iv <= 5
                or not open_ns <= daily["timestamp_ns"] <= daily["available_ns"] < daily["expiry_ns"] <= end_ns
                or daily["available_ns"] > open_ns + FIVE_MIN_NS):
            raise _invalid("daily proxy must be observable near the open and expire within the session")
        expected_move = forward * iv * math.sqrt((daily["expiry_ns"] - daily["timestamp_ns"]) / YEAR_NS)
        if not math.isfinite(expected_move) or expected_move <= 0:
            raise _invalid("daily proxy is not finite and positive")
        if not isinstance(weekly["call_contract_id"], str) or not isinstance(weekly["put_contract_id"], str) or weekly["call_contract_id"] == weekly["put_contract_id"]:
            raise _invalid("weekly identities are invalid")
        expiry = _timestamp(weekly["expiry_ns"], "weekly expiry")
        if expiry <= end_ns or not 5 * 86_400_000_000_000 <= expiry - open_ns <= 9 * 86_400_000_000_000:
            raise _invalid("weekly contract must expire in 5-9 calendar days")
        bars, quotes = session["bars"], session["option_quotes"]
        if not isinstance(bars, list) or not isinstance(quotes, list): raise _invalid("observations are invalid")
        parsed_bars = []
        for row in bars:
            if not isinstance(row, dict) or set(row) != {"timestamp_ns", "available_ns", "close"}: raise _invalid("bar is invalid")
            timestamp, available, close = _timestamp(row["timestamp_ns"], "bar"), _timestamp(row["available_ns"], "bar availability"), _number(row["close"], "close")
            if close <= 0 or available < timestamp or not open_ns <= timestamp <= end_ns: raise _invalid("bar bounds are invalid")
            parsed_bars.append({"timestamp_ns": timestamp, "available_ns": available, "close": close})
        if any(a["timestamp_ns"] >= b["timestamp_ns"] for a, b in zip(parsed_bars, parsed_bars[1:])): raise _invalid("bars must be chronological")
        parsed_quotes = []
        for row in quotes:
            if not isinstance(row, dict) or set(row) != {"contract_id", "timestamp_ns", "available_ns", "bid", "ask"}: raise _invalid("quote is invalid")
            if row["contract_id"] not in {weekly["call_contract_id"], weekly["put_contract_id"]}: raise _invalid("quote contract mismatch")
            item = {"contract_id": row["contract_id"], "timestamp_ns": _timestamp(row["timestamp_ns"], "quote"),
                    "available_ns": _timestamp(row["available_ns"], "quote availability"), "bid": _number(row["bid"], "bid"), "ask": _number(row["ask"], "ask")}
            if (not open_ns <= item["timestamp_ns"] <= end_ns or item["available_ns"] < item["timestamp_ns"]
                    or item["bid"] < 0 or item["ask"] <= 0 or item["ask"] < item["bid"]):
                raise _invalid("quote bounds are invalid")
            parsed_quotes.append(item)
        if len({(row["contract_id"], row["timestamp_ns"]) for row in parsed_quotes}) != len(parsed_quotes):
            raise _invalid("duplicate quote timestamp")
        parsed_quotes = sorted(
            (row for row in parsed_quotes if row["available_ns"] - row["timestamp_ns"] <= 30_000_000_000),
            key=lambda row: (row["available_ns"], row["timestamp_ns"], row["contract_id"]),
        )
        decision_bars = [row for row in parsed_bars if (row["timestamp_ns"] - open_ns) % FIVE_MIN_NS == 0]
        sessions_out.append({"id": session["id"], "daily_expected_move_proxy": expected_move,
                             "daily_proxy_available_ns": daily["available_ns"], "weekly_expiry_ns": expiry,
                             "raw_completed_bar_count": len(parsed_bars), "five_minute_decision_bar_count": len(decision_bars)})
        for candidate in definitions:
            name = candidate["id"]
            if name == "cash": continue
            entries = 0
            busy_until = open_ns
            peak_distance = 0.0
            excursion_side = 0
            for previous, current in zip(decision_bars, decision_bars[1:]):
                if entries >= MAX_ENTRIES: break
                decision_ns = current["available_ns"]
                if (decision_ns < daily["available_ns"] or decision_ns + FIVE_MIN_NS >= end_ns
                        or previous["available_ns"] > decision_ns
                        or previous["available_ns"] > previous["timestamp_ns"] + FIVE_MIN_NS
                        or current["timestamp_ns"] - previous["timestamp_ns"] != FIVE_MIN_NS
                        or decision_ns - current["timestamp_ns"] > FIVE_MIN_NS):
                    peak_distance, excursion_side = 0.0, 0
                    continue
                before, now = previous["close"] - forward, current["close"] - forward
                distance, prior_distance = abs(now) / expected_move, abs(before) / expected_move
                sign = 1 if now > 0 else -1 if now < 0 else 0
                same_side = before * now > 0
                threshold = candidate["threshold"]
                reentry = prior_distance >= threshold and distance < threshold and same_side
                continuation = distance >= candidate["threshold"] and abs(now) > abs(before) and before * now > 0
                if distance >= threshold:
                    inherited_peak = peak_distance if excursion_side == sign else 0.0
                    peak_distance = max(distance, prior_distance if same_side else 0.0, inherited_peak)
                    excursion_side = sign
                observed_peak = max(peak_distance if excursion_side == sign else 0.0,
                                    prior_distance if same_side else 0.0, distance)
                if distance < threshold:
                    peak_distance, excursion_side = 0.0, 0
                if not sign or decision_ns < busy_until:
                    continue
                if candidate["mode"] == "trend" and not continuation:
                    continue
                if candidate["mode"] == "fade":
                    if candidate["confirmation"] == "completed_reentry" and not reentry:
                        continue
                    if candidate["confirmation"] == "none" and (distance < threshold or same_side and prior_distance >= threshold):
                        continue
                if candidate.get("reject_above") is not None and observed_peak > candidate["reject_above"]:
                    output[name]["skipped"].append({"session": session["id"], "reason": "beyond_declared_2sigma_rejection"}); continue
                # The gated candidate consumes only availability-dated news and
                # observable continuation/volatility shock, never fixture labels.
                news = _known_news(session_flags, decision_ns)
                shock = observed_peak >= candidate.get("shock_threshold", 1.5)
                if candidate.get("regime_gate") and (news or shock):
                    reason = "active_news" if news else "excursion_shock"
                    output[name]["skipped"].append({"session": session["id"], "reason": reason,
                                                    "decision_available_ns": decision_ns})
                    continue
                # Fade buys toward the open; trend buys in the observed direction.
                direction = -sign if candidate["mode"] == "fade" else sign
                contract = weekly["call_contract_id"] if direction > 0 else weekly["put_contract_id"]
                if candidate.get("max_spread_fraction") is not None:
                    visible = _latest_known(parsed_quotes, contract, decision_ns)
                    if (visible is None or decision_ns - visible["timestamp_ns"] > FIVE_MIN_NS
                            or (visible["ask"] - visible["bid"]) / visible["ask"] > candidate["max_spread_fraction"]):
                        output[name]["skipped"].append({"session": session["id"], "reason": "predecision_spread_too_wide"}); continue
                entry = _quote(parsed_quotes, contract, decision_ns, end_ns=end_ns - FIVE_MIN_NS)
                if entry is None:
                    output[name]["skipped"].append({"session": session["id"], "reason": "subsequent_ask_unavailable"}); continue
                if candidate.get("regime_gate") and _known_news(session_flags, entry["available_ns"]):
                    output[name]["skipped"].append({"session": session["id"], "reason": "news_before_fill",
                                                    "decision_available_ns": decision_ns})
                    continue
                ask = entry["ask"] * (1 + costs["spread_bps"] / 10_000)
                entry_debit = ask * multiplier + costs["fee_per_contract"]
                risk_deployed = entry_debit + costs["fee_per_contract"]
                if not math.isfinite(risk_deployed) or risk_deployed > cap:
                    output[name]["skipped"].append({"session": session["id"], "reason": "debit_cap"}); continue
                entries += 1
                deadline = entry["available_ns"] + FIVE_MIN_NS
                exit_quote = _quote(parsed_quotes, contract, deadline - 1, end_ns=end_ns, needs_bid=True)
                common = {"variant": name, "session": session["id"], "contract_id": contract,
                          "signal_asof_ns": current["timestamp_ns"], "decision_available_ns": decision_ns,
                          "entry_ns": entry["available_ns"], "entry_quote_ns": entry["timestamp_ns"],
                          "entry_debit": entry_debit, "entry_ask": entry["ask"], "entry_bid": entry["bid"],
                          "risk_deployed": risk_deployed, "expected_move_proxy": expected_move,
                          "distance_sigma": distance, "excursion_peak_sigma": observed_peak,
                          "observable_regime": "news" if news else "shock" if shock else "quiet",
                          "quantity": 1}
                if exit_quote is None:
                    output[name]["unresolved"].append({**common, "deadline_ns": deadline,
                        "reason": "no_first_eligible_bid_at_or_after_deadline", "conservative_recovery": 0.0})
                    break  # no overlapping exposure
                bid = exit_quote["bid"] * (1 - costs["spread_bps"] / 10_000)
                exit_credit = bid * multiplier - costs["fee_per_contract"]
                busy_until = exit_quote["available_ns"]
                output[name]["trades"].append({**common, "exit_ns": exit_quote["available_ns"], "exit_credit": exit_credit,
                    "exit_quote_ns": exit_quote["timestamp_ns"], "exit_bid": exit_quote["bid"], "exit_ask": exit_quote["ask"],
                    "gross_pnl": bid * multiplier - ask * multiplier,
                    "fees": 2 * costs["fee_per_contract"], "net_pnl": exit_credit - entry_debit,
                    "fill_policy": "later_ask_then_first_eligible_bid_after_5m"})
    candidates = [{**item, "status": "cash_baseline" if item["id"] == "cash" else "recorded",
                   "aggregate": _aggregate(output[item["id"]]["trades"], output[item["id"]]["unresolved"]),
                   **output[item["id"]]} for item in definitions]
    raw_counts = [row["raw_completed_bar_count"] for row in sessions_out]
    decision_counts = [row["five_minute_decision_bar_count"] for row in sessions_out]
    return {"schema_version": 1, "evidence": "provided_observations_replay_not_profitability_evidence",
            "expected_move_label": "1–2× expected move (daily-expiry ATM IV sqrt remaining calendar time proxy)",
            "assumptions": ["Opening same-day ATM IV × opening underlying × √(remaining calendar time / 365 days). The frozen band is separate from the traded weekly option.",
                            "Five-minute decision grid, same-side reentry or continued excursion. Regime variants veto available active news and large excursions at their stated shock threshold.",
                            "A fill uses a later quote with at most 30 seconds publication delay and five minutes waiting. The exit quote must originate at or after the five-minute hold.",
                            "One contract, one position at a time, up to five entries per session. Unresolved exits use zero recovery and block later entries within that session."],
            "candidate_definitions": list(definitions), "sessions": sessions_out,
            "cadence_summary": {"decision_grid_ns": FIVE_MIN_NS,
                "raw_completed_bars": {"mean": statistics.mean(raw_counts) if raw_counts else 0, "median": statistics.median(raw_counts) if raw_counts else 0},
                "five_minute_decision_bars": {"mean": statistics.mean(decision_counts) if decision_counts else 0, "median": statistics.median(decision_counts) if decision_counts else 0}},
            "short_leg_support": {"status": "excluded", "reason": "American short-option exercise, assignment, and executable combo lifecycle are not modeled."},
            "cost_scenario": payload["cost_scenario"], "candidates": candidates}
