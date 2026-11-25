"""One command per trading day: paper trials at real quotes plus the post-close report.

    qte day run                  # start by 09:40 ET; asks once for the Schwab app key/secret
                                 # (memory only), refreshes the token itself, stops after the report
    qte day report [--date D]    # rebuild a day's report offline from the truth DB and ledgers

Schedule (ET): 09:40-11:35 early-ORB trial (qte.orb_forward) - 15:49 close capture -
16:01 close settlement - 16:20 data collection and the daily report. No orders are sent.
Report files: data/truth/reports/daily/<date>.md and .json
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import getpass
import json
import math
from pathlib import Path
from statistics import fmean
import sys
import time

from qte import forward_paper, orb_forward, regime_lab, truth_db

MINUTE = orb_forward.MINUTE
CLOSE_DIR = truth_db.TRUTH_ROOT / "forward" / "close"
CLOSE_ENTRIES = CLOSE_DIR / "entries.ndjson"
CLOSE_SETTLES = CLOSE_DIR / "settles.ndjson"
REPORTS = truth_db.TRUTH_ROOT / "reports" / "daily"
REGIME_REPORT = Path("build/research/regime-lab-20261124/report.json")
FEE = 1.25


# ---------------------------------------------------------------- 15:50 close study (pure)

def close_entry(snap, day, now):
    """ATM call/put asks at ~15:50, the straddle-implied move and the 30-minute momentum."""
    spx = orb_forward.candles(snap["spx-5m"], now)
    chain = snap["spx-chain-0dte"]
    spot = chain["response"].get("underlyingPrice")
    c1550, c1520 = spx.get(day.close_ns - 15 * MINUTE), spx.get(day.close_ns - 45 * MINUTE)  # bars ending 15:50 / 15:20
    if spot is None or c1550 is None or c1520 is None:
        return dict(status="incomplete", reason="missing spot or 15:20/15:50 bars")
    call = orb_forward.pick_contract(chain, day.trading_date, spot, 1)
    put = orb_forward.pick_contract(chain, day.trading_date, spot, -1)
    if not call or not put or not call.get("ask") or not put.get("ask"):
        return dict(status="incomplete", reason="missing ATM quotes")
    momentum = c1550[3] - c1520[3]
    straddle_mid = (call["bid"] + call["ask"] + put["bid"] + put["ask"]) / 2
    return dict(status="captured", entry_ns=now, spot=spot, strike=call["strike"], call=call, put=put,
                momentum_points=round(momentum, 2), momentum_side=1 if momentum > 0 else -1 if momentum < 0 else 0,
                straddle_ask=round(call["ask"] + put["ask"], 2), implied_abs_move=round(straddle_mid, 2))


def close_settle(entry, settlement):
    k = entry["strike"]
    call_value, put_value = max(settlement - k, 0.0), max(k - settlement, 0.0)
    pnl = lambda value, paid: round((value - paid) * 100 - FEE, 2)  # cash-settled: one fee
    call_pnl, put_pnl = pnl(call_value, entry["call"]["ask"]), pnl(put_value, entry["put"]["ask"])
    side = entry["momentum_side"]
    return dict(settlement=settlement, realized_abs_move=round(abs(settlement - entry["spot"]), 2),
                implied_abs_move=entry["implied_abs_move"], call_pnl_usd=call_pnl, put_pnl_usd=put_pnl,
                straddle_pnl_usd=round(call_pnl + put_pnl, 2),
                momentum_pnl_usd=None if side == 0 else (call_pnl if side > 0 else put_pnl))


def close_status(entries=CLOSE_ENTRIES, settles=CLOSE_SETTLES):
    rows = forward_paper.read_ledger(settles, recover_partial=False) if settles.exists() else []
    if not rows:
        return dict(sessions=0)
    real, implied = [r["realized_abs_move"] for r in rows], [r["implied_abs_move"] for r in rows]
    mom = [r["momentum_pnl_usd"] for r in rows if r["momentum_pnl_usd"] is not None]
    return dict(sessions=len(rows), mean_realized_move=round(fmean(real), 2), mean_implied_move=round(fmean(implied), 2),
                realized_over_implied=round(fmean(real) / fmean(implied), 3) if fmean(implied) else None,
                straddle_mean_pnl_usd=round(fmean(r["straddle_pnl_usd"] for r in rows), 2),
                momentum_leg_mean_pnl_usd=round(fmean(mom), 2) if mom else None,
                note="Options are cheap at 15:50 only if realized_over_implied stays above 1 after costs")


# ---------------------------------------------------------------- what the day was (pure)

def day_outcome(bars, prior_close, prior_vix):
    """Session descriptors in expected-move units, same definitions as truth_db.regime_outcomes."""
    em = prior_close * prior_vix / 100 / math.sqrt(252)
    o, c = bars[0]["open"], bars[-1]["close"]
    hi, lo = max(b["high"] for b in bars), min(b["low"] for b in bars)
    rng = hi - lo
    close_sigma, high_sigma, low_sigma = (c - prior_close) / em, (hi - prior_close) / em, (lo - prior_close) / em
    loc = (c - lo) / rng if rng > 0 else 0.5
    last10 = (c - bars[-3]["close"]) / (prior_vix / 100 * math.sqrt(10 / (390 * 252)) * prior_close) if len(bars) >= 3 else None
    labels = []
    gap = (o - prior_close) / em
    if gap >= 1:
        labels.append("gap_up_day")
    if gap <= -1:
        labels.append("gap_down_day")
    if rng / em >= 1 and loc >= 0.9:
        labels.append("trend_up_day")
    if rng / em >= 1 and loc <= 0.1:
        labels.append("trend_down_day")
    if low_sigma <= -1 and close_sigma - low_sigma >= 1:
        labels.append("reversal_from_low")
    if high_sigma >= 1 and high_sigma - close_sigma >= 1:
        labels.append("reversal_from_high")
    if not any(l.startswith(("trend", "reversal")) for l in labels):
        labels.append("range_day" if rng / em < 0.8 else "two_sided_day")
    return dict(open=o, high=hi, low=lo, close=c, prior_close=prior_close, prior_vix=prior_vix,
                expected_move=round(em, 2), close_sigma=round(close_sigma, 3), range_sigma=round(rng / em, 3),
                close_location=round(loc, 3), last10_sigma=None if last10 is None else round(last10, 3),
                gap_sigma=round((o - prior_close) / em, 3), labels=labels)


# ---------------------------------------------------------------- report

def regime_summary(db, asof, regime_report):
    try:
        row = regime_lab.preopen_row(db, asof)
    except ValueError as exc:
        return dict(regime=None, reason=str(exc))
    rid = regime_lab.classify(row, regime_report)
    meta = next((r for r in regime_report["regimes"] if r["id"] == rid), None)
    return dict(regime=rid, asof=asof, rules=meta["rules"] if meta else None,
                features={k: round(row[k], 3) for k in ("vix", "vix_ts", "bb_pctb", "rv_iv", "trend50", "bb_width_pct")},
                history=dict(fit=meta["fit"], validation=meta["validation"]) if meta else None)


def build_report(db, label):
    daily_spx, daily_vix = truth_db.daily(db, "SPX"), truth_db.daily(db, "VIX")
    prior_days = [d for d in sorted(daily_spx) if d < label]
    if not prior_days:
        raise ValueError("no prior SPX close in the truth database")
    prev = prior_days[-1]
    bars = truth_db.intraday(db, "SPX", "5m", start=label, end=label, session="rth")
    if not bars:
        raise ValueError(f"no final SPX 5-minute bars for {label}; run qte truth collect after 16:15 ET")
    regime_report = json.loads(REGIME_REPORT.read_text())
    outcome = day_outcome(bars, daily_spx[prev]["close"], daily_vix[prev]["close"])
    today = regime_summary(db, prev, regime_report)
    if today.get("history"):
        typical = today["history"]["validation"]["range_sigma"]
        today["range_vs_regime_mean"] = round(outcome["range_sigma"] / typical, 2) if typical else None
    tomorrow = regime_summary(db, label, regime_report) if label in daily_spx else dict(
        regime=None, reason="today's final daily close not yet in the truth database")
    orb_today = dict(entry=orb_forward.today_record(orb_forward.ENTRIES, label),
                     exit=orb_forward.today_record(orb_forward.EXITS, label)) if orb_forward.ENTRIES.exists() else {}
    close_today = dict(entry=orb_forward.today_record(CLOSE_ENTRIES, label) if CLOSE_ENTRIES.exists() else None,
                       settle=orb_forward.today_record(CLOSE_SETTLES, label) if CLOSE_SETTLES.exists() else None)
    return dict(schema="qte-daily-report-v1", date=label, generated_utc=datetime.now(timezone.utc).isoformat(),
                session=outcome, regime_today=today, regime_tomorrow=tomorrow,
                orb_trial=dict(today=orb_today, running=orb_forward.status()),
                close_study=dict(today=close_today, running=close_status()))


def render(rep):
    s, t, n = rep["session"], rep["regime_today"], rep["regime_tomorrow"]
    lines = [f"# QTE daily report: {rep['date']}", "",
             f"**What the day was:** {', '.join(s['labels'])}. SPX {s['open']:.2f} → {s['close']:.2f} "
             f"(close {s['close_sigma']:+.2f}σ, range {s['range_sigma']:.2f}σ, gap {s['gap_sigma']:+.2f}σ, "
             f"last 10 min {s['last10_sigma']:+.2f}σ). 1σ = {s['expected_move']} pts from prior VIX {s['prior_vix']}.", "",
             f"**Today's pre-open regime:** {t.get('regime')}"
             + (f", range was {t['range_vs_regime_mean']}× that regime's typical day." if t.get("range_vs_regime_mean") else "."),
             f"**Tomorrow's pre-open regime:** {n.get('regime') or n.get('reason')}"
             + (f" (typical range {n['history']['validation']['range_sigma']}σ, closes beyond 2σ "
                f"{n['history']['validation']['close_beyond_2sigma']:.1%})" if n.get("history") else ""), ""]
    orb = rep["orb_trial"]
    e, x = (orb.get("today") or {}).get("entry"), (orb.get("today") or {}).get("exit")
    lines.append("## Early-ORB paper trial")
    lines.append(f"Today: {e['status'] if e else 'not run'}" + (
        f", {e['contract']['symbol']} paid {e['paid']}, exit {x['reason']} at {x['sold']}, P&L ${x['pnl_usd']}" if e and x else ""))
    r = orb["running"]
    lines += [f"Running: {r.get('trades', 0)} trades, total ${r.get('total_pnl_usd', 0)}, decision {r.get('decision')}.", ""]
    cs = rep["close_study"]
    ce, cx = cs["today"]["entry"], cs["today"]["settle"]
    lines.append("## 15:50 close study")
    if ce and cx:
        lines.append(f"Today: ATM {ce['strike']} straddle ask {ce['straddle_ask']}, implied move {cx['implied_abs_move']} "
                     f"vs realized {cx['realized_abs_move']} pts; straddle P&L ${cx['straddle_pnl_usd']}, "
                     f"momentum leg P&L ${cx['momentum_pnl_usd']}.")
    else:
        lines.append(f"Today: {ce['status'] if ce else 'not captured'}.")
    lines.append(f"Running: {json.dumps(cs['running'])}")
    return "\n".join(lines) + "\n"


def write_report(db, label):
    rep = build_report(db, label)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{label}.json").write_text(json.dumps(rep, indent=1, default=str))
    (REPORTS / f"{label}.md").write_text(render(rep))
    return rep


# ---------------------------------------------------------------- the daily loop

def run_day(key, secret, *, now_fn=time.time_ns, sleep=time.sleep):
    from qte.brokers.schwab_capture import capture_read
    day, _ = orb_forward.session(now_fn())
    if day.open_ns is None:
        print("Market closed today.")
        return
    label, done = day.trading_date, set()

    def snapshot(name):
        now = now_fn()
        stamp = datetime.fromtimestamp(now / 1e9, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = truth_db.TRUTH_ROOT / "captures" / "schwab" / f"{name}-{stamp}"
        orb_forward.capture(directory, orb_forward._client(), now, label)
        return orb_forward.load_capture(directory), directory, now

    while True:
        orb_forward._refresh_if_needed(key, secret)
        now = now_fn()
        if "orb" not in done:
            result = orb_forward.cycle(now, capture_fn=lambda d, n, l: orb_forward.capture(d, orb_forward._client(), n, l))
            print(datetime.now(truth_db.NY).strftime("%H:%M:%S"), "orb", result.get("status"), flush=True)
            if result.get("status") in ("finished", "exited", "range_too_wide", "no_range", "no_signal", "missed", "no_quote"):
                done.add("orb")
        if "close_entry" not in done and now >= day.close_ns - 11 * MINUTE:
            snap, directory, at = snapshot("close")
            body = dict(close_entry(snap, day, at), date=label, capture=str(directory))
            forward_paper.append_session(CLOSE_ENTRIES, "qte-close-entry-v1", body)
            print("close capture", body["status"], flush=True)
            done.add("close_entry")
        if "close_settle" not in done and now >= day.close_ns + 1 * MINUTE:
            entry = orb_forward.today_record(CLOSE_ENTRIES, label) if CLOSE_ENTRIES.exists() else None
            if entry and entry["status"] == "captured":
                snap, directory, at = snapshot("settle")
                last = orb_forward.candles(snap["spx-5m"], at).get(day.close_ns - 5 * MINUTE)
                if last:
                    body = dict(close_settle(entry, last[3]), date=label, entry_sha256=entry["sha256"], capture=str(directory))
                    forward_paper.append_session(CLOSE_SETTLES, "qte-close-settle-v1", body)
                    done.add("close_settle")
            else:
                done.add("close_settle")
        if now >= day.close_ns + 20 * MINUTE:
            with closing(truth_db.connect()) as db:
                stamp = datetime.fromtimestamp(now / 1e9, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                truth_db.collect(db, Path.cwd(), truth_db.DEFAULT_CONTRACT, truth_db.CAPTURES / "schwab" / stamp)
                truth_db.derive_all(db)
                rep = write_report(db, label)
            print(render(rep))
            return
        sleep(300 - time.time() % 300 + 20)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qte day", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "report"))
    parser.add_argument("--date", help="Trading date for `report` (default: today, New York)")
    args = parser.parse_args(argv)
    if args.command == "report":
        label = args.date or datetime.now(truth_db.NY).date().isoformat()
        with closing(truth_db.connect()) as db:
            print(render(write_report(db, label)))
        return 0
    if not sys.stdin.isatty():
        parser.error("run needs an interactive terminal for the private key/secret prompt")
    key = getpass.getpass("Schwab App Key (hidden): ").strip()
    secret = getpass.getpass("Schwab App Secret (hidden): ").strip()
    try:
        run_day(key, secret)
    except KeyboardInterrupt:
        return 130
    finally:
        key = secret = None
    return 0


if __name__ == "__main__":
    sys.exit(main())
