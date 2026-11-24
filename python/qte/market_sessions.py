"""Intraday option admission using a pinned, complete cash-session schedule.

Only the overlap with the underlying cash session is eligible. Broker or venue
restrictions can shorten that window. Expiry is the listed contract date.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from time import time_ns
from zoneinfo import ZoneInfo

from qte.adapters.exchange_sessions import import_xnys
from qte.adapters.session_bundle import load_session_bundle, schedule_from_dict
from qte.json_input import strict_json
from qte.live_outputs import publish
from qte.session_calendar import SessionSchedule

NS = 1_000_000_000
PRODUCTS = ("SPXW_PM", "SPX_AM", "SPY_OPTION")
NY = ZoneInfo("America/New_York")
POLICY = "us_options_cash_rth_v1"


def _nanos(value, name):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError(f"{name} must be nonnegative signed-64-bit nanoseconds")
    return value


def _last_date(schedule, expiry, product):
    expiry_day = schedule.day(expiry)
    if expiry_day.open_ns is None:
        raise ValueError("expiry must be the listed contract expiration on an open date")
    if product != "SPX_AM":
        return expiry_day.trading_date
    cursor = date.fromisoformat(expiry)
    while cursor.isoformat() > schedule.coverage_start:
        cursor -= timedelta(days=1)
        prior = schedule.day(cursor.isoformat())
        if prior.open_ns is not None:
            return prior.trading_date
    raise ValueError("AM settlement requires coverage of the preceding trading session")


def entry_window(schedule: SessionSchedule, *, product: str, expiry: str, now_ns: int,
                 hold_ns: int, buffer_ns: int, broker_cutoff_ns: int | None = None,
                 contract_cutoff_ns: int | None = None) -> dict:
    """Test admission as of a receipt time. Hold plus reserve may equal the cutoff."""
    if not isinstance(schedule, SessionSchedule) or product not in PRODUCTS:
        raise ValueError(f"require a QTE schedule and one of {', '.join(PRODUCTS)}")
    _nanos(now_ns, "now_ns")
    _nanos(hold_ns, "hold_ns")
    _nanos(buffer_ns, "buffer_ns")
    if broker_cutoff_ns is not None:
        _nanos(broker_cutoff_ns, "broker_cutoff_ns")
    if contract_cutoff_ns is not None:
        _nanos(contract_cutoff_ns, "contract_cutoff_ns")
    label = datetime.fromtimestamp(now_ns // NS, timezone.utc).astimezone(NY).date().isoformat()
    session = schedule.day(label)
    last_date = _last_date(schedule, expiry, product)
    cutoff = session.close_ns
    if cutoff is not None:
        # Reject schedules from other venues that run outside US cash RTH. An
        # actual delay of the open or advance of the close is conservative.
        opening = datetime.fromtimestamp(session.open_ns // NS, NY)
        close = datetime.fromtimestamp((cutoff - 1) // NS, NY)
        if (opening.date().isoformat() != label or close.date().isoformat() != label
                or (opening.hour, opening.minute) < (9, 30) or close.hour >= 16):
            raise ValueError("declared interval exceeds US cash regular hours")
        for restriction in (contract_cutoff_ns, broker_cutoff_ns):
            if restriction is not None:
                cutoff = min(cutoff, restriction)
    # The deadline must exist on a date when the selected series is tradable.
    # On later dates portfolio exits may need settlement accounting instead.
    if label > last_date:
        cutoff = None
        reason = "past_last_trading_date"
    elif cutoff is None:
        reason = "closed_session"
    elif now_ns < session.open_ns:
        reason = "before_open"
    elif now_ns >= cutoff:
        reason = "at_or_after_cutoff"
    elif now_ns + hold_ns + buffer_ns > cutoff:
        reason = "insufficient_time"
    else:
        reason = "admitted"
    return dict(policy=POLICY, product=product, expiry=expiry, last_trading_date=last_date,
                trading_date=label, admitted=reason == "admitted", reason=reason,
                session_open_ns=session.open_ns, session_close_ns=session.close_ns,
                product_last_cash_close_ns=schedule.day(last_date).close_ns,
                contract_cutoff_ns=contract_cutoff_ns, broker_cutoff_ns=broker_cutoff_ns, cutoff_ns=cutoff,
                remaining_ns=None if cutoff is None else max(0, cutoff - now_ns),
                last_entry_ns=None if cutoff is None else min(cutoff - 1, cutoff - hold_ns - buffer_ns))


def load_schedule(path: Path) -> SessionSchedule:
    value = strict_json(path.read_bytes())
    if type(value) is dict and value.get("schema") == "qte-session-bundle-v1":
        value = value.get("schedule")
    return schedule_from_dict(value)


def _write(path, payload):
    raw = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Export XNYS regular-session hours")
    export.add_argument("--start", required=True)
    export.add_argument("--end", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--bars-input", type=Path,
                        help="Optional QTE bundle fields without schedule, including captured bars")
    export.add_argument("--tick-size", type=float, default=0.01,
                        help="Canonical input tick size for --bars-input validation")
    check = sub.add_parser("check", help="Inspect one timestamp for a paper entry")
    check.add_argument("--schedule", type=Path, required=True)
    check.add_argument("--product", choices=PRODUCTS, required=True)
    check.add_argument("--expiry", required=True)
    check.add_argument("--now-ns", type=int, help="Evaluation time in UTC ns (default: system clock)")
    check.add_argument("--hold-seconds", type=int, required=True)
    check.add_argument("--buffer-seconds", type=int, required=True)
    check.add_argument("--broker-cutoff-ns", type=int)
    check.add_argument("--contract-cutoff-ns", type=int)
    check.add_argument("--desk", type=Path, nargs="?", const=Path("build/live-desk"), metavar="DIR",
                       help="Publish the schedule and entry policy for the local desk")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            schedule = load_schedule(args.schedule)
            policy = dict(product=args.product, expiry=args.expiry, hold_ns=args.hold_seconds * NS,
                          buffer_ns=args.buffer_seconds * NS, broker_cutoff_ns=args.broker_cutoff_ns,
                          contract_cutoff_ns=args.contract_cutoff_ns)
            now = time_ns() if args.now_ns is None else args.now_ns
            result = entry_window(schedule, now_ns=now, **policy)
            if args.desk is not None:
                publish(args.desk / "session.json", dict(schedule=asdict(schedule), **policy))
            print(json.dumps(result, sort_keys=True))
            return 0
        schedule = import_xnys(args.start, args.end)
        payload = asdict(schedule)
        if args.bars_input:
            bars = strict_json(args.bars_input.read_bytes())
            if type(bars) is not dict or "schedule" in bars:
                raise ValueError("--bars-input must be QTE bundle fields without schedule")
            payload = {**bars, "schedule": payload}
            load_session_bundle(json.dumps(payload, allow_nan=False).encode("utf-8"), tick_size=args.tick_size)
        _write(args.output, payload)
        print(f"{args.output}  {schedule.source_id}  snapshot={schedule.snapshot_sha256}")
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
