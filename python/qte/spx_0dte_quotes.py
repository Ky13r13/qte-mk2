"""Offline observed-quote replay for one declared same-day SPXW contract."""
from argparse import ArgumentParser
from bisect import bisect_left, bisect_right
from datetime import date, time, timedelta
import json
from pathlib import Path

from qte.adapters.theta_quote_csv import QuoteSession, read_quotes, read_spot

DAY = "2025-08-19"
STRIKE = 6455.0
SAMPLE = QuoteSession(date.fromisoformat(DAY), STRIKE, "CALL", time(16))
SOURCE = Path("build/tick-data-evaluation/theta")
FEE = 1.30
MULTIPLIER = 100
OTM_RANGE = (.0015, .0025)
MAX_QUOTE_WAIT = timedelta(seconds=10)
HOLD = timedelta(minutes=5)
STOP = -.30


def load(source, session=SAMPLE, *, spot_path=None, quotes_path=None, hashes=None):
    spot_path = spot_path or source / "SPX_price.csv"
    quotes_path = quotes_path or source / f"SPXW_{session.day:%Y%m%d}_quotes_tick.csv"
    prices = read_spot(spot_path, session, hashes=hashes)
    quotes, counts = read_quotes(quotes_path, session, hashes=hashes)
    if (quotes[0][0] > prices[-1][0] + MAX_QUOTE_WAIT
            or prices[0][0] > quotes[-1][0] + MAX_QUOTE_WAIT):
        raise ValueError("SPX spot and selected SPXW quote series have disjoint coverage")
    return prices, quotes, counts, [spot_path, quotes_path]


def replay(prices, quotes, session=SAMPLE):
    """Replay validated, source-ordered observations. Every scheduled decision is retained."""
    spot_clock = [x[0] for x in prices]
    quote_clock = [x[0] for x in quotes]
    starts = [session.opens_at + i * timedelta(minutes=30) for i in range(13)]
    starts.append(session.opens_at.replace(hour=15, minute=50))
    close = session.closes_at
    outcomes = []

    def later(when):
        index = bisect_right(quote_clock, when)
        if index < len(quotes) and quote_clock[index] - when <= MAX_QUOTE_WAIT:
            return index
        return None

    def run(row, decision, seconds, target, offset):
        latency = timedelta(seconds=seconds)
        if decision + latency + HOLD + latency >= close:
            return "insufficient_time_before_close"
        if offset is None:
            return "no_strictly_prior_fresh_spot"
        if not row["quote_range_covers_decision"]:
            return "no_quote_coverage_at_decision"
        entry = later(decision + latency)
        if entry is None:
            return "no_fresh_later_entry_quote"
        if not OTM_RANGE[0] <= offset <= OTM_RANGE[1]:
            return "strike_outside_fixed_otm_range"
        entered, _, ask = quotes[entry]
        deadline = entered + HOLD
        if deadline + latency >= close:
            return "insufficient_time_before_close"
        debit = ask * MULTIPLIER + FEE
        row.update(entry_time=entered.isoformat(), ask=ask, debit=debit)
        previous = entered
        trigger, why = deadline, "time_exit"
        for index in range(bisect_right(quote_clock, entered), bisect_left(quote_clock, deadline)):
            stamp, bid, _ = quotes[index]
            if stamp - previous > MAX_QUOTE_WAIT:
                row.update(exit_reason="observation_gap", conservative_pnl=-debit)
                return "unresolved_exit"
            previous = stamp
            net_return = (bid * MULTIPLIER - FEE) / debit - 1
            if net_return >= target or net_return <= STOP:
                trigger = stamp
                why = "target" if net_return >= target else "stop"
                break
        row.update(trigger_time=trigger.isoformat(), exit_reason=why)
        if why == "time_exit" and deadline - previous > MAX_QUOTE_WAIT:
            row.update(exit_reason="observation_gap", conservative_pnl=-debit)
            return "unresolved_exit"
        exit_index = later(trigger + latency)
        if exit_index is None:
            row.update(conservative_pnl=-debit)
            return "unresolved_exit"
        finished, bid, _ = quotes[exit_index]
        pnl = bid * MULTIPLIER - FEE - debit
        row.update(exit_time=finished.isoformat(), bid=bid, net_pnl=round(pnl, 8), net_return=pnl / debit)
        return "observed_quote_replay"

    for decision in starts:
        if decision >= close:
            continue
        prior = bisect_left(spot_clock, decision) - 1
        has_spot = prior >= 0 and decision - spot_clock[prior] <= MAX_QUOTE_WAIT
        spot = prices[prior][1] if has_spot else None
        offset = None if spot is None else (session.strike / spot - 1) * (1 if session.right == "CALL" else -1)
        covered = quote_clock[0] <= decision <= quote_clock[-1]
        for seconds in (1, 5):
            for target in (.30, .50):
                row = dict(decision=str(decision.time()), latency_seconds=seconds, target=target,
                           otm_fraction=offset, prior_spot=spot, quote_range_covers_decision=covered)
                row[f"{session.right.lower()}_quote_range_covers_decision"] = covered
                row["status"] = run(row, decision, seconds, target, offset)
                outcomes.append(row)
    return outcomes


def make_report(session, prices, quotes, counts, hashes):
    side = session.right.lower()
    return dict(session=session.day.isoformat(), strike=session.strike, right=session.right,
                session_close_et=session.close.isoformat(), fee_per_side=FEE, multiplier=MULTIPLIER,
                max_wait_seconds=MAX_QUOTE_WAIT.total_seconds(), target_otm=.002, otm_range=OTM_RANGE,
                nominal_hold_minutes=5, stop=STOP, exclusions=counts, sources=hashes,
                **{f"observed_{side}_quotes": len(quotes), f"first_{side}_quote": quotes[0][0].isoformat(),
                   f"last_{side}_quote": quotes[-1][0].isoformat()},
                pricing="one displayed contract at later ask for entry and later bid for liquidation",
                limitations=["Assumed New York source clock, no receipt timestamps or measured latency.",
                             "Condition 50 and displayed size do not confirm a fill.",
                             "Late observations are discarded. A monitored gap over 10s leaves the exit unresolved."],
                outcomes=replay(prices, quotes, session))


def markdown(report):
    side = report["right"].lower()
    lines = [
        "# One-day observed 0DTE quote replay", "",
        f"SPXW {report['session']} {report['strike']:g} {side}. Session close {report['session_close_et']} ET. "
        f"The file supplies {report[f'observed_{side}_quotes']:,} admitted quotes "
        f"from {report[f'first_{side}_quote']} through {report[f'last_{side}_quote']} ET.", "",
        "Each row represents one alternative for one contract. The strike must be 0.15–0.25% OTM. "
        "Entry pays a later ask. A later bid triggers the net target or −30% stop. The time limit "
        "is five minutes after entry. Exit uses a subsequent bid after the assumed delay. Fees are $1.30 per side.", "",
        *report["limitations"], "",
        "| ET | Offset | Delay | Target | Result | Ask | Exit bid | Net P&L |",
        "|---|---:|---:|---:|---|---:|---:|---:|",
    ]
    for row in report["outcomes"]:
        offset = "—" if row["otm_fraction"] is None else f"{row['otm_fraction']:.3%}"
        numbers = [f"{row[key]:.2f}" if key in row else "—" for key in ("ask", "bid", "net_pnl")]
        lines.append(f"| {row['decision']} | {offset} | {row['latency_seconds']}s | {row['target']:.0%} "
                     f"| {row['status'].replace('_', ' ')} | {' | '.join(numbers)} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE, help="Directory with standard Theta sample filenames")
    parser.add_argument("--spot-csv", type=Path)
    parser.add_argument("--quotes-csv", type=Path)
    parser.add_argument("--session", type=date.fromisoformat)
    parser.add_argument("--strike", type=float)
    parser.add_argument("--right", choices=("CALL", "PUT"))
    parser.add_argument("--close-et", type=time.fromisoformat)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    contract = (args.session, args.strike, args.right, args.close_et)
    if args.spot_csv or args.quotes_csv or any(x is not None for x in contract):
        if any(x is None for x in contract):
            parser.error("Custom captures require --session, --strike, --right and --close-et")
        session = QuoteSession(*contract)
    else:
        session = SAMPLE
    hashes = {}
    prices, quotes, counts, _ = load(args.source, session, spot_path=args.spot_csv,
                                    quotes_path=args.quotes_csv, hashes=hashes)
    report = make_report(session, prices, quotes, counts, hashes)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text(markdown(report), encoding="utf-8")
    resolved = sum(row["status"] == "observed_quote_replay" for row in report["outcomes"])
    print(f"{resolved} resolved scenario rows across {len(report['outcomes'])} alternatives. {args.output / 'report.md'}")


if __name__ == "__main__":
    main()
