"""Compare frozen S5 decisions to SPY uptrend days at the identical intraday clock."""
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from statistics import mean, median

from .adapters.exchange_sessions import import_xnys
from .adapters.schwab_research import MINUTE_NS, read_daily_closes, read_minute_sessions
from .strategies.oversold_bounce import S5Config, S5Signal
from .json_input import strict_json

HORIZONS = (1, 3, 5)


def observe_sessions(minutes, daily, schedule, config):
    state = S5Signal(config)
    rows = []
    first, last = min(minutes), max(minutes)
    for session in schedule.days:
        day = session.trading_date
        if session.open_ns is None:
            if day in minutes or day in daily:
                raise ValueError(f"Candle on a closed date: {day}")
            continue
        if first <= day <= last:
            bars = minutes.get(day, ())
            if any(b.start_ns < session.open_ns or b.end_ns > session.close_ns for b in bars):
                raise ValueError(f"Minute outside declared session: {day}")
            cutoff = session.close_ns - 15 * MINUTE_NS
            prefix = [b for b in bars if b.end_ns <= cutoff]
            standard = session.close_ns - session.open_ns == 390 * MINUTE_NS
            row = dict(date=day, standard_session=standard, decision_ns=cutoff,
                       mark_ns=cutoff + MINUTE_NS, mark_price=None, features=None)
            for bar in bars:
                if bar.start_ns == row["mark_ns"]:
                    row["mark_price"] = bar.open
                    break
            if not prefix or len(prefix) * MINUTE_NS != cutoff - session.open_ns:
                row["status"] = "incomplete_prefix"
            else:
                features = state.snapshot(max(b.high for b in prefix), min(b.low for b in prefix), prefix[-1].close)
                row.update(features=features, status="observed" if features else "insufficient_daily_history")
            rows.append(row)
        # Commit the official daily candle only after the intraday observation.
        if day in daily:
            state.finish_day(*daily[day])
        else:
            state = S5Signal(config)
    return rows


def _outcome(row, exit_row):
    if row["mark_price"] is None or exit_row["mark_price"] is None:
        return None
    return exit_row["mark_price"] / row["mark_price"] - 1


def _exits(rows, index):
    entry = rows[index]
    results = {}
    for h in HORIZONS:
        end = index + h
        results[f"{h}d"] = _outcome(entry, rows[end]) if end < len(rows) else None
    results["rule"] = None
    reason = "end_of_capture"
    exit_date = None
    for age, future in enumerate(rows[index+1:index+6], 1):
        if future["features"] is None:
            reason = "missing_exit_signal"
            break
        if future["features"]["exit_signal"] or age == 5:
            results["rule"] = _outcome(entry, future)
            exit_date = future["date"]
            reason = "indicator" if future["features"]["exit_signal"] else "time"
            if results["rule"] is None:
                reason = "missing_mark"
            break
    return results, exit_date, reason


def _summary(cohort):
    out = {}
    for key in ("1d", "3d", "5d", "rule"):
        valid = [r["returns"][key] for r in cohort if r["returns"][key] is not None]
        out[key] = dict(n=len(valid), censored=len(cohort)-len(valid),
                        mean=mean(valid) if valid else None, median=median(valid) if valid else None,
                        positive_fraction=sum(v > 0 for v in valid)/len(valid) if valid else None)
    return out


def study(minutes, daily, schedule, config):
    rows = observe_sessions(minutes, daily, schedule, config)
    eligible = []
    for i, row in enumerate(rows):
        if row["standard_session"] and row["features"] and row["features"]["uptrend"]:
            row["returns"], row["rule_exit_date"], row["rule_exit_reason"] = _exits(rows, i)
            if row["rule_exit_reason"] == "end_of_capture":
                row["interim_date"] = rows[-1]["date"]
                row["interim_spy_return"] = _outcome(row, rows[-1])
            eligible.append(row)
    groups = dict(all_uptrend=eligible, s5=[r for r in eligible if r["features"]["signal"]],
                  uptrend_without_signal=[r for r in eligible if not r["features"]["signal"]])
    return dict(rows=rows, sessions=len(rows), cohort_sizes={k:len(v) for k,v in groups.items()},
                cohorts={k:_summary(v) for k,v in groups.items()})


def render_report(report):
    lines = ["# S5 oversold bounce", "", f"Retrospective SPY observations, {report['first']} through {report['last']}.",
             "", "| Gross mean SPY return | S5 (n) | All eligible uptrend days (n) | Other uptrend days (n) |",
             "|---|---:|---:|---:|"]
    for key, name in (("1d","1 session"),("3d","3 sessions"),("5d","5 sessions"),("rule","Indicator or time exit")):
        cells = []
        for cohort in ("s5","all_uptrend","uptrend_without_signal"):
            value = report["cohorts"][cohort][key]
            cells.append(f"{value['mean']:.3%} ({value['n']})" if value["n"] else "— (0)")
        lines.append("| " + " | ".join([name, *cells]) + " |")
    lines += ["", "The all-uptrend group includes the S5 dates. Events can overlap. Counts exclude unavailable exits.",
              "SPY marks are one minute after the decision. Dividends, transaction costs and the spread premium stop are excluded from these underlying returns.",
              "This is a fixed-parameter retrospective screen. The period has no independent holdout or walk-forward test."]
    for row in report["rows"]:
        if row.get("interim_spy_return") is not None and row["features"]["signal"]:
            lines.append(f"Open S5 event from {row['date']}: interim SPY return {row['interim_spy_return']:.3%} at the {row['interim_date']} mark.")
    proxy = report["spy_equity_proxy"]
    if proxy["status"] == "simulated":
        lines.extend(["", "Native QTE one-share SPY proxy (starting cash $10,000; one position at a time):"])
        for name, case in proxy["cases"].items():
            lines.append(f"- {name}: end equity ${case['final_equity_usd']:,.2f}; P&L ${case['pnl_usd']:,.2f}, {case['closed_trades']} closed and {case['open_trades']} open.")
    options = report.get("spxw")
    if options:
        lines += ["", f"SPXW paired-quote evidence: {options['evidence']}. Closed probes: {options['closed']}. Unresolved: {options['unresolved']}.",
                  f"Closed-only modeled P&L in USD: {options['closed_only_pnl_usd']}."]
    else:
        lines += ["", "SPXW spread P&L is unavailable. No matching chain and quote tape was supplied."]
    return "\n".join(lines)+"\n"


def run(daily_path: Path, minute_path: Path, output: Path, config_path=None, spread_data=None):
    from .cli import write_json
    raw_config = config_path.read_bytes() if config_path else b"{}"
    config = S5Config(**strict_json(raw_config))
    daily, daily_hash = read_daily_closes(daily_path, "SPY")
    minutes, minute_hash = read_minute_sessions(minute_path, "SPY")
    end = date.fromisoformat(max(max(daily), max(minutes))) + timedelta(days=7)
    schedule = import_xnys(min(min(daily), min(minutes)), end.isoformat())
    report = study(minutes, daily, schedule, config)
    from .s5_equity import equity_proxy
    report["spy_equity_proxy"] = equity_proxy(minutes, report["rows"], schedule)
    report.update(schema="qte-s5-signal-study-v1", evidence="retrospective_underlying_price_event_study",
                  parameters=asdict(config), symbol="SPY", first=min(minutes), last=max(minutes),
                  sources=dict(daily_sha256=daily_hash, minute_sha256=minute_hash,
                               config_sha256=sha256(raw_config).hexdigest(), calendar_id=schedule.source_id,
                               calendar_sha256=schedule.snapshot_sha256),
                  option_pnl=None, units="fractional gross SPY price return per independent observation",
                  selection="15:45 ET on regular full sessions; partial daily RSI2/IBS/SMA200; mark one minute later",
                  validation="entire observed window retrospective; thresholds fixed; no optimization or holdout")
    if spread_data is not None:
        from .s5_spreads import replay_options
        raw = spread_data.read_bytes()
        report["spxw"] = replay_options(report, schedule, strict_json(raw))
        report["sources"]["option_tape_sha256"] = sha256(raw).hexdigest()
        options = report["spxw"]
        if options["evidence"] == "observed" and options["closed"] == len(options["probes"]):
            report["option_pnl"] = report["spxw"]["closed_only_pnl_usd"]
    if output.exists():
        raise FileExistsError(f"Study output already exists: {output}")
    output.parent.mkdir(exist_ok=True, parents=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        result = Path(temporary) / "study"
        result.mkdir()
        write_json(result / "report.json", report)
        (result / "report.md").write_text(render_report(report), encoding="utf-8")
        result.rename(output)
    return output
