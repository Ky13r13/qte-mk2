"""Import XNYS cash regular hours into the existing, offline QTE schedule format."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from hashlib import sha256
from importlib.metadata import version
import json

from qte.session_calendar import SessionDay, SessionSchedule


def _source_digest(source_id, start, end, days):
    evidence = [source_id, start, end, [asdict(day) for day in days]]
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(payload).hexdigest()


def verify_imported_snapshot(schedule: SessionSchedule) -> None:
    if schedule.source_id.startswith("exchange_calendars/") and schedule.source_id.endswith("/XNYS/cash-rth-v1"):
        expected = _source_digest(schedule.source_id, schedule.coverage_start, schedule.coverage_end, schedule.days)
        if expected != schedule.source_sha256:
            raise ValueError("imported XNYS hours differ from the declared source checksum")


def _coverage(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end:
        raise ValueError("coverage dates must be YYYY-MM-DD")
    if not 1 <= (last - first).days + 1 <= 36_600:
        raise ValueError("coverage must contain between 1 and 36,600 dates")
    return first, last


def import_xnys(start: str, end: str) -> SessionSchedule:
    """Declare every date, including closures. Record the installed provider version."""
    first, last = _coverage(start, end)
    try:
        import exchange_calendars
    except ModuleNotFoundError as exc:
        if exc.name != "exchange_calendars":
            raise
        raise ValueError("XNYS export requires exchange-calendars in the QTE venv") from exc

    # Padding supports one-date requests and verifies that holiday-only ranges
    # lie within actual provider coverage instead of mistaking truncation for closure.
    pad = timedelta(days=14)
    cal = exchange_calendars.get_calendar(
        "XNYS", start=(first - pad).isoformat(), end=(last + pad).isoformat()
    )
    if cal.first_session.date() > first or cal.last_session.date() < last:
        raise ValueError("exchange calendar cannot cover the entire requested interval")
    schedule = cal.schedule.loc[start:end, ["open", "break_start", "break_end", "close"]]
    if schedule[["break_start", "break_end"]].notna().any().any():
        raise ValueError("QTE session schedules require a single continuous session")
    opened = {}
    for label, opening, closing in schedule[["open", "close"]].itertuples():
        if opening.utcoffset() != timedelta(0) or closing.utcoffset() != timedelta(0):
            raise ValueError("calendar provider must supply explicit UTC timestamps")
        day = label.date().isoformat()
        if day in opened:
            raise ValueError("duplicate date in exchange calendar")
        opened[day] = SessionDay(day, opening.value, closing.value)
    days = []
    for offset in range((last - first).days + 1):
        label = (first + timedelta(days=offset)).isoformat()
        days.append(opened[label] if label in opened else SessionDay(label))
    source_id = f"exchange_calendars/{version('exchange-calendars')}/XNYS/cash-rth-v1"
    return SessionSchedule(start, end, source_id, _source_digest(source_id, start, end, days),
                           "historical_declared", tuple(days))
