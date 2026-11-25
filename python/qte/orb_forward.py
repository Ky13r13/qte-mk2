"""Forward paper trial: early opening-range breakout with BTO 0DTE SPXW calls/puts.

Rules are pre-registered in docs/strategies/orb-bto-options.md ("Forward paper trial").
Every cycle saves a real Schwab snapshot and fills paper trades at real quotes
(buy at the ask, sell at the bid). No orders are ever sent.

    qte orb run      # start once per session; asks for the app key/secret privately,
                     # keeps them in memory only, refreshes the token, stops after the close
    qte orb cycle    # one pass with the stored token (for scheduling while a token is valid)
    qte orb status   # trades so far and the pre-registered decision rule
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import getpass
import json
import math
from pathlib import Path
from statistics import fmean, median
import sys
import time

from qte import forward_paper, truth_db
from qte.adapters.exchange_sessions import import_xnys
from qte.gamma_surface import _black76

MINUTE = 60_000_000_000
FORWARD = truth_db.TRUTH_ROOT / "forward" / "orb"
ENTRIES = FORWARD / "entries.ndjson"
EXITS = FORWARD / "exits.ndjson"
ENTRY_SCHEMA = "qte-orb-forward-entry-v1"
EXIT_SCHEMA = "qte-orb-forward-exit-v1"
RULE = dict(range_bars=2, max_range_em=0.40, volume_ratio=2.0, lookback=20, last_slot=24,
            fresh_minutes=10, time_stop_minutes=60, model_iv_mult=0.60, model_half_spread=0.05)
FEE, MULT = 1.25, 100


# ---------------------------------------------------------------- pure decision logic

def session(asof):
    day = datetime.fromtimestamp(asof // 10**9, truth_db.NY).date()
    schedule = import_xnys((day - timedelta(days=40)).isoformat(), day.isoformat())
    today = schedule.day(day.isoformat())
    return today, [d for d in schedule.days if d.open_ns is not None and d.trading_date < today.trading_date]


def candles(record, asof):
    """Completed candles as {start_ns: (open, high, low, close, volume)}."""
    out = {}
    for row in record["response"].get("candles", []):
        start = row["datetime"] * 1_000_000
        if start + 5 * MINUTE > asof:
            continue
        values, reason = truth_db._check(row)
        if reason:
            raise ValueError(f"invalid candle: {reason}")
        out[start] = values[:5]
    return out


def prior_daily(record, today_label):
    rows = sorted((datetime.fromtimestamp(r["datetime"] / 1000, truth_db.NY).date().isoformat(), r["close"])
                  for r in record["response"].get("candles", []))
    earlier = [c for d, c in rows if d < today_label]
    return earlier[-1] if earlier else None


def evaluate(day, previous_days, spx, spy, prior_close, prior_vix, now, rule=RULE):
    """Breakout decision from completed bars only. Returns a dict with a `status`."""
    em = prior_close * prior_vix / 100 / math.sqrt(252)
    opening = [spx.get(day.open_ns + k * 5 * MINUTE) for k in range(rule["range_bars"])]
    deadline_end = day.open_ns + (rule["last_slot"] + 1) * 5 * MINUTE
    if None in opening:
        return dict(status="waiting" if now < day.open_ns + (rule["range_bars"] + 1) * 5 * MINUTE else "no_range", em=em)
    hi, lo = max(b[1] for b in opening), min(b[2] for b in opening)
    base = dict(em=em, range_high=hi, range_low=lo, midpoint=(hi + lo) / 2)
    if hi - lo >= rule["max_range_em"] * em:
        return dict(base, status="range_too_wide")
    for slot in range(rule["range_bars"], rule["last_slot"] + 1):
        start = day.open_ns + slot * 5 * MINUTE
        bar = spx.get(start)
        if bar is None:
            continue
        side = 1 if bar[3] > hi else -1 if bar[3] < lo else 0
        if not side:
            continue
        offset = slot * 5 * MINUTE
        history = [spy[d.open_ns + offset][4] for d in previous_days[-rule["lookback"]:]
                   if d.open_ns + offset in spy]
        today_volume = spy.get(start, (None,) * 5)[4]
        if len(history) < rule["lookback"] or today_volume is None or fmean(history) <= 0:
            continue
        ratio = today_volume / fmean(history)
        if ratio > rule["volume_ratio"]:
            return dict(base, status="signal", side=side, bar_start_ns=start, bar_end_ns=start + 5 * MINUTE,
                        bar_close=bar[3], volume_ratio=round(ratio, 3))
    return dict(base, status="no_signal" if now >= deadline_end else "no_signal_yet")


def pick_contract(chain_record, day_label, spot, side):
    field = "callExpDateMap" if side > 0 else "putExpDateMap"
    target = 5 * round(spot / 5)
    for expiry, strikes in chain_record["response"].get(field, {}).items():
        if expiry.split(":")[0] != day_label:
            continue
        for strike, rows in strikes.items():
            if float(strike) == target and rows:
                row = rows[0]
                if row.get("settlementType") == "P" and row.get("multiplier") == 100:
                    return dict(symbol=row["symbol"], strike=target, right="C" if side > 0 else "P",
                                bid=row.get("bid"), ask=row.get("ask"), volatility=row.get("volatility"))
    return None


def exit_reason(entry, spx, day, now, rule=RULE):
    """First completed bar after entry that triggers an exit, or None."""
    start = entry["entry_ns"] - (entry["entry_ns"] - day.open_ns) % (5 * MINUTE)
    for s in sorted(k for k in spx if k >= start):
        end, close = s + 5 * MINUTE, spx[s][3]
        if end > now:
            break
        if (entry["side"] > 0 and close < entry["midpoint"]) or (entry["side"] < 0 and close > entry["midpoint"]):
            return "midpoint_stop", end
        if end - entry["entry_ns"] >= rule["time_stop_minutes"] * MINUTE:
            return "time_stop", end
        if end >= day.close_ns - 5 * MINUTE:
            return "close", end
    return None


def modeled(spot, strike, right, minutes_left, prior_vix, buy, rule=RULE):
    if minutes_left <= 0:
        return max((spot - strike) if right == "C" else (strike - spot), 0.0)
    iv = prior_vix / 100 * rule["model_iv_mult"]
    mid = _black76(spot, strike, iv, minutes_left / (390 * 252), 0.0, "call" if right == "C" else "put")["price"]
    return round(mid + rule["model_half_spread"] if buy else max(mid - rule["model_half_spread"], 0.0), 2)


# ---------------------------------------------------------------- one cycle

def today_record(path, label):
    return next((r for r in forward_paper.read_ledger(path, recover_partial=False) if r["date"] == label), None)


def capture(directory, client, now, day_label):
    from qte.brokers.schwab_capture import capture_read
    directory.mkdir(parents=True, exist_ok=False)
    end = now // 1_000_000

    def save(name, operation, **query):
        capture_read(client, operation, directory / f"{name}.json", path_parameters={}, query_parameters=query)
    save("spx-5m", "priceHistory", symbol="$SPX", periodType="day", frequencyType="minute", frequency=5,
         startDate=end - 3 * 86_400_000, endDate=end, needExtendedHoursData=False)
    save("spy-5m", "priceHistory", symbol="SPY", periodType="day", frequencyType="minute", frequency=5,
         startDate=end - 45 * 86_400_000, endDate=end, needExtendedHoursData=False)
    for sym in ("$SPX", "$VIX"):
        save(sym.strip("$").lower() + "-1d", "priceHistory", symbol=sym, periodType="month", frequencyType="daily",
             frequency=1, startDate=end - 20 * 86_400_000, endDate=end, needExtendedHoursData=False)
    save("spx-chain-0dte", "optionChain", symbol="$SPX", contractType="ALL", includeUnderlyingQuote=True,
         strikeCount=40, fromDate=day_label, toDate=day_label)


def load_capture(directory):
    return {p.stem: json.loads(p.read_text()) for p in Path(directory).glob("*.json")}


def cycle(now, *, capture_fn, entries=ENTRIES, exits=EXITS, rule=RULE):
    """Run one decision pass. `capture_fn(directory, now, day_label)` writes the snapshot files."""
    day, previous = session(now)
    if day.open_ns is None:
        return dict(status="market_closed")
    label = day.trading_date
    entry, done = today_record(entries, label), today_record(exits, label)
    if done:
        return dict(status="finished", date=label)
    if now < day.open_ns + (rule["range_bars"] + 1) * 5 * MINUTE:
        return dict(status="waiting_for_range", date=label)
    if entry and entry["status"] != "entered":
        return dict(status=entry["status"], date=label)
    stamp = datetime.fromtimestamp(now / 1e9, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = truth_db.TRUTH_ROOT / "captures" / "schwab" / f"orb-{stamp}"
    capture_fn(directory, now, label)
    snap = load_capture(directory)
    spx, spy = candles(snap["spx-5m"], now), candles(snap["spy-5m"], now)
    spot = snap["spx-chain-0dte"]["response"].get("underlyingPrice")
    prior_close, prior_vix = prior_daily(snap["spx-1d"], label), prior_daily(snap["vix-1d"], label)
    if prior_close is None or prior_vix is None or spot is None:
        return dict(status="blocked", reason="missing prior close/VIX or live spot", capture=str(directory))

    if entry is None:
        decision = evaluate(day, previous, spx, spy, prior_close, prior_vix, now, rule)
        if decision["status"] in ("waiting", "no_signal_yet"):
            return dict(decision, date=label, capture=str(directory))
        if decision["status"] == "signal":
            late = now - decision["bar_end_ns"] > rule["fresh_minutes"] * MINUTE
            contract = None if late else pick_contract(snap["spx-chain-0dte"], label, spot, decision["side"])
            if late or contract is None or not contract["ask"]:
                body = dict(decision, date=label, status="missed" if late else "no_quote", capture=str(directory))
            else:
                left = (day.close_ns - now) / MINUTE
                body = dict(decision, date=label, status="entered", entry_ns=now, spot=spot, contract=contract,
                            paid=contract["ask"], prior_vix=prior_vix,
                            modeled_paid=modeled(spot, contract["strike"], contract["right"], left, prior_vix, True, rule),
                            capture=str(directory))
        else:
            body = dict(decision, date=label, capture=str(directory))
        record, _ = forward_paper.append_session(entries, ENTRY_SCHEMA, body)
        return record

    # an open position: look for an exit
    found = exit_reason(entry, spx, day, now, rule)
    after_close = now >= day.close_ns
    if not found and not after_close:
        return dict(status="holding", date=label, contract=entry["contract"]["symbol"], capture=str(directory))
    reason, bar_end = found if found else ("expired", day.close_ns)
    contract = pick_contract(snap["spx-chain-0dte"], label, entry["contract"]["strike"], entry["side"])
    if after_close or contract is None or contract.get("bid") is None:
        last = spx.get(day.close_ns - 5 * MINUTE)
        if last is None:
            return dict(status="awaiting_close_bar", date=label)
        settle = last[3]
        sold = max((settle - entry["contract"]["strike"]) * entry["side"], 0.0)
        reason = reason if not after_close else "expired_intrinsic"
    else:
        sold = contract["bid"]
    left = max((day.close_ns - now) / MINUTE, 0)
    body = dict(date=label, status="exited", reason=reason, trigger_bar_end_ns=bar_end, exit_ns=now, spot=spot,
                contract=entry["contract"]["symbol"], paid=entry["paid"], sold=sold,
                pnl_usd=round((sold - entry["paid"]) * MULT - 2 * FEE, 2),
                modeled_sold=modeled(spot, entry["contract"]["strike"], entry["contract"]["right"], left,
                                     entry["prior_vix"], False, rule),
                entry_sha256=entry["sha256"], capture=str(directory))
    record, _ = forward_paper.append_session(exits, EXIT_SCHEMA, body)
    return record


def status(entries=ENTRIES, exits=EXITS):
    ins = forward_paper.read_ledger(entries, recover_partial=False) if entries.exists() else []
    outs = {r["date"]: r for r in (forward_paper.read_ledger(exits, recover_partial=False) if exits.exists() else [])}
    trades = [(e, outs[e["date"]]) for e in ins if e["status"] == "entered" and e["date"] in outs]
    pnl = [x["pnl_usd"] for _, x in trades]
    gaps = [e["paid"] - e["modeled_paid"] for e, _ in trades]
    counts = {}
    for e in ins:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    decided = len(trades) >= 30
    return dict(sessions=len(ins), outcomes=counts, trades=len(trades),
                mean_pnl_usd=round(fmean(pnl), 2) if pnl else None, total_pnl_usd=round(sum(pnl), 2),
                win_rate=round(sum(p > 0 for p in pnl) / len(pnl), 3) if pnl else None,
                median_entry_gap_vs_model=round(median(gaps), 2) if gaps else None,
                decision=("pending" if not decided else
                          "continue" if fmean(pnl) > 0 and abs(median(gaps)) <= 0.5 else "reject"),
                rule="After 30 trades: continue only if mean quoted P&L > $0 and |median entry gap| <= $0.50")


# ---------------------------------------------------------------- CLI

def _client():
    from qte.brokers.schwab_auth import TokenStore, market_client
    store = TokenStore(Path.cwd())
    with store.lock():
        return market_client(store.load(), json.loads(truth_db.DEFAULT_CONTRACT.read_text()))


def _refresh_if_needed(key, secret, margin=300):
    from qte.brokers.schwab_auth import TokenStore, exchange
    store = TokenStore(Path.cwd())
    with store.lock():
        doc = store.load()
        if doc["expires_at"] - time.time() < margin:
            store.save(exchange(key, secret, refresh_token=doc["token"]["refresh_token"]))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qte orb", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "cycle", "status"))
    args = parser.parse_args(argv)
    if args.command == "status":
        print(json.dumps(status(), indent=2))
        return 0

    def live_capture(directory, now, label):
        capture(directory, _client(), now, label)

    if args.command == "cycle":
        print(json.dumps(cycle(time.time_ns(), capture_fn=live_capture), indent=2, default=str))
        return 0
    if not sys.stdin.isatty():
        parser.error("run needs an interactive terminal for the private key/secret prompt")
    key = getpass.getpass("Schwab App Key (hidden): ").strip()
    secret = getpass.getpass("Schwab App Secret (hidden): ").strip()
    try:
        while True:
            _refresh_if_needed(key, secret)
            result = cycle(time.time_ns(), capture_fn=live_capture)
            print(datetime.now(truth_db.NY).strftime("%H:%M:%S"), json.dumps(
                {k: result.get(k) for k in ("status", "side", "contract", "paid", "sold", "pnl_usd", "reason")}), flush=True)
            if result.get("status") in ("finished", "exited", "market_closed", "range_too_wide", "no_range",
                                        "no_signal", "missed", "no_quote"):
                break
            now = time.time()
            time.sleep(300 - now % 300 + 20)  # 20 s after each 5-minute boundary, once the bar is complete
    except KeyboardInterrupt:
        return 130
    finally:
        key = secret = None
    return 0


if __name__ == "__main__":
    sys.exit(main())
