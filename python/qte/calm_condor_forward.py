"""Observation, gate audit and quote-based condor settlement."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
from time import time_ns

from qte import calm_condor, forward_paper, session_read, truth_db
from qte.adapters import schwab_session
from qte.brokers.schwab_auth import AuthError
from qte.brokers.readonly import BrokerConnectionError

FORWARD = truth_db.TRUTH_ROOT / "forward"
OBSERVATIONS = FORWARD / "calm-condor.ndjson"
PAPER = FORWARD / "calm-condor" / forward_paper.LEDGER
SCHEMA = "qte-calm-condor-observation-v1"
PAPER_SCHEMA = "qte-calm-condor-paper-v1"
MINUTE = schwab_session.MINUTE


def opening_window(day, at):
    return day.open_ns+25*MINUTE <= at <= day.open_ns+35*MINUTE


def shadow_trade(report):
    reading = report["gate"]
    if reading["score"] is None:
        return None
    positions = []
    for name, quantity in (("short_P", -1), ("wing_P", 1), ("short_C", -1), ("wing_C", 1)):
        row = reading["legs"][name]
        if row is None or row["market"] is None:
            return None
        price = row["market"]["bid" if quantity < 0 else "ask"]
        positions.append(dict(row, quantity=quantity, fill=price, right=name[-1]))
    quoted = round(sum(-p["quantity"]*p["fill"] for p in positions), 9)
    wing = calm_condor.PRIMARY["wing"]
    if not 0 < quoted < wing:
        return None
    day, _, _ = session_read.session_clock(report["asof_ns"])
    left = (day.close_ns-report["asof_ns"])/MINUTE
    iv = report["prior"]["vix"]/100
    modeled = 0.
    from qte import _core
    for p in positions:
        q, right = p["quantity"], p["right"] == "C"
        mid, _ = _core.european_option(report["spot"], p["strike"], iv, left/(390*252), right)
        modeled += calm_condor.sell_fill(mid, .05) if q < 0 else -calm_condor.buy_fill(mid, .05)
    return dict(legs=positions, modeled_credit=round(modeled, 2), quoted_credit=quoted,
                entry_fee_usd=4*calm_condor.FEE,
                max_loss_usd=round((wing-quoted)*calm_condor.MULT+4*calm_condor.FEE, 2))


def note(report, directory):
    day, _, _ = session_read.session_clock(report["asof_ns"])
    in_window = opening_window(day, report["asof_ns"])
    calm = report["regime"] in calm_condor.CALM
    trade = shadow_trade(report) if in_window and calm else None
    status = ("outside_window" if not in_window else "non_calm" if not calm else
              "observed" if trade is not None else "incomplete_quotes")
    return dict(date=day.trading_date, status=status, asof_ns=report["asof_ns"],
                calm=calm, regime=report["regime"], gate=report["gate"], trade=trade, capture=str(directory),
                chain_sha256=report["chain_sha256"], regime_rules_sha256=report["regime_rules_sha256"])


def gate_status(path):
    records = forward_paper.read_ledger(path, recover_partial=False)
    if any(r.get("schema") != SCHEMA for r in records):
        raise forward_paper.LedgerMismatch("Unexpected observation ledger schema")
    scores = [r["gate"]["score"] for r in records
              if r["status"] in ("observed", "incomplete_quotes") and r["gate"]["score"] is not None]
    mid = median(scores) if scores else None
    return dict(records=len(records), calm_sessions_observed=len(scores), minimum_sessions=20,
                median_model_multiple=mid, threshold=.60, passed=len(scores) >= 20 and mid >= .60)


def observe(path, now):
    day, _, _ = session_read.session_clock(now)
    if not opening_window(day, now):
        return dict(date=day.trading_date, status="outside_window"), False
    prior = forward_paper.read_ledger(path, recover_partial=False)
    existing = next((r for r in prior if r["date"] == day.trading_date), None)
    if existing:
        return existing, False
    stamp = datetime.fromtimestamp(now//10**9, timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+str(now % 10**9)
    directory = truth_db.TRUTH_ROOT/"captures"/"schwab"/("condor-"+stamp)
    try:
        schwab_session.capture(directory, chain_only=True)
        body = note(session_read.read_session(directory), directory)
    except (AuthError, BrokerConnectionError, FileNotFoundError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, (AuthError, ValueError)) else type(exc).__name__
        body = dict(date=day.trading_date, status="blocked", reason=reason, asof_ns=now, capture=str(directory))
    return forward_paper.append_session(path, SCHEMA, body)


def expiry_values(trade, close):
    payoff = -sum(p["quantity"]*max(0., (close-p["strike"])*(1 if p["right"] == "C" else -1))
                  for p in trade["legs"])
    fee = trade["entry_fee_usd"]
    return dict(settlement=close, payoff=payoff,
                modeled_pnl_usd=round((trade["modeled_credit"]-payoff)*100-fee, 2),
                paper_pnl_usd=round((trade["quoted_credit"]-payoff)*100-fee, 2))


def settle(observations, path, db, now):
    rows = forward_paper.read_ledger(observations, recover_partial=False)
    output = []
    for row in rows:
        if row["schema"] != SCHEMA:
            raise forward_paper.LedgerMismatch("Unexpected observation ledger schema")
        if row["status"] != "observed":
            continue
        day, _, _ = session_read.session_clock(row["asof_ns"])
        if day.close_ns > now:
            continue
        label = day.trading_date
        matches = [b for b in truth_db.intraday(db, "SPX", "5m", start=label, end=label, session="rth")
                   if b["start_ns"] == day.close_ns-5*MINUTE]
        if not matches:
            output.append(dict(date=label, status="awaiting_final_spx_5m"))
            continue
        result = expiry_values(row["trade"], matches[0]["close"])
        body = dict(result, date=label, status="settled", observation_sha256=row["sha256"],
                    quoted_credit=row["trade"]["quoted_credit"], modeled_credit=row["trade"]["modeled_credit"],
                    fee_usd=row["trade"]["entry_fee_usd"], legs=row["trade"]["legs"],
                    settlement_bar_start_ns=matches[0]["start_ns"])
        record, _ = forward_paper.append_session(path, PAPER_SCHEMA, body)
        for k, v in body.items():
            if record[k] != v:
                raise forward_paper.LedgerMismatch(f"Settlement changed for {label}")
        output.append(record)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qte calm-condor")
    parser.add_argument("command", choices=("observe", "gate-status", "settle"))
    parser.add_argument("--replay", type=Path, help="Read a saved capture for gate-status diagnostics")
    args = parser.parse_args(argv)
    if args.replay and args.command != "gate-status":
        parser.error("--replay applies only to gate-status")
    try:
        if args.command == "observe":
            record, appended = observe(OBSERVATIONS, time_ns())
            result = dict(record, appended=appended)
        elif args.command == "settle":
            with closing(session_read.open_truth()) as db:
                result = settle(OBSERVATIONS, PAPER, db, time_ns())
        else:
            result = gate_status(OBSERVATIONS)
            if args.replay:
                result["replay"] = note(session_read.read_session(args.replay), args.replay)
    except (ValueError, OSError, forward_paper.LedgerMismatch) as error:
        parser.exit(2, f"qte calm-condor: {error}\n")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0
