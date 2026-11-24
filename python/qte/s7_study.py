"""Run the S7 signal and quote replay from local, identified research inputs."""
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .adapters.exchange_sessions import import_xnys
from .adapters.robinhood_research import read_index_minutes
from .adapters.schwab_research import read_minute_sessions
from .json_input import strict_json
from .s7_execution import Account, money, replay
from .s7_tape import parse_session
from .spx_spreads import _fields, _finite
from .strategies.trend_day_convexity import LOOKBACK, five_minutes, index_window, screen


def schedule_for(dates):
    last = date.fromisoformat(max(dates))
    first = date.fromisoformat(min(dates)) - timedelta(days=45)
    return import_xnys(first.isoformat(), last.replace(day=monthrange(last.year, last.month)[1]).isoformat())


def study(index, volume, schedule, options, symbol, cash, fee):
    if _finite(cash, "starting capital") <= 0 or _finite(fee, "per-contract fee") < 0:
        raise ValueError("Starting capital must be positive and fees nonnegative")
    account = Account(money(cash))
    rows, prior = [], []
    sessions = [d for d in schedule.days if d.open_ns is not None]
    remaining = Counter(d.trading_date[:7] for d in sessions)
    if set(options)-set(index):
        raise ValueError("Options tape contains sessions outside supplied index evidence")
    if (set(index) | set(volume))-{d.trading_date for d in sessions}:
        raise ValueError("Market data contains a date outside the open schedule")
    for day in sessions:
        label, month = day.trading_date, day.trading_date[:7]
        if label in index:
            tape = parse_session(options[label], day, symbol) if label in options else None
            row = screen(day, index[label], volume.get(label, {}), prior[-LOOKBACK:], tape)
            if row["signal"]:
                if account.unresolved:
                    row["trade"] = dict(status="skipped", reason="prior_unresolved_position")
                else:
                    row["trade"] = replay(row, day, index[label], tape, account, remaining[month], money(fee))
            rows.append(row)
        prior.append(volume.get(label, {}))
        remaining[month] -= 1
    trades = [r["trade"] for r in rows if "trade" in r]
    closed = sum(t["status"] == "closed" for t in trades)
    return dict(schema="qte-s7-study-v1", symbol=symbol, volume_proxy="SPY", starting_cash_usd=cash,
                per_contract_fee_usd=fee, sessions=len(rows), signals=len(trades), closed=closed,
                unresolved=account.unresolved, rows=rows,
                net_pnl_usd=float(account.cash-money(cash)) if closed and not account.unresolved else None)


def captures(paths, spy_path, symbol):
    all_index, sources = {}, []
    for path in paths:
        sessions, digest = read_index_minutes(path, symbol)
        if set(sessions) & set(all_index):
            raise ValueError("Index session supplied more than once")
        all_index.update(sessions)
        sources.append(dict(file=path.name, sha256=digest))
    spy, digest = read_minute_sessions(spy_path, "SPY")
    sources.append(dict(file=spy_path.name, sha256=digest))
    schedule = schedule_for(set(all_index) | set(spy))

    def slots(day, series, reducer):
        session = schedule.day(day)
        if session.open_ns is None:
            raise ValueError("Unexpected index or volume data on a closed date")
        return five_minutes(series, session.open_ns, session.close_ns, reducer)
    index = {d: slots(d, rows, index_window) for d, rows in all_index.items()}
    volume = {d: slots(d, {b.start_ns: b.volume for b in rows}, sum) for d, rows in spy.items()}
    return index, volume, schedule, sources


def render(report):
    lines = ["# S7 trend-day convexity", "", f"Evidence: {report['evidence']}. Index: {report['symbol']}. "
             "Volume proxy: SPY.", "", "| Session | Outside-range closes examined | Above 1.5× / measurable | Signal | Option replay |",
             "|---|---:|---:|---|---|"]
    for row in report["rows"]:
        measured = [b["relative_volume"] for b in row["breakouts"] if b["relative_volume"] is not None]
        volume_result = f"{sum(v > 1.5 for v in measured)} / {len(measured)}" if measured else "—"
        trade = row.get("trade", {})
        lines.append(f"| {row['date']} | {len(row['breakouts'])} | {volume_result} | {row['status']} | "
                     f"{trade.get('reason', trade.get('status', '—'))} |")
    reasons = Counter(reason for r in report["rows"] for b in r["breakouts"] for reason in b["reasons"])
    if reasons:
        lines += ["", "Filter counts across examined closes (a close can fail multiple filters).", ""]
        lines += [f"- {key}: {n}" for key, n in sorted(reasons.items())]
    eastern = ZoneInfo("America/New_York")
    for row in report["rows"]:
        trade = row.get("trade", {})
        if trade.get("fills"):
            lines += ["", f"#### {row['date']} · {trade['contract'].strip()}", "",
                      "| Time (ET) | Event | Contracts | Price | Fees |", "|---|---|---:|---:|---:|"]
            for fill in trade["fills"]:
                time = datetime.fromtimestamp(fill["at_ns"]/1e9, eastern).strftime("%H:%M:%S")
                lines.append(f"| {time} | {fill.get('reason', 'entry')} | {fill['quantity']} | "
                             f"{fill['price']:.2f} | {fill['fee_usd']:.2f} |")
    pnl = report["net_pnl_usd"]
    lines += ["", f"Completed simulated trades: {report['closed']}. "
              + ("Option P&L unavailable." if pnl is None else f"Simulated net P&L: ${pnl:,.2f}."),
              "", "Fixed rules, retrospective replay. The JSON includes each decision, fill and input checksum. "
              "Independent evaluation and forward fills remain outstanding."]
    return "\n".join(lines)+"\n"


def run(args):
    from .cli import write_json
    if args.demo:
        from .s7_fixture import fixture
        if args.spy_minute or args.options:
            raise ValueError("The synthetic demo supplies its own market and options")
        index, volume, schedule, options = fixture(args.symbol)
        sources, evidence = [], "synthetic mechanics fixture v1"
    else:
        if not args.spy_minute:
            raise ValueError("--index-minute requires --spy-minute for volume")
        index, volume, schedule, sources = captures(args.index_minute, args.spy_minute, args.symbol)
        options, evidence = {}, "observed underlying and proxy volume; options unavailable"
        if args.options:
            raw = args.options.read_bytes()
            payload = strict_json(raw)
            _fields(payload, ("schema", "symbol", "evidence", "sessions"), "S7 options tape")
            if (payload["schema"] != "qte-s7-quotes-v1" or payload["symbol"] != args.symbol
                    or payload["evidence"] not in ("observed", "synthetic") or type(payload["sessions"]) is not dict):
                raise ValueError("Invalid S7 options tape metadata")
            options = payload["sessions"]
            evidence = f"observed index and proxy volume; {payload['evidence']} options and regime tape"
            sources.append(dict(file=args.options.name, sha256=sha256(raw).hexdigest()))
    report = study(index, volume, schedule, options, args.symbol, args.cash, args.fee)
    report.update(evidence=evidence, sources=sources, calendar_id=schedule.source_id,
                  calendar_sha256=schedule.snapshot_sha256)
    output = args.output
    if output.exists():
        raise FileExistsError(f"Study directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as staging:
        dest = Path(staging)/"report"
        dest.mkdir()
        write_json(dest/"report.json", report)
        (dest/"report.md").write_text(render(report), encoding="utf-8")
        dest.rename(output)
    return output
