"""Normalize FirstRate Data's public one-minute S&P 500 cash-index sample."""
from __future__ import annotations

import csv
from datetime import datetime, time
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import math
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo


SAMPLE_URL = "https://frd001.s3.us-east-2.amazonaws.com/frd_sample_index_SPX.zip"
DATASET_URL = "https://firstratedata.com/i/index/SPX"
_MEMBER = "SPX_1min_sample.csv"
_SAMPLE_LIMIT = 5 * 1024 * 1024
_NY = ZoneInfo("America/New_York")
_SECOND_NS = 1_000_000_000
_MINUTE_NS = 60 * _SECOND_NS
_OPEN = time(9, 30)
_CLOSE = time(16)
_SESSION_MINUTES = 390
_FIELDS = ("timestamp", "open", "high", "low", "close")


def _ns(value: datetime) -> int:
    return int(value.timestamp()) * _SECOND_NS


def _bars(table):
    reader = csv.reader(table, strict=True)
    if next(reader, None) != list(_FIELDS):
        raise ValueError("unexpected FirstRate SPX CSV header")

    sessions = {}
    outside_regular_rows = 0
    previous = None
    for row in reader:
        if len(row) != len(_FIELDS):
            raise ValueError(f"invalid FirstRate SPX row {reader.line_num}")
        timestamp, *raw_prices = row
        stamp = datetime.fromisoformat(timestamp).replace(tzinfo=_NY)
        if (timestamp != stamp.strftime("%Y-%m-%d %H:%M:00")
                or previous is not None and stamp <= previous):
            raise ValueError(f"invalid or unordered FirstRate SPX timestamp at row {reader.line_num}")
        previous = stamp
        opening, high, low, close = prices = [float(value) for value in raw_prices]
        if (any(not math.isfinite(value) or value <= 0 for value in prices)
                or low > min(opening, close) or high < max(opening, close)):
            raise ValueError(f"invalid FirstRate SPX OHLC at row {reader.line_num}")
        if stamp.weekday() >= 5 or not _OPEN <= stamp.time() < _CLOSE:
            outside_regular_rows += 1
            continue
        end_ns = _ns(stamp) + _MINUTE_NS
        sessions.setdefault(stamp.date(), []).append({
            "timestamp_ns": end_ns,
            "available_ns": end_ns,
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
        })
    return sessions, outside_regular_rows


def load_firstrate_spx(path: Path) -> tuple[list[dict], dict]:
    """Return complete regular sessions in UTC and public archive provenance."""
    with path.open("rb") as source:
        raw = source.read(_SAMPLE_LIMIT + 1)
    if len(raw) > _SAMPLE_LIMIT:
        raise ValueError("FirstRate SPX sample ZIP exceeds 5 MiB")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            matches = [item for item in archive.infolist() if item.filename == _MEMBER]
            if len(matches) != 1 or matches[0].file_size > _SAMPLE_LIMIT:
                raise ValueError("expected one bounded SPX_1min_sample.csv member")
            with TextIOWrapper(archive.open(matches[0]), encoding="ascii", newline="") as table:
                source_sessions, outside_regular_rows = _bars(table)
    except (BadZipFile, csv.Error) as exc:
        raise ValueError(f"invalid FirstRate SPX sample: {exc}") from exc

    sessions = []
    skipped_sessions = {}
    for day, bars in source_sessions.items():
        day_id = day.isoformat()
        start = _ns(datetime.combine(day, _OPEN, tzinfo=_NY))
        if len(bars) != _SESSION_MINUTES:
            skipped_sessions[day_id] = {"reason": "non_full_regular_session", "rth_rows": len(bars)}
            continue
        sessions.append({"id": day_id, "open_ns": start,
                         "end_ns": start + _SESSION_MINUTES * _MINUTE_NS, "bars": bars})
    if not sessions:
        raise ValueError("FirstRate SPX sample has no complete regular sessions")
    provenance = {
        "provider": "FirstRate Data",
        "source_id": "firstrate_spx_public_sample_1min",
        "source_url": SAMPLE_URL,
        "dataset_url": DATASET_URL,
        "source_sha256": sha256(raw).hexdigest(),
        "underlying": "SPX cash index",
        "cadence": "1m",
        "first_date": sessions[0]["id"],
        "last_date": sessions[-1]["id"],
        "sessions_seen": len(source_sessions),
        "sessions_usable": len(sessions),
        "skipped_sessions": skipped_sessions,
        "rth_rows_used": _SESSION_MINUTES * len(sessions),
        "outside_regular_rows": outside_regular_rows,
        "availability_assumption": "Each bar becomes available at its end. Source receipt times are absent.",
    }
    return sessions, provenance
