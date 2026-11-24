"""Offline study entry points for S1 and S2."""
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters.robinhood_research import read_index_closes, read_index_minutes
from .adapters.schwab_research import read_minute_sessions
from .core_reversion_tape import gates, parse_day
from .core_vertical_replay import replay
from .json_input import strict_json
from .s7_study import schedule_for
from .spx_spreads import _fields, _finite
from .strategies.core_reversion import VwapRules, gap_setup, observations, vwap_setups
from .volatility import MINUTE_NS

NAMES = {"S1": "Intraday VWAP z-score reversion", "S2": "Opening gap fade"}


def inputs(args, strategy):
    sources, index, volumes, daily, options = [], {}, {}, {}, {}

    def record(path, digest):
        sources.append(dict(file=path.name, sha256=digest))

    for path in args.index_minute:
        days, digest = read_index_minutes(path, "SPX", include_open=True)
        if days.keys() & index.keys():
            raise ValueError("Duplicate SPX session")
        index.update(days)
        record(path, digest)
    if strategy == "S1":
        if args.spy_minute is None:
            raise ValueError("S1 requires --spy-minute for volume")
        spy, digest = read_minute_sessions(args.spy_minute, "SPY")
        volumes = {date: {b.start_ns: b.volume for b in bars} for date, bars in spy.items()}
        record(args.spy_minute, digest)
    else:
        if args.daily is None:
            raise ValueError("S2 requires --daily for the previous scheduled SPX close")
        daily, digest = read_index_closes(args.daily, "SPX")
        record(args.daily, digest)
    evidence = "observed index; regime, calendar and options unavailable"
    if args.options:
        raw = args.options.read_bytes()
        doc = strict_json(raw)
        _fields(doc, ("schema", "evidence", "symbol", "sessions"), "CORE research tape")
        if (doc["schema"] != "qte-core-spxw-v1" or doc["symbol"] != "SPX"
                or doc["evidence"] not in ("observed", "synthetic") or type(doc["sessions"]) is not dict):
            raise ValueError("Invalid CORE options tape declaration")
        options = doc["sessions"]
        evidence = f"observed index; {doc['evidence']} regime, calendar and option quotes"
        record(args.options, sha256(raw).hexdigest())
    return index, volumes, daily, schedule_for(index), options, sources, evidence


def study(strategy, index, volumes, daily, schedule, options, cash, fraction, fee, rules=VwapRules()):
    if (strategy not in NAMES or _finite(cash, "cash") <= 0 or not 0 < _finite(fraction, "risk fraction") <= .02
            or _finite(fee, "per-leg fee") < 0):
        raise ValueError("Expected positive capital, risk fraction in (0, 0.02], and nonnegative fee")
    equity, risk_fraction, fee = (Decimal(str(v)) for v in (cash, fraction, fee))
    sessions = [d for d in schedule.days if d.open_ns is not None]
    if set(index)-{d.trading_date for d in sessions} or set(options)-set(index):
        raise ValueError("Unexpected session date in index or option tape")
    rows = []
    unresolved = False
    for i, day in enumerate(sessions):
        label = day.trading_date
        if label not in index:
            continue
        bars = index[label]
        minutes = observations(day, bars, volumes.get(label, {}), strategy, rules)
        tape, calendar = parse_day(options[label], day) if label in options else (None, [])
        row = dict(date=label, usable_minutes=len(minutes), candidates=[])
        if strategy == "S1":
            setups = vwap_setups(day, minutes, rules)
            row["status"] = ("price_setup" if setups else "incomplete_price_or_volume" if
                             len(minutes) < min(300, (day.close_ns-day.open_ns)//MINUTE_NS-15)
                             else "no_confirmed_deviation")
        else:
            previous = daily.get(sessions[i-1].trading_date) if i else None
            opening = bars[day.open_ns][0] if day.open_ns in bars else None
            setups, details = gap_setup(day, minutes, opening, previous)
            row.update(details)
        for signal in setups:
            reasons = gates(strategy, day, signal["at_ns"], tape, calendar)
            row["candidates"].append(dict(signal, rejected_by=reasons))
            if reasons:
                continue
            if unresolved:
                trade = dict(status="skipped", reason="prior_unresolved_position")
            else:
                trade = replay(strategy, signal, day, minutes, tape, calendar, equity, risk_fraction, fee)
            row["trade"] = trade
            if trade["status"] == "closed":
                equity += Decimal(str(trade["net_pnl_usd"]))
                trade["ending_cash_usd"] = float(equity)
            if trade["status"] == "unresolved":
                unresolved = True
            break
        rows.append(row)
    closed = sum(r.get("trade", {}).get("status") == "closed" for r in rows)
    return dict(schema="qte-core-reversion-study-v1", strategy=strategy, sessions=len(rows), rows=rows,
                parameters=asdict(rules) if strategy == "S1" else dict(min_gap_pct=.3, max_gap_pct=1.0),
                price_setups_examined=sum(len(r["candidates"]) for r in rows), closed=closed, unresolved=unresolved,
                starting_cash_usd=cash, risk_fraction=fraction, fee_per_contract_side_usd=float(fee),
                net_pnl_usd=float(equity-Decimal(str(cash))) if closed and not unresolved else None)


def render(report):
    strategy = report["strategy"]
    lines = [f"# {strategy} · {NAMES[strategy]}", "", report["evidence"], "",
             "| Session | Price screen | Setups examined | Option result | Net USD |",
             "|---|---|---:|---|---:|"]
    blocked = Counter()
    for r in report["rows"]:
        trade = r.get("trade", {})
        pnl = trade.get("net_pnl_usd")
        for s in r["candidates"]:
            blocked.update(s["rejected_by"])
        lines.append(f"| {r['date']} | {r['status']} | {len(r['candidates'])} | "
                     f"{trade.get('reason', '—')} | {pnl if pnl is not None else '—'} |")
    if blocked:
        lines += ["", "Entry filters across examined price setups", ""]
        lines.extend(f"- {key}: {n}" for key, n in sorted(blocked.items()))
    pnl = report["net_pnl_usd"]
    for r in report["rows"]:
        trade = r.get("trade", {})
        if "entry_ns" in trade:
            lines += ["", f"**{r['date']} vertical** · Buy `{trade['long']}`, sell `{trade['short']}`. "
                      f"Entry debit {trade['debit']:.2f} index points. "
                      + (f"Exit credit {trade['credit']:.2f}. Fees ${trade['fees_usd']:.2f}."
                         if trade["status"] == "closed" else "Exit unresolved.")]
    lines += ["", f"Completed simulated trades: {report['closed']}. "
              + ("Option P&L unavailable." if pnl is None else f"Simulated net P&L: ${pnl:,.2f}."),
              "", "Fixed rules on the supplied sessions. Independent holdout, walk-forward, and forward fills remain untested."]
    return "\n".join(lines)+"\n"


def run(args):
    from .cli import write_json
    strategy = args.command[:2].upper()
    if args.demo:
        if args.options or getattr(args, "daily", None) or getattr(args, "spy_minute", None):
            raise ValueError("--demo supplies its own inputs")
        from .core_reversion_fixture import fixture
        index, volumes, daily, schedule, options = fixture(strategy)
        sources, evidence = [], "Synthetic mechanics fixture v1"
    else:
        index, volumes, daily, schedule, options, sources, evidence = inputs(args, strategy)
    rules = VwapRules(args.lookback, args.entry_z, args.stop_z) if strategy == "S1" else VwapRules()
    result = study(strategy, index, volumes, daily, schedule, options,
                   args.cash, args.risk_fraction, args.fee, rules)
    result.update(sources=sources, evidence=evidence, calendar_sha256=schedule.snapshot_sha256,
                  calendar_id=schedule.source_id)
    output = args.output
    if output.exists():
        raise FileExistsError(f"Study directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        dest = Path(temp)/"study"
        dest.mkdir()
        write_json(dest/"report.json", result)
        (dest/"report.md").write_text(render(result), encoding="utf8")
        dest.rename(output)
    return output
