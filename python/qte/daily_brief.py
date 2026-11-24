"""Publish completed session evidence for the read-only Daily brief page."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .json_input import strict_json
from .live_outputs import publish

ARCHIVE = Path("build/daily-regimes")
PUBLIC = Path("build/daily-briefs")
SYMBOLS = ("SPX", "VIX", "SPY", "QQQ", "IWM")


def iso_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    return date.fromisoformat(value).isoformat() == value


def completed(repository, now=None):
    """Return closed XNYS sessions covering the local daily archive through now."""
    from .adapters.exchange_sessions import import_xnys
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    dates = [date.fromisoformat(f.parent.parent.name) for f in (repository/ARCHIVE).glob("*/*/summary.json")
             if iso_day(f.parent.parent.name)]
    start = min([today-timedelta(days=10), *dates])
    schedule = import_xnys(start.isoformat(), today.isoformat())
    return [d for d in schedule.days if d.close_ns and d.close_ns <= int(now.timestamp()*1e9)]


def due_sessions(repository, now=None):
    days = completed(repository, now)
    path = repository/PUBLIC/"index.json"
    known = {e["session"] for e in strict_json(path.read_bytes())["entries"]} if path.exists() else set()
    if not known:
        return [days[-1].trading_date]
    return [d.trading_date for d in days if d.trading_date >= min(known) and d.trading_date not in known]


def news_item(item):
    url = item["source"]
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("News sources must be HTTPS publisher links")
    stamp = item.get("time") or item.get("date")
    if stamp is None or (not iso_day(stamp) and datetime.fromisoformat(stamp).utcoffset() is None):
        raise ValueError("Each news event needs a date or timezone-aware timestamp")
    title = item.get("event") or item.get("evidence")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Missing news description")
    return dict(time=stamp, headline=title, source=url, attribution=item.get("attribution"))


def normalize(summary, session, capture):
    if not iso_day(session) or summary["session"] != session:
        raise ValueError("Capture path and session date must agree")
    if "prior_known_regime" in summary:
        prior = summary["prior_known_regime"]
        values = summary["metrics"]
        outcome = summary["retrospective_outcome"]
        notes = [summary["daily_status"], summary.get("options"), summary.get("futures")]
        sigma = summary.get("sigma", {})
    else:
        # Original November 20 format stored daily and intraday observations separately.
        prior = summary["prior_regime"]
        groups = summary["observations"]
        daily = {**groups["indices"], **groups["equities"]}
        values = {}
        for symbol, bar in daily.items():
            intra = groups.get("spx" if symbol == "SPX" else "minutes", {}).get(symbol, {})
            values[symbol] = dict(last=bar["close"], return_pct=bar["return_pct"], gap_pct=bar.get("gap_pct"),
                                  observed=intra.get("valid_bars"), interval_seconds=60 if intra else None)
        outcome = summary["retrospective_assessment"]
        notes = ["Prices and returns use provider daily bars.", *summary["limitations"]]
        sigma = summary.get("spx_excursions_sigma", {})
    unknown = prior["regime"] == "unclassified" and prior["asof"] is None
    if not unknown and (not iso_day(prior["asof"]) or prior["asof"] >= session):
        raise ValueError("Prior regime must predate the session")
    if not all(isinstance(v, str) and v.strip() for v in (prior["regime"], prior["reason"], outcome)):
        raise ValueError("Missing regime, rationale or outcome")
    events = summary.get("events", [])
    if summary.get("event"):
        events = [summary["event"], *events]
    rows = []
    for symbol in SYMBOLS:
        data = values.get(symbol, {})
        row = {k: data.get(k) for k in ("last", "gap_pct", "return_pct",
                                        "observed", "expected", "interval_seconds")}
        if any(v is not None and type(v) not in (int, float) for v in row.values()):
            raise ValueError(f"Invalid {symbol} metric")
        rows.append(dict(symbol=symbol, **row))
    if all(row["last"] is None for row in rows):
        raise ValueError("A completed brief needs an observed market level; retain failed attempts separately")
    if not isinstance(sigma, dict):
        raise ValueError("Expected an object for prior expected-move excursions")
    return dict(schema="qte-daily-brief-v1", session=session, capture=capture, regime=prior["regime"],
                asof=prior["asof"], reason=prior["reason"], outcome=outcome, news=[
                    news_item(e) for e in events],
                markets=rows, coverage=[n for n in notes if n], sigma=sigma,
                evidence_path=(ARCHIVE/session/capture/"summary.json").as_posix(), data=summary)


def publish_archive(repository):
    output = repository/PUBLIC
    if output.resolve() != repository.resolve()/PUBLIC:
        raise ValueError("Daily publications must stay inside the checkout")
    output.mkdir(parents=True, exist_ok=True)
    with (output/"publish.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        return _publish(repository, output)


def _publish(repository, output):
    archive = repository/ARCHIVE
    entries = []
    eligible = {d.trading_date for d in completed(repository)}
    for source in sorted(archive.glob("*/*/summary.json"), reverse=True):
        day, capture = source.parent.parent.name, source.parent.name
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        if day not in eligible:
            raise ValueError(f"Expected a completed XNYS session, got {day}")
        if source.resolve() != repository.resolve()/ARCHIVE/day/capture/"summary.json":
            raise ValueError("Expected regular archive paths inside the checkout")
        raw = source.read_bytes()
        try:
            brief = normalize(strict_json(raw), day, capture)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Invalid daily evidence at {source}: {exc}") from exc
        brief["source_sha256"] = sha256(raw).hexdigest()
        encoded = json.dumps(brief, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
        if len(encoded) > 900_000:
            raise ValueError("Keep raw responses separate from the session summary")
        key = sha256(encoded).hexdigest()
        record = output/(key+".json")
        if record.exists():
            if record.read_bytes() != encoded+b"\n":
                raise ValueError(f"Published brief checksum mismatch: {key}")
        else:
            publish(record, brief)
        entries.append(dict(session=day, capture=capture, id=key))
    if not entries:
        raise ValueError("No captured daily summaries under build/daily-regimes")
    # Publish the inventory last. Older content-addressed records remain readable.
    publish(output/"index.json", dict(schema="qte-daily-brief-index-v1", entries=entries))
    return output/"index.json"
