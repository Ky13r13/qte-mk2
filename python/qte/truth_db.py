"""QTE market-data truth database: every source capture, every bar, every disagreement.

One SQLite file (``data/truth/qte-truth.sqlite3`` by default, an ignored path)
accumulates bars from all providers over time. Rules:

- Raw captures stay on disk and are identified by SHA-256. A capture is
  ingested at most once; re-ingesting is a no-op.
- Bars are keyed by (instrument, interval, start, provider). Providers are never
  merged into each other; cross-provider disagreement is queryable.
- A bar whose session had not finished when the capture was received is
  ``provisional``. A later final observation replaces it and the change is
  logged in ``revisions``. A final bar is never overwritten; a differing later
  final observation is logged in ``revisions`` with ``kind='conflict'``.
- Rows with inconsistent OHLC are kept out of ``bars`` and recorded in
  ``rejected`` with the reason. Rows with open=high=low=close are kept but
  marked ``close_only`` so range/excursion studies can exclude them.

Timestamps: intraday ``start_ns`` is the bar's UTC start. Daily bars are keyed by
trading date: ``session_date`` is the New York date of the provider stamp and
``start_ns`` is New York midnight of that date (Schwab stamps daily candles at
midnight US/Central, and has re-stamped the newest candle at midnight US/Eastern).
Prices are provider-native and unadjusted. Schwab ``/ES`` is the provider's
continuous front-month series, unadjusted across rolls.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, time as dtime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
TRUTH_ROOT = Path("data/truth")
DEFAULT_DB = TRUTH_ROOT / "qte-truth.sqlite3"
CAPTURES = TRUTH_ROOT / "captures"
DEFAULT_CONTRACT = TRUTH_ROOT / "contracts" / "schwab-market-data.json"
NATIVE_INTERVALS = ("1m", "5m", "10m", "15m", "30m", "1d", "1w", "1mo")
DERIVED_INTERVALS = {"1h": 3600, "4h": 14400}
SCHEMA_VERSION = 1

# Provider symbol -> canonical QTE instrument.
SCHWAB_SYMBOLS = {"$SPX": "SPX", "$VIX": "VIX", "$VIX3M": "VIX3M", "$VIX9D": "VIX9D", "$NDX": "NDX",
                  "/ES": "ES", "/MES": "MES", "/NQ": "NQ"}
FUTURES = {"ES", "MES", "NQ"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS captures (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  path TEXT NOT NULL,
  instrument TEXT NOT NULL,
  interval TEXT NOT NULL,
  request_json TEXT NOT NULL,
  received_utc_ns INTEGER,
  ingested_utc_ns INTEGER NOT NULL,
  rows_total INTEGER NOT NULL,
  rows_inserted INTEGER NOT NULL,
  rows_rejected INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bars (
  instrument TEXT NOT NULL,
  interval TEXT NOT NULL,
  start_ns INTEGER NOT NULL,
  provider TEXT NOT NULL,
  session_date TEXT NOT NULL,
  session TEXT NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume REAL,
  quality TEXT NOT NULL,
  capture_id INTEGER NOT NULL REFERENCES captures(id),
  PRIMARY KEY (instrument, interval, start_ns, provider)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS bars_by_date ON bars(instrument, interval, session_date);
CREATE TABLE IF NOT EXISTS revisions (
  instrument TEXT NOT NULL, interval TEXT NOT NULL, start_ns INTEGER NOT NULL, provider TEXT NOT NULL,
  kind TEXT NOT NULL,
  old_json TEXT NOT NULL, new_json TEXT NOT NULL,
  old_capture_id INTEGER NOT NULL, new_capture_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rejected (
  capture_id INTEGER NOT NULL REFERENCES captures(id),
  row_json TEXT NOT NULL,
  reason TEXT NOT NULL
);
"""


class TruthError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    found = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if found is None:
        db.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        db.commit()
    elif int(found[0]) != SCHEMA_VERSION:
        raise TruthError(f"Truth database schema {found[0]} differs from supported {SCHEMA_VERSION}")
    return db


def _interval(params):
    kind, n = params.get("frequencyType"), params.get("frequency")
    if kind == "minute" and n in (1, 5, 10, 15, 30):
        return f"{n}m", n * 60
    if kind == "daily" and n == 1:
        return "1d", None
    if kind == "weekly" and n == 1:
        return "1w", None
    if kind == "monthly" and n == 1:
        return "1mo", None
    raise TruthError(f"Unsupported Schwab interval {kind}/{n}")


def _period_end_ns(instrument, interval, session_date):
    """When a daily/weekly/monthly candle starting on ``session_date`` is final."""
    day = datetime.fromisoformat(session_date).date()
    if interval == "1w":
        day = day + timedelta(days=6)
    elif interval == "1mo":
        nxt = day.replace(day=28) + timedelta(days=4)
        day = nxt - timedelta(days=nxt.day)
    return _session_end_ns(instrument, day.isoformat())


def _session_end_ns(instrument, session_date):
    """Conservative time after which a daily bar for ``session_date`` is final."""
    day = datetime.fromisoformat(session_date).date()
    local = datetime.combine(day, dtime(17, 15) if instrument in FUTURES else dtime(16, 15), NY)
    return int(local.timestamp()) * 1_000_000_000


def _daily_key_ns(session_date):
    """Canonical start for daily bars: New York midnight of the trading date."""
    day = datetime.fromisoformat(session_date).date()
    return int(datetime.combine(day, dtime(0), NY).timestamp()) * 1_000_000_000


def _check(row):
    names = ("open", "high", "low", "close")
    try:
        o, h, l, c = (row[k] for k in names)
    except KeyError:
        return None, "missing_ohlc"
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (o, h, l, c)):
        return None, "nonpositive_or_nonfinite"
    if not l <= min(o, c) or max(o, c) > h:
        return None, "inconsistent_ohlc"
    v = row.get("volume")
    if v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0):
        return None, "invalid_volume"
    quality = "close_only" if o == h == l == c else "ok"
    return (float(o), float(h), float(l), float(c), None if v is None else float(v), quality), None


def read_schwab_capture(path):
    """Validate a ``schwab_capture`` priceHistory file. Returns (sha, record) or None if not one."""
    raw = Path(path).read_bytes()
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("provider") != "schwab":
        return None
    request = record.get("request") or {}
    if request.get("operation_id") != "priceHistory":
        return None
    response = record.get("response")
    if sha256(_canonical(response)).hexdigest() != record.get("response_canonical_json_sha256"):
        raise TruthError(f"{path}: response checksum mismatch")
    return sha256(raw).hexdigest(), record


def ingest_schwab(db, path, *, clock=time.time_ns):
    """Ingest one Schwab priceHistory capture. Returns a summary dict (``skipped`` if already present)."""
    found = read_schwab_capture(path)
    if found is None:
        return None
    digest, record = found
    if db.execute("SELECT 1 FROM captures WHERE sha256=?", (digest,)).fetchone():
        return dict(path=str(path), status="already_ingested")
    params = record["request"]["query_parameters"]
    response = record["response"]
    symbol = params["symbol"]
    returned = response.get("symbol")
    instrument = SCHWAB_SYMBOLS.get(symbol, symbol)
    # Futures roots resolve to the provider's current contract (e.g. /ES -> /ESZ26).
    if returned not in (None, symbol) and not (instrument in FUTURES and str(returned).startswith(symbol)):
        raise TruthError(f"{path}: response symbol differs from request")
    interval, seconds = _interval(params)
    extended = params.get("needExtendedHoursData") is True
    session = "eth" if instrument in FUTURES and seconds else ("ext" if extended else "rth")
    received = record.get("response_received_utc_ns")
    candles = response.get("candles") or []
    with db:
        cur = db.execute(
            "INSERT INTO captures(sha256, provider, path, instrument, interval, request_json, received_utc_ns,"
            " ingested_utc_ns, rows_total, rows_inserted, rows_rejected) VALUES (?,?,?,?,?,?,?,?,?,0,0)",
            (digest, "schwab", str(path), instrument, interval,
             _canonical(dict(record["request"], returned_symbol=returned)).decode(),
             received, clock(), len(candles)))
        capture_id = cur.lastrowid
        inserted = rejected = 0
        # A timestamp repeated with different values inside one response is
        # ambiguous: reject every copy. Identical repeats collapse to one.
        seen, ambiguous = {}, set()
        for row in candles:
            stamp = row.get("datetime")
            if stamp in seen and seen[stamp] != row:
                ambiguous.add(stamp)
            seen.setdefault(stamp, row)
        emitted = set()
        for row in candles:
            stamp = row.get("datetime")
            if stamp in ambiguous:
                db.execute("INSERT INTO rejected VALUES (?,?,?)",
                           (capture_id, _canonical(row).decode(), "duplicate_timestamp_conflict"))
                rejected += 1
                continue
            if stamp in emitted:
                continue
            emitted.add(stamp)
            if type(stamp) is not int:
                db.execute("INSERT INTO rejected VALUES (?,?,?)", (capture_id, _canonical(row).decode(), "bad_timestamp"))
                rejected += 1
                continue
            start_ns = stamp * 1_000_000
            values, reason = _check(row)
            if reason:
                db.execute("INSERT INTO rejected VALUES (?,?,?)", (capture_id, _canonical(row).decode(), reason))
                rejected += 1
                continue
            if seconds:
                session_date = datetime.fromtimestamp(stamp / 1000, NY).date().isoformat()
                complete = received is None or start_ns + seconds * 1_000_000_000 <= received
            else:
                # Schwab stamps daily candles at midnight US/Central but has also re-stamped the
                # newest candle at midnight US/Eastern (2026-11-23). Both fall on the same New York
                # date. Key daily bars by that trading date so a re-stamp cannot add a phantom session.
                session_date = datetime.fromtimestamp(stamp / 1000, NY).date().isoformat()
                start_ns = _daily_key_ns(session_date)
                complete = received is None or _period_end_ns(instrument, interval, session_date) <= received
            quality = values[5] if complete else "provisional"
            inserted += _upsert(db, (instrument, interval, start_ns, "schwab", session_date, session,
                                     *values[:5], quality, capture_id))
        db.execute("UPDATE captures SET rows_inserted=?, rows_rejected=? WHERE id=?", (inserted, rejected, capture_id))
    return dict(path=str(path), status="ingested", instrument=instrument, interval=interval,
                rows=len(candles), inserted=inserted, rejected=rejected)


def _upsert(db, row):
    key = row[:4]
    old = db.execute("SELECT open, high, low, close, volume, quality, capture_id FROM bars"
                     " WHERE instrument=? AND interval=? AND start_ns=? AND provider=?", key).fetchone()
    if old is None:
        db.execute("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        return 1
    new_values, new_quality, new_capture = row[6:11], row[11], row[12]
    if tuple(old[:5]) == tuple(new_values):
        if old[5] == "provisional" and new_quality != "provisional":
            db.execute("UPDATE bars SET quality=?, capture_id=? WHERE instrument=? AND interval=? AND start_ns=?"
                       " AND provider=?", (new_quality, new_capture, *key))
        return 0
    names = ("open", "high", "low", "close", "volume")
    old_json = json.dumps(dict(zip(names, old[:5]), quality=old[5]), sort_keys=True)
    new_json = json.dumps(dict(zip(names, new_values), quality=new_quality), sort_keys=True)
    if old[5] == "provisional":
        db.execute("UPDATE bars SET open=?, high=?, low=?, close=?, volume=?, quality=?, capture_id=?"
                   " WHERE instrument=? AND interval=? AND start_ns=? AND provider=?",
                   (*new_values, new_quality, new_capture, *key))
        kind = "finalized" if new_quality != "provisional" else "provisional_update"
    else:
        kind = "conflict"  # final bars are immutable; keep the first and log the disagreement
    db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?)", (*key, kind, old_json, new_json, old[6], new_capture))
    return 0


EXCLUDED_PARTS = ("test", "pytest", "fixture", "demo", "synthetic")


def _excluded(path, root):
    parts = path.relative_to(root).parts[:-1] if path.is_relative_to(root) else path.parts[:-1]
    # Dot-prefixed folders hold incomplete staging (e.g. .staging-*) or tool state.
    return any(part.startswith(".") or any(token in part.lower() for token in EXCLUDED_PARTS) for part in parts)


ALPACA_INTERVALS = {"1Min": ("1m", 60), "5Min": ("5m", 300), "15Min": ("15m", 900), "30Min": ("30m", 1800),
                    "1Hour": ("1h", 3600), "1Day": ("1d", None)}


def _rfc3339_ns(text):
    from qte.adapters.common import parse_rfc3339_utc_ns
    return parse_rfc3339_utc_ns(text)


def _xnys_hours(first_ns, last_ns):
    """Trading date -> (open_ns, close_ns) for XNYS regular sessions covering the range."""
    from qte.adapters.exchange_sessions import import_xnys
    first = datetime.fromtimestamp(first_ns / 1e9, NY).date().isoformat()
    last = datetime.fromtimestamp(last_ns / 1e9, NY).date().isoformat()
    schedule = import_xnys(first, last)
    return {d.trading_date: (d.open_ns, d.close_ns) for d in schedule.days if d.open_ns is not None}


def ingest_alpaca(db, bars_path, *, clock=time.time_ns):
    """Ingest one ``alpaca_download`` cache directory (bars.json + download.json), checksum-verified."""
    bars_path = Path(bars_path)
    meta_path = bars_path.with_name("download.json")
    if bars_path.name != "bars.json" or not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text())
    raw = bars_path.read_bytes()
    digest = sha256(raw).hexdigest()
    if meta.get("sha256", {}).get("bars.json") != digest:
        raise TruthError(f"{bars_path}: checksum differs from download.json")
    if db.execute("SELECT 1 FROM captures WHERE sha256=?", (digest,)).fetchone():
        return dict(path=str(bars_path), status="already_ingested")
    doc = json.loads(raw)
    envelope = doc.get("_qte") or {}
    if envelope.get("adjustment") != "raw" or envelope.get("timeframe") not in ALPACA_INTERVALS:
        raise TruthError(f"{bars_path}: unsupported Alpaca envelope")
    interval, seconds = ALPACA_INTERVALS[envelope["timeframe"]]
    received = meta.get("fetched_at_ns")
    stamps = [_rfc3339_ns(r["t"]) for rows in doc["bars"].values() for r in rows if isinstance(r, dict) and "t" in r]
    hours = _xnys_hours(min(stamps), max(stamps)) if stamps else {}
    summaries = []
    with db:
        for symbol, rows in sorted(doc["bars"].items()):
            cur = db.execute(
                "INSERT INTO captures(sha256, provider, path, instrument, interval, request_json, received_utc_ns,"
                " ingested_utc_ns, rows_total, rows_inserted, rows_rejected) VALUES (?,?,?,?,?,?,?,?,?,0,0)",
                (digest if len(doc["bars"]) == 1 else f"{digest}:{symbol}", "alpaca", str(bars_path), symbol,
                 interval, _canonical(dict(meta["request"], feed=envelope.get("feed"))).decode(), received,
                 clock(), len(rows)))
            capture_id = cur.lastrowid
            inserted = rejected = 0
            for row in rows:
                values, reason = _check(dict(open=row.get("o"), high=row.get("h"), low=row.get("l"),
                                             close=row.get("c"), volume=row.get("v")))
                try:
                    start_ns = _rfc3339_ns(row.get("t"))
                except (TypeError, ValueError):
                    reason = "bad_timestamp"
                if reason:
                    db.execute("INSERT INTO rejected VALUES (?,?,?)", (capture_id, _canonical(row).decode(), reason))
                    rejected += 1
                    continue
                local = datetime.fromtimestamp(start_ns / 1e9, NY)
                if seconds:
                    # Regular session from the XNYS calendar: early closes and holidays respected.
                    opened, closed = hours.get(local.date().isoformat(), (None, None))
                    rth = opened is not None and opened <= start_ns and start_ns + seconds * 1_000_000_000 <= closed
                    session = "rth" if rth else "ext"
                    complete = received is None or start_ns + seconds * 1_000_000_000 <= received
                else:
                    session, complete = "rth", received is None or _session_end_ns(symbol, local.date().isoformat()) <= received
                    start_ns = _daily_key_ns(local.date().isoformat())
                quality = values[5] if complete else "provisional"
                inserted += _upsert(db, (symbol, interval, start_ns, "alpaca", local.date().isoformat(), session,
                                         *values[:5], quality, capture_id))
            db.execute("UPDATE captures SET rows_inserted=?, rows_rejected=? WHERE id=?", (inserted, rejected, capture_id))
            summaries.append(dict(path=str(bars_path), status="ingested", provider="alpaca", instrument=symbol,
                                  interval=interval, rows=len(rows), inserted=inserted, rejected=rejected))
    return summaries[0] if len(summaries) == 1 else dict(path=str(bars_path), status="ingested", parts=summaries)


def ingest_paths(db, paths):
    """Ingest files; directories are searched recursively, skipping test/fixture/demo folders."""
    results = []
    for root in paths:
        root = Path(root)
        files = (sorted(p for p in root.rglob("*.json") if p.is_file() and not _excluded(p, root))
                 if root.is_dir() else [root])
        for path in files:
            if path.stat().st_size > 512 * 1024 * 1024:
                continue
            try:
                result = ingest_alpaca(db, path) if path.name == "bars.json" else ingest_schwab(db, path)
            except (TruthError, KeyError, TypeError) as exc:
                result = dict(path=str(path), status="error", error=str(exc))
            if result is not None:
                results.append(result)
    return results


def coverage(db):
    rows = db.execute(
        "SELECT instrument, interval, provider, session, COUNT(*), MIN(session_date), MAX(session_date),"
        " COUNT(DISTINCT session_date), SUM(quality='provisional'), SUM(quality='close_only')"
        " FROM bars GROUP BY instrument, interval, provider, session ORDER BY instrument, interval").fetchall()
    keys = ("instrument", "interval", "provider", "session", "bars", "first", "last", "sessions",
            "provisional", "close_only")
    return [dict(zip(keys, r)) for r in rows]


def daily(db, instrument, *, provider="schwab", final_only=True, end=None):
    """Daily bars keyed by trading date, as dicts with date/open/high/low/close/volume/quality."""
    sql = ("SELECT session_date, open, high, low, close, volume, quality FROM bars"
           " WHERE instrument=? AND interval='1d' AND provider=?")
    if final_only:
        sql += " AND quality != 'provisional'"
    args = [instrument, provider]
    if end is not None:
        sql += " AND session_date <= ?"
        args.append(end)
    out = {}
    for d, o, h, l, c, v, q in db.execute(sql + " ORDER BY session_date", args):
        out[d] = dict(date=d, open=o, high=h, low=l, close=c, volume=v or 0, quality=q)
    return out


def intraday(db, instrument, interval, *, provider="schwab", start=None, end=None, session=None):
    sql = ("SELECT start_ns, session_date, open, high, low, close, volume, quality FROM bars"
           " WHERE instrument=? AND interval=? AND provider=? AND quality != 'provisional'")
    args = [instrument, interval, provider]
    if session:
        sql += " AND session = ?"
        args.append(session)
    if start:
        sql += " AND session_date >= ?"
        args.append(start)
    if end:
        sql += " AND session_date <= ?"
        args.append(end)
    keys = ("start_ns", "session_date", "open", "high", "low", "close", "volume", "quality")
    return [dict(zip(keys, r)) for r in db.execute(sql + " ORDER BY start_ns", args)]


# ------------------------------------------------------------------ collection

# The standing collection set. Intraday history rolls off at the provider
# (1m ~48 days, 5m-30m ~9 months), so frequent collection is what builds depth.
COLLECT = (
    [(sym, "minute", n) for sym in ("$SPX", "SPY", "/ES") for n in (1, 5, 10, 15, 30)]
    + [("$VIX", "minute", n) for n in (5, 10, 15, 30)]
    + [(sym, kind, 1) for sym in ("$SPX", "SPY", "/ES", "$VIX", "QQQ") for kind in ("daily", "weekly", "monthly")]
    + [("$VIX3M", "daily", 1), ("$VIX9D", "daily", 1)]
)
SUFFIX = {"minute": "m", "daily": "d", "weekly": "w", "monthly": "mo"}


def collect(db, repo, contract_path, out_dir, *, lookback_days=None, clock=time.time_ns):
    """Fetch the standing set from Schwab with the stored token and ingest each capture."""
    from qte.brokers.schwab_auth import TokenStore, market_client
    from qte.brokers.schwab_capture import capture_read
    store = TokenStore(Path(repo))
    with store.lock():
        client = market_client(store.load(), json.loads(Path(contract_path).read_text()))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    now_ms = clock() // 1_000_000
    results = []
    for symbol, kind, n in COLLECT:
        start = 946684800000 if kind == "minute" else 0
        if lookback_days:
            start = now_ms - lookback_days * 86_400_000
        params = dict(symbol=symbol, periodType="day" if kind == "minute" else "year", frequencyType=kind,
                      frequency=n, startDate=start, endDate=now_ms, needExtendedHoursData=False)
        name = f"{symbol.strip('$/').lower()}-{n}{SUFFIX[kind]}.json"
        try:
            capture_read(client, "priceHistory", out_dir / name, path_parameters={}, query_parameters=params)
            results.append(ingest_schwab(db, out_dir / name))
        except Exception as exc:  # one failing symbol must not lose the rest of the run
            results.append(dict(path=str(out_dir / name), status="error", error=type(exc).__name__))
    return results


# ------------------------------------------------------------------ derived intervals

def derive(db, instrument, provider, *, source="5m", targets=("1h", "4h")):
    """Build 1h/4h bars from complete 5-minute buckets. Derived bars are reproducible, so each
    run replaces the previous derived set for (instrument, target, provider); observed bars are
    never touched.

    Cash-session series (``rth``): buckets anchored at the XNYS open (09:30 ET), last bucket ends
    at the calendar close (early closes respected). Futures (``eth``): 1h buckets on ET clock
    hours, 4h buckets from 18:00 ET (CME trading day), excluding the 17:00-18:00 ET halt.
    A bucket missing any expected source bar is skipped, never filled.
    """
    step = {"5m": 300, "15m": 900, "30m": 1800}[source] * 1_000_000_000
    rows = db.execute("SELECT start_ns, session_date, open, high, low, close, volume FROM bars WHERE instrument=?"
                      " AND interval=? AND provider=? AND session IN ('rth', 'eth') AND quality != 'provisional'"
                      " ORDER BY start_ns", (instrument, source, provider)).fetchall()
    if not rows:
        return {}
    futures = instrument in FUTURES
    by_start = {r[0]: r for r in rows}
    hours = {} if futures else _xnys_hours(rows[0][0], rows[-1][0])
    digest = sha256(_canonical([instrument, provider, source, len(rows), rows[0][0], rows[-1][0],
                                math.fsum(r[5] for r in rows)])).hexdigest()
    summary = {}
    with db:
        found = db.execute("SELECT id FROM captures WHERE sha256=?", (digest,)).fetchone()
        capture_id = found[0] if found else db.execute(
            "INSERT INTO captures(sha256, provider, path, instrument, interval, request_json, received_utc_ns,"
            " ingested_utc_ns, rows_total, rows_inserted, rows_rejected) VALUES (?,?,?,?,?,?,?,?,?,0,0)",
            (digest, "derived", f"derived:{instrument}:{provider}:{source}", instrument, source,
             _canonical(dict(source=source, provider=provider, targets=list(targets))).decode(), None,
             time.time_ns(), len(rows))).lastrowid
        for target in targets:
            length = DERIVED_INTERVALS[target] * 1_000_000_000
            buckets = {}
            for r in rows:
                if futures:
                    local = datetime.fromtimestamp(r[0] / 1e9, NY)
                    if target == "1h":
                        start = int(local.replace(minute=0, second=0, microsecond=0).timestamp()) * 1_000_000_000
                    else:
                        anchor = local.replace(hour=18, minute=0, second=0, microsecond=0)
                        if local.hour < 18:
                            anchor -= timedelta(days=1)
                        start = int(anchor.timestamp()) * 1_000_000_000 + (r[0] - int(anchor.timestamp()) * 1_000_000_000) // length * length
                    end = start + length
                else:
                    opened, closed = hours.get(r[1], (None, None))
                    if opened is None or not opened <= r[0] < closed:
                        continue
                    start = opened + (r[0] - opened) // length * length
                    end = min(start + length, closed)
                buckets.setdefault((start, end), []).append(r)
            db.execute("DELETE FROM bars WHERE instrument=? AND interval=? AND provider=? AND quality='derived'",
                       (instrument, target, provider))
            made = skipped = 0
            for (start, end), members in sorted(buckets.items()):
                expected = [t for t in range(start, end, step)
                            if not (futures and datetime.fromtimestamp(t / 1e9, NY).hour == 17)]
                if any(t not in by_start for t in expected):
                    skipped += 1
                    continue
                parts = [by_start[t] for t in expected]
                volume = None if any(b[6] is None for b in parts) else math.fsum(b[6] for b in parts)
                db.execute("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (instrument, target, start, provider, parts[0][1], "eth" if futures else "rth",
                            parts[0][2], max(b[3] for b in parts), min(b[4] for b in parts), parts[-1][5],
                            volume, "derived", capture_id))
                made += 1
            summary[target] = dict(bars=made, skipped_incomplete=skipped)
    return summary


def derive_all(db, source="5m"):
    pairs = db.execute("SELECT DISTINCT instrument, provider FROM bars WHERE interval=? AND quality != 'derived'"
                       " ORDER BY 1, 2", (source,)).fetchall()
    return {f"{i}:{p}": derive(db, i, p, source=source) for i, p in pairs}


# ------------------------------------------------------------------ regime validation

def regime_outcomes(db, *, start=None, end=None, trend_close_fraction=0.1, reversal_sigma=1.0):
    """Score each pre-open regime label against the session that followed, in expected-move units.

    Expected move = prior SPX close x prior VIX / 100 / sqrt(252) (``regime_study``).
    Daily proxies (definitions are provisional and documented):
      trend day: range >= 1 expected move and close within ``trend_close_fraction`` of an extreme
      reversal from low: low <= -``reversal_sigma`` and close at least 1 move above the low (mirror for high)
    Close-only rows are excluded from range-based measures.
    """
    from statistics import fmean, median
    from qte.regime_study import features
    rows = features({"SPX": daily(db, "SPX"), "VIX": daily(db, "VIX"), "SPY": daily(db, "SPY")})
    sessions = []
    for r in rows:
        if r["regime"] == "unclassified" or (start and r["date"] < start) or (end and r["date"] > end):
            continue
        m, a = r["expected_move"], r["anchor"]
        s = dict(date=r["date"], regime=r["regime"], close_sigma=(r["close"] - a) / m)
        if r.get("quality") != "close_only" and r["high"] > r["low"]:
            hi, lo = (r["high"] - a) / m, (r["low"] - a) / m
            rng = hi - lo
            loc = (r["close"] - r["low"]) / (r["high"] - r["low"])
            s.update(high_sigma=hi, low_sigma=lo, range_sigma=rng,
                     trend_up=rng >= 1 and loc >= 1 - trend_close_fraction,
                     trend_down=rng >= 1 and loc <= trend_close_fraction,
                     reversal_from_low=lo <= -reversal_sigma and s["close_sigma"] - lo >= 1,
                     reversal_from_high=hi >= reversal_sigma and hi - s["close_sigma"] >= 1)
        sessions.append(s)

    def summarize(group):
        ranged = [g for g in group if "range_sigma" in g]
        pct = lambda pred, pool: round(100 * sum(map(pred, pool)) / len(pool), 1) if pool else None
        return dict(
            sessions=len(group), ranged_sessions=len(ranged),
            mean_close_sigma=round(fmean(g["close_sigma"] for g in group), 3),
            median_close_sigma=round(median(g["close_sigma"] for g in group), 3),
            pct_close_beyond_1sigma=pct(lambda g: abs(g["close_sigma"]) > 1, group),
            pct_close_beyond_2sigma=pct(lambda g: abs(g["close_sigma"]) > 2, group),
            pct_touch_1sigma=pct(lambda g: g["high_sigma"] >= 1 or g["low_sigma"] <= -1, ranged),
            pct_touch_minus_1sigma=pct(lambda g: g["low_sigma"] <= -1, ranged),
            pct_touch_plus_1sigma=pct(lambda g: g["high_sigma"] >= 1, ranged),
            mean_range_sigma=round(fmean(g["range_sigma"] for g in ranged), 3) if ranged else None,
            pct_trend_up_day=pct(lambda g: g["trend_up"], ranged),
            pct_trend_down_day=pct(lambda g: g["trend_down"], ranged),
            pct_reversal_from_low=pct(lambda g: g["reversal_from_low"], ranged),
            pct_reversal_from_high=pct(lambda g: g["reversal_from_high"], ranged),
        )

    by_regime = {}
    for s in sessions:
        by_regime.setdefault(s["regime"], []).append(s)
    return dict(
        schema="qte-regime-outcomes-v1", start=sessions[0]["date"] if sessions else None,
        end=sessions[-1]["date"] if sessions else None,
        definitions=dict(expected_move="prior SPX close * prior VIX / 100 / sqrt(252)",
                         trend_day=f"range >= 1 move and close within {trend_close_fraction:.0%} of high/low",
                         reversal=f"touch {reversal_sigma:g} move beyond prior close, close >= 1 move off the extreme",
                         normal_reference="if VIX were an exact daily sigma, |close| > 1 move on ~31.7% of days"),
        all=summarize(sessions) if sessions else None,
        regimes={k: summarize(v) for k, v in sorted(by_regime.items())},
        sessions=sessions,
    )


def render_regimes(report):
    cols = [("sessions", "N"), ("mean_close_sigma", "mean close σ"), ("pct_close_beyond_1sigma", "|close|>1σ %"),
            ("pct_close_beyond_2sigma", ">2σ %"), ("pct_touch_1sigma", "touch ±1σ %"),
            ("mean_range_sigma", "range σ"), ("pct_trend_up_day", "trend up %"),
            ("pct_trend_down_day", "trend down %"), ("pct_reversal_from_low", "rev. low %"),
            ("pct_reversal_from_high", "rev. high %")]
    lines = [f"# Regime outcomes {report['start']} to {report['end']}", "",
             "Each session's pre-open regime (prior-day data only) against what the session did, "
             "in expected-move units.", ""]
    lines += [f"- **{k}**: {v}" for k, v in report["definitions"].items()]
    lines += ["", "| regime | " + " | ".join(c[1] for c in cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for name, s in [("ALL", report["all"]), *report["regimes"].items()]:
        lines.append(f"| {name} | " + " | ".join("" if s[c] is None else str(s[c]) for c, _ in cols) + " |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ CLI

def main(argv=None):
    parser = argparse.ArgumentParser(prog="qte truth", description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest", help="Ingest Schwab priceHistory captures (files or directories)")
    ing.add_argument("paths", type=Path, nargs="+")
    sub.add_parser("coverage", help="Show what the database holds")
    col = sub.add_parser("collect", help="Fetch the standing Schwab set with the stored token and ingest it")
    col.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    col.add_argument("--output", type=Path, help="New capture directory (default data/truth/captures/schwab/<UTC stamp>)")
    col.add_argument("--lookback-days", type=int, help="Limit intraday/daily requests to recent days")
    sub.add_parser("derive", help="Rebuild derived 1h/4h bars from complete 5-minute buckets")
    reg = sub.add_parser("regimes", help="Score pre-open regimes against daily outcomes")
    reg.add_argument("--start")
    reg.add_argument("--end")
    reg.add_argument("--output", type=Path, required=True, help="New report directory")
    args = parser.parse_args(argv)
    try:
        with closing(connect(args.db)) as db:
            if args.command == "ingest":
                results = ingest_paths(db, args.paths)
                for r in results:
                    print(json.dumps(r))
                errors = [r for r in results if r["status"] == "error"]
                print(f"{sum(r['status'] == 'ingested' for r in results)} ingested, "
                      f"{sum(r['status'] == 'already_ingested' for r in results)} already present, "
                      f"{len(errors)} errors", file=sys.stderr)
                return 1 if errors else 0
            if args.command == "coverage":
                for r in coverage(db):
                    print(json.dumps(r))
                return 0
            if args.command == "derive":
                for key, result in derive_all(db).items():
                    print(json.dumps({key: result}))
                return 0
            if args.command == "collect":
                out = args.output or CAPTURES / "schwab" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                results = collect(db, Path.cwd(), args.contract, out, lookback_days=args.lookback_days)
                for r in results:
                    print(json.dumps(r))
                return 1 if any(r and r.get("status") == "error" for r in results) else 0
            report = regime_outcomes(db, start=args.start, end=args.end)
            args.output.mkdir(parents=True, exist_ok=False)
            (args.output / "report.json").write_bytes(_canonical(report) + b"\n")
            (args.output / "report.md").write_text(render_regimes(report))
            print(render_regimes(report))
            return 0
    except (OSError, TruthError, sqlite3.Error) as exc:
        print(f"qte truth: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
