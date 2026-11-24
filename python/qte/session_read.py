"""Summarize Schwab session evidence using frozen, prior-close regime rules."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from statistics import fmean
from time import time_ns

from qte import regime_lab, truth_db, session_options
from qte.adapters import schwab_session
from qte.adapters.exchange_sessions import import_xnys
from qte.brokers.schwab_auth import AuthError
from qte.brokers.readonly import BrokerConnectionError

RULES = Path("build/research/regime-lab-20261124/report.json")
MINUTE = schwab_session.MINUTE


def session_clock(asof):
    date = datetime.fromtimestamp(asof//10**9, truth_db.NY).date()
    schedule = import_xnys((date-timedelta(days=24)).isoformat(), date.isoformat())
    day = schedule.day(date.isoformat())
    if day.open_ns is None or not day.open_ns <= asof <= day.close_ns:
        raise ValueError("The primary chain must be captured during a cash session")
    previous = [s for s in schedule.days if s.close_ns and s.close_ns < day.open_ns][-1]
    return day, previous, schedule


def summarize(directory, db, rules, positions=()):
    primary = schwab_session.read(directory, "spx-chain-0dte", "optionChain", "SPX")
    at = primary["response_received_utc_ns"]
    day, previous, schedule = session_clock(at)
    label = day.trading_date
    prior = regime_lab.preopen_row(db, previous.trading_date)
    rid = regime_lab.classify(prior, rules)
    if rid is None:
        raise ValueError("The frozen classifier does not cover the observed features")
    rule = next(r["rules"] for r in rules["regimes"] if r["id"] == rid)
    missing, histories = [], {}
    for symbol in ("SPX", "VIX", "VIX3M"):
        try:
            rec = schwab_session.read(directory, symbol.lower()+"-5m", "priceHistory", symbol)
        except FileNotFoundError:
            missing.append(symbol+" five-minute capture")
            continue
        histories[symbol] = schwab_session.bars(rec, at)
    slots = [t for s in schedule.days if s.open_ns is not None for t in range(s.open_ns, min(s.close_ns, at), 5*MINUTE)
             if t+5*MINUTE <= at]
    spx = histories.get("SPX", {})
    today = [spx[t] for t in slots if t >= day.open_ns and t in spx]
    expected = sum(t >= day.open_ns for t in slots)

    def sma(n):
        return fmean(spx[t]["close"] for t in slots[-n:]) if len(slots) >= n and all(t in spx for t in slots[-n:]) else None
    spot = primary["response"]["underlyingPrice"]
    if primary["response"]["isDelayed"] or not session_options.positive(spot):
        raise ValueError("Primary snapshot must have an undelayed underlying")
    current = {k: histories.get(k, {}).get(slots[-1], {}).get("close")
               if slots else None for k in ("VIX", "VIX3M")}
    ratio = current["VIX"]/current["VIX3M"] if None not in current.values() else None
    opts = schwab_session.chain_contracts(primary, label)
    today_opts = [r for r in opts.values() if r["expiry"] == label]
    position_chains = {label: (today_opts, spot, at)}
    gamma = dict(today=session_options.dealer_gamma(today_opts, spot, at))
    extras = sorted(directory.glob("spx-chain-*.json"))
    extras = [p for p in extras if p.stem != "spx-chain-0dte"]
    if len(extras) > 1:
        raise ValueError("Expected at most one next-ten-day chain")
    if extras:
        later = schwab_session.read(directory, extras[0].stem, "optionChain", "SPX")
        later_at = later["response_received_utc_ns"]
        if later["response"]["isDelayed"]:
            missing.append("undelayed next-ten-day chain")
        else:
            future = [r for r in schwab_session.chain_contracts(later, label).values() if r["expiry"] > label]
            later_spot = later["response"]["underlyingPrice"]
            if not session_options.positive(later_spot):
                raise ValueError("Invalid underlying level in the next-ten-day chain")
            position_chains.update({r["expiry"]: (future, later_spot, later_at) for r in future})
            gamma["next_ten_days"] = dict(session_options.dealer_gamma(future, later_spot, later_at),
                                          asof_ns=later_at)
    else:
        missing.append("next-ten-day chain")
    em, anchor = prior["em"], prior["anchor"]
    high = max((b["high"] for b in today), default=None)
    low = min((b["low"] for b in today), default=None)
    scenarios = []
    for position in positions:
        expiry = session_options.position_expiry(position, label)
        if expiry not in position_chains:
            raise ValueError(f"No captured option chain for expiry {expiry}")
        scenarios.append(session_options.position(position, *position_chains[expiry], label))
    return dict(schema="qte-session-read-v1", status="observed", session=label, asof_ns=at,
                regime=rid, rules=rule, prior=prior, spot=spot, move_sigma=(spot-anchor)/em,
                high_sigma=(high-anchor)/em if high is not None else None,
                low_sigma=(low-anchor)/em if low is not None else None,
                session_bars=len(today), expected_session_bars=expected,
                sma20=sma(20), sma200=sma(200), volatility=dict(current, term_structure=ratio),
                dealer_gamma=gamma, gate=session_options.gate(today_opts, spot, at, day.close_ns, prior["vix"]),
                positions=scenarios, missing=missing,
                chain_sha256=primary["response_canonical_json_sha256"])


def open_truth():
    return sqlite3.connect(truth_db.DEFAULT_DB.resolve().as_uri()+"?mode=ro", uri=True)


def read_session(directory, positions=()):
    raw = RULES.read_bytes()
    rules = json.loads(raw)
    if rules["schema"] != "qte-regime-lab-v1":
        raise ValueError("Expected the frozen regime-lab report")
    with closing(open_truth()) as db:
        return dict(summarize(directory, db, rules, positions), regime_rules_sha256=sha256(raw).hexdigest())


def show(report):
    print(f"{'Reading':<23}Value")
    vols, gate, gamma = report["volatility"], report["gate"], report["dealer_gamma"]["today"]
    for label, value in (
        ("Session", report["session"]),
        ("Pre-open regime", report["regime"]),
        ("SPX move σ", report["move_sigma"]),
        ("Observed low/high σ", (report["low_sigma"], report["high_sigma"])),
        ("5m SMA20 / SMA200", (report["sma20"], report["sma200"])),
        ("VIX / VIX3M levels", (vols["VIX"], vols["VIX3M"])),
        ("VIX term ratio", vols["term_structure"]),
        ("Dealer gamma", gamma["net"]),
        ("Gamma flips", gamma["flip_levels"]),
        ("Short-strike IV gate", gate["score"]),
    ):
        print(f"{label:<23}", f"{value:,.4f}" if isinstance(value, float) else value)
    print(gamma["convention"])
    for label, key in (("ATM put", "atm_P"), ("ATM call", "atm_C"), ("Short put", "short_P"), ("Short call", "short_C")):
        leg = gate.get("legs", {}).get(key)
        if leg and leg["model_multiple"] is not None:
            print(f"{label:<23}{leg['strike']:g} at {leg['model_multiple']:.4f}× prior VIX")
    for p in report["positions"]:
        print(p["contract"], "mark", p["mark"], "P&L USD", p["pnl_usd"], "expiry break-even", p["expiry_breakeven"])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qte session-read")
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--position", action="append", default=[])
    parser.add_argument("--output", type=Path, help="New JSON output file")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = args.replay or truth_db.TRUTH_ROOT/"captures"/"schwab"/("session-read-"+now)
    path = args.output or Path("build/session-read")/(now+".json")
    try:
        if args.replay is None:
            schwab_session.capture(directory)
        report = read_session(directory, args.position)
    except (AuthError, BrokerConnectionError, FileNotFoundError, sqlite3.Error, ValueError) as exc:
        detail = str(exc) if isinstance(exc, (AuthError, ValueError)) else type(exc).__name__
        report = dict(status="blocked", reason=detail, asof_ns=time_ns())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if report["status"] == "observed":
        show(report)
    else:
        print("blocked:", report["reason"])
    print(path)
    return 0 if report["status"] == "observed" else 2
