"""Deterministic synthetic option-quote replay for mechanics research only.

This deliberately models a fully-paid long straddle, not a claim about a
tradable options strategy.  Fixture regimes describe how the synthetic path was
made and are never supplied to non-oracle candidate decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .gamma_surface import _black76, calculate_gamma_surface

STEP_NS = 300_000_000_000
YEAR_NS = 365 * 24 * 60 * 60 * 1_000_000_000
HOLD_STEPS = 2
FEE_PER_CONTRACT = 1.25
MULTIPLIER = 20
MAX_RISK_DOLLARS = 250.0

CANDIDATE_DEFINITIONS = (
    {"id": "no_confirmation_fixed", "confirmation": "none", "sizing": "one_pair", "oracle": False},
    {"id": "confirmation_fixed", "confirmation": "observed_move_at_least_0_15", "sizing": "one_pair", "oracle": False},
    {"id": "confirmation_regime_sized", "confirmation": "observed_move_at_least_0_15", "sizing": "two_pairs_only_if_premarket_realized_move_at_least_0_40", "oracle": False},
    {"id": "confirmation_no_chase_fixed", "confirmation": "observed_move_0_15_to_0_40", "sizing": "one_pair", "oracle": False},
    {"id": "perfect_data_oracle_infeasible", "confirmation": "future_net_payout", "sizing": "one_pair", "oracle": True},
)


@dataclass(frozen=True)
class _Quote:
    timestamp_ns: int
    available_ns: int
    call_bid: float | None
    call_ask: float | None
    put_bid: float | None
    put_ask: float | None
    forward: float
    years: float
    iv: float
    premarket_move: float
    fixture_regime: str


def _prices(forward: float, strike: float, years: float, iv: float, spread: float) -> tuple[float, float, float, float]:
    call = _black76(forward, strike, iv, years, 0.0, "call")["price"]
    put = _black76(forward, strike, iv, years, 0.0, "put")["price"]
    # Conservative quotes stay nonnegative even in tail/expiry-adjacent cases.
    return (max(0.0, call - spread / 2), call + spread / 2,
            max(0.0, put - spread / 2), put + spread / 2)


def _fixture() -> list[_Quote]:
    """Four predeclared paths; availability is one completed quote later."""
    specs = (
        ("calm_chop", [100, 100.2, 99.9, 100.1, 100, 100.15], .16, .20, .10),
        ("wide_costs", [100, 100.7, 99.5, 100.5, 99.7, 100.2], .20, 1.20, .65),
        ("expansion", [100, 100.3, 101.0, 103.0, 104.0, 115.0], .30, .25, .55),
        # The unavailable quote is intentionally a failed entry, not a free fill.
        ("entry_quote_failure", [100, 100.1, 101.5, 101.7, 101.8, 102.0], .24, .25, .20),
    )
    result: list[_Quote] = []
    offset = 0
    for name, path, iv, spread, premarket_move in specs:
        for index, forward in enumerate(path):
            timestamp = offset + index * STEP_NS
            years = 12 / 365 - index * STEP_NS / YEAR_NS
            bid_call, ask_call, bid_put, ask_put = _prices(forward, 100.0, years, iv, spread)
            if name == "entry_quote_failure" and index == 3:
                bid_call = ask_call = bid_put = ask_put = None
            result.append(_Quote(timestamp, timestamp + STEP_NS, bid_call, ask_call, bid_put, ask_put,
                                 forward, years, iv, premarket_move, name))
        offset += len(path) * STEP_NS
    return result


def _fresh(quote: _Quote | None, at_ns: int) -> bool:
    return quote is not None and quote.available_ns <= at_ns <= quote.timestamp_ns + STEP_NS and all(
        isinstance(value, float) and math.isfinite(value) and value >= 0
        for value in (quote.call_bid, quote.call_ask, quote.put_bid, quote.put_ask)) and quote.call_bid <= quote.call_ask and quote.put_bid <= quote.put_ask


def _aggregate(trades: list[dict]) -> dict:
    fees = sum(item["fees"] for item in trades)
    gross = sum(item["gross_pnl"] for item in trades)
    risk = sum(item["risk_deployed"] for item in trades)
    net = gross - fees
    return {"trades": len(trades), "gross_pnl": gross, "fees": fees, "net_pnl": net,
            "risk_deployed": risk, "return_on_debit_including_fees": None if not risk else net / risk,
            "win_rate": None if not trades else sum(item["net_pnl"] > 0 for item in trades) / len(trades)}


def _candidate(name: str, *, confirmation: bool, regime_sizing: bool, oracle: bool, no_chase: bool = False,
               quotes: list[_Quote]) -> tuple[list[dict], list[dict]]:
    trades: list[dict] = []; skipped: list[dict] = []
    by_regime: dict[str, list[_Quote]] = {}
    for quote in quotes: by_regime.setdefault(quote.fixture_regime, []).append(quote)
    for regime, series in by_regime.items():
        # Decision i sees quote i-1 only. Filling uses a later quote i+1.
        for decision_index in (2,):
            fill_index, exit_index = decision_index + 1, decision_index + 1 + HOLD_STEPS
            if exit_index >= len(series): continue
            asof = series[decision_index - 1]
            entry, exit_quote = series[fill_index], series[exit_index]
            if not _fresh(asof, series[decision_index].timestamp_ns):
                skipped.append({"variant": name, "regime": regime, "reason": "stale_asof_snapshot"}); continue
            if not _fresh(entry, entry.available_ns):
                skipped.append({"variant": name, "regime": regime, "reason": "atomic_entry_quote_unavailable"}); continue
            # Entry is now committed. Missing later exit quotes are conservatively
            # valued at zero; they never retroactively cancel the entry.
            exit_valid = _fresh(exit_quote, exit_quote.available_ns)
            premarket_realized_move = asof.premarket_move
            move = abs(asof.forward - series[0].forward)
            confirmed = move >= .15 and (not no_chase or move <= .40)
            if oracle and not exit_valid:
                skipped.append({"variant": name, "regime": regime, "reason": "oracle_exit_quote_unavailable"}); continue
            future_gross_one = (exit_quote.call_bid + exit_quote.put_bid - entry.call_ask - entry.put_ask) if exit_valid else -math.inf
            if oracle:
                enter = future_gross_one * MULTIPLIER > 4 * FEE_PER_CONTRACT
            else:
                enter = not confirmation or confirmed
            if not enter:
                skipped.append({"variant": name, "regime": regime, "reason": "confirmation_not_met"}); continue
            requested_quantity = 2 if regime_sizing and premarket_realized_move >= .40 else 1
            one_pair_risk = (entry.call_ask + entry.put_ask) * MULTIPLIER + 4 * FEE_PER_CONTRACT
            quantity = min(requested_quantity, int(MAX_RISK_DOLLARS // one_pair_risk))
            if quantity <= 0:
                skipped.append({"variant": name, "regime": regime, "reason": "risk_cap_prevents_entry"}); continue
            debit = quantity * (entry.call_ask + entry.put_ask) * MULTIPLIER
            credit = (quantity * (exit_quote.call_bid + exit_quote.put_bid) * MULTIPLIER) if exit_valid else 0.0
            fees = quantity * 4 * FEE_PER_CONTRACT
            # Under Black-76, calls and puts with these shared inputs have equal gamma.
            entry_gamma = 2 * _black76(entry.forward, 100.0, entry.years, entry.iv, 0.0, "call")["gamma"]
            exit_gamma = (2 * _black76(exit_quote.forward, 100.0, exit_quote.years,
                                       exit_quote.iv, 0.0, "call")["gamma"]) if exit_valid else None
            scenario_gamma = 2 * _black76(entry.forward * 1.01, 100.0, entry.years, entry.iv, 0.0, "call")["gamma"]
            trades.append({"variant": name, "regime": regime, "entry_ns": entry.available_ns,
                           "exit_ns": exit_quote.available_ns, "quantity": quantity,
                           "entry_debit": debit, "exit_credit": credit, "gross_pnl": credit - debit,
                           "fees": fees, "net_pnl": credit - debit - fees,
                           "max_loss": debit + fees, "risk_deployed": debit + fees, "multiplier": MULTIPLIER,
                           "premarket_realized_move": premarket_realized_move, "confirmation_move": move,
                           "observed_regime": "high_activity" if premarket_realized_move >= .40 else "low_activity",
                           "signal_asof_ns": asof.timestamp_ns,
                           "decision_available_ns": asof.available_ns,
                           "liquidation_status": "closed" if exit_valid else "unresolved_zero_recovery",
                           "gamma_diagnostics": {"entry_pair_gamma": entry_gamma, "exit_pair_gamma": exit_gamma,
                                                 "entry_pair_gamma_forward_up_1pct": scenario_gamma,
                                                 "entry_surface_inputs": {"forward": entry.forward, "strike": 100.0,
                                                                          "volatility_pct": entry.iv * 100,
                                                                          "days": entry.years * 365, "rate_pct": 0,
                                                                          "multiplier": MULTIPLIER, "quantity": quantity,
                                                                          "option_type": "call", "span_pct": 10}},
                           "entry_fill_policy": "subsequent_fresh_ask_both_legs_atomic",
                           "exit_fill_policy": "fresh_bid_both_legs_atomic_or_zero_recovery"})
    return trades, skipped


def run_options_research() -> dict:
    """Return fixed synthetic mechanics results; no data access or execution occurs."""
    quotes = _fixture()
    definitions = ((item["id"], item["id"] != "no_confirmation_fixed" and not item["oracle"],
                    item["id"] == "confirmation_regime_sized", item["oracle"]) for item in CANDIDATE_DEFINITIONS)
    all_trades = []; skips = []
    candidates = []
    regimes = sorted({quote.fixture_regime for quote in quotes})
    for name, confirmation, sizing, oracle in definitions:
        trades, candidate_skips = _candidate(name, confirmation=confirmation, regime_sizing=sizing, oracle=oracle,
                            no_chase=name == "confirmation_no_chase_fixed", quotes=quotes)
        all_trades.extend(trades); skips.extend(candidate_skips)
        candidates.append({"id": name, "confirmation": confirmation, "regime_sizing": sizing,
                           "oracle": oracle, "status": "infeasible_reference" if oracle else "recorded_synthetic",
                           "aggregate": _aggregate(trades)})
    regime_rows = []
    for regime in regimes:
        regime_rows.append({"name": regime, "fixture_label": regime,
                            "variants": {candidate["id"]: _aggregate([trade for trade in all_trades
                                if trade["variant"] == candidate["id"] and trade["regime"] == regime])
                                         for candidate in candidates}})
    example = next(trade for trade in all_trades
                   if trade["variant"] == "confirmation_fixed" and trade["regime"] == "expansion")
    surface = calculate_gamma_surface({"schema_version": 1, **example["gamma_diagnostics"]["entry_surface_inputs"]})
    return {"schema_version": 1, "evidence": "synthetic_known_mechanics_only",
            "assumptions": ["Long one-strike same-expiry call and put; no futures hedge.",
                            "Entry pays both asks and exit receives both bids; fees are charged per leg.",
                            "Signals see only prior completed quotes; stale or missing snapshots skip entry and fills require a subsequent available quote.",
                            "Legs use an intentionally simplified atomic paired-fill rule; no partial option-leg fills are fabricated.",
                            "Sizing uses a separate synthetic premarket move, known before the opening move used for confirmation. Fixture names are display labels.",
                            "The oracle alone sees the future quote payout. The no-confirmation control uses the same decision clock as the confirmation candidates.",
                            "Loss is capped by the paid debit plus fees; no short options, margin, financing, or assignment is modeled."],
            "quote_policy": {"availability": "one_completed_quote_delay", "holding_steps": HOLD_STEPS,
                             "fee_per_contract": FEE_PER_CONTRACT, "multiplier": MULTIPLIER,
                             "max_risk_dollars_per_trade": MAX_RISK_DOLLARS,
                             "paired_fill_simplification": "both legs fill together only when both fresh quotes exist"},
            "candidate_definitions": list(CANDIDATE_DEFINITIONS), "candidates": candidates,
            "regimes": regime_rows, "trades": all_trades, "skipped": skips,
            "surface_example": {"fixture": example["regime"], "candidate": example["variant"],
                                "inputs": surface["inputs"], "model": surface["model"],
                                "point": surface["point"]}}
