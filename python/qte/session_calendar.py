"""Immutable, offline session windows from an explicitly supplied UTC schedule.

The caller owns the New York trading-date labels, source identity, source hash,
and holiday/DST/official-hours assertions.  This module only validates and
projects the supplied same-UTC-day regular-session intervals; it performs no I/O
and never synthesizes a calendar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import re

_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_DAY_NS = 86_400_000_000_000
_HOUR_NS = 3_600_000_000_000
_EPOCH = date(1970, 1, 1)
_POLICY = "session_windows_v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _date(value: object) -> date:
    if type(value) is not str:
        raise ValueError("trading dates must be canonical YYYY-MM-DD strings")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("trading dates must be canonical YYYY-MM-DD strings") from None
    if parsed.isoformat() != value:
        raise ValueError("trading dates must be canonical YYYY-MM-DD strings")
    return parsed


def _int64(value: object, name: str) -> int:
    if type(value) is not int or not _INT64_MIN <= value <= _INT64_MAX:
        raise ValueError(f"{name} must be a signed 64-bit integer")
    return value


def _utc_date(timestamp_ns: int) -> date:
    days = timestamp_ns // _DAY_NS
    try:
        return _EPOCH + timedelta(days=days)
    except OverflowError:
        raise ValueError("timestamp is outside the supported UTC date range") from None


def _same_utc_day(label: date, start_ns: int, end_ns: int) -> None:
    if _utc_date(start_ns) != label or _utc_date(end_ns - 1) != label:
        raise ValueError("session interval must lie within its labeled UTC date")


@dataclass(frozen=True, slots=True)
class SessionDay:
    """One declared trading date: closed, or one same-UTC-day open interval."""

    trading_date: str
    open_ns: int | None = None
    close_ns: int | None = None

    def __post_init__(self) -> None:
        label = _date(self.trading_date)
        if self.open_ns is None and self.close_ns is None:
            return
        if self.open_ns is None or self.close_ns is None:
            raise ValueError("open_ns and close_ns must both be set or both be None")
        opened = _int64(self.open_ns, "open_ns")
        closed = _int64(self.close_ns, "close_ns")
        if opened >= closed:
            raise ValueError("open_ns must precede close_ns")
        _same_utc_day(label, opened, closed)


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """A supplied-session bucket available no earlier than its declared time."""

    trading_date: str
    start_ns: int
    end_ns: int
    available_at_ns: int

    def __post_init__(self) -> None:
        label = _date(self.trading_date)
        start = _int64(self.start_ns, "start_ns")
        end = _int64(self.end_ns, "end_ns")
        available = _int64(self.available_at_ns, "available_at_ns")
        if not start < end <= available:
            raise ValueError("window must satisfy start_ns < end_ns <= available_at_ns")
        _same_utc_day(label, start, end)


@dataclass(frozen=True, slots=True)
class SessionSchedule:
    """Complete inclusive declaration of at most 36,600 consecutive dates."""

    coverage_start: str
    coverage_end: str
    source_id: str
    source_sha256: str
    evidence: str
    days: tuple[SessionDay, ...]

    def __post_init__(self) -> None:
        start = _date(self.coverage_start)
        end = _date(self.coverage_end)
        count = (end - start).days + 1
        if not 1 <= count <= 36_600:
            raise ValueError("coverage must contain between 1 and 36,600 dates")
        if (type(self.source_id) is not str or not self.source_id
                or self.source_id.strip() != self.source_id or len(self.source_id) > 256
                or not self.source_id.isprintable()):
            raise ValueError("source_id must be nonempty, trimmed, printable, and at most 256 characters")
        if type(self.source_sha256) is not str or _SHA256.fullmatch(self.source_sha256) is None:
            raise ValueError("source_sha256 must be 64 lowercase hexadecimal characters")
        if type(self.evidence) is not str or self.evidence not in {"synthetic", "historical_declared"}:
            raise ValueError("evidence must be synthetic or historical_declared")
        if type(self.days) not in {list, tuple}:
            raise ValueError("days must be a list or tuple")
        if len(self.days) != count:
            raise ValueError("days must contain exactly one SessionDay per coverage date")
        owned = tuple(self.days)
        if any(type(day) is not SessionDay for day in owned):
            raise ValueError("days must contain exactly one SessionDay per coverage date")

        previous_close = None
        for offset, day in enumerate(owned):
            if day.trading_date != (start + timedelta(days=offset)).isoformat():
                raise ValueError("days must exactly cover the ordered inclusive date range")
            if day.open_ns is not None:
                if previous_close is not None and day.open_ns < previous_close:
                    raise ValueError("open session intervals must be chronological and nonoverlapping")
                previous_close = day.close_ns
        object.__setattr__(self, "days", owned)

    @property
    def snapshot_sha256(self) -> str:
        """Hash canonical metadata and every open or explicitly closed date."""
        value = {
            "policy": _POLICY,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "evidence": self.evidence,
            "days": [
                {"trading_date": day.trading_date, "open_ns": day.open_ns, "close_ns": day.close_ns}
                for day in self.days
            ],
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def day(self, trading_date: str) -> SessionDay:
        """Return the explicitly declared day; reject labels outside coverage."""
        wanted = _date(trading_date)
        start = _date(self.coverage_start)
        offset = (wanted - start).days
        if offset < 0 or offset >= len(self.days):
            raise ValueError("trading_date is outside schedule coverage")
        return self.days[offset]

    def buckets(self, trading_date: str, *, kind: str = "hourly",
                publication_delay_ns: int = 0) -> tuple[SessionWindow, ...]:
        """Project daily or open-anchored hourly windows without aggregation."""
        if type(kind) is not str or kind not in {"hourly", "daily"}:
            raise ValueError("kind must be hourly or daily")
        delay = _int64(publication_delay_ns, "publication_delay_ns")
        if delay < 0:
            raise ValueError("publication_delay_ns must be nonnegative")
        declared = self.day(trading_date)
        if declared.open_ns is None:
            return ()

        intervals = []
        cursor = declared.open_ns
        while cursor < declared.close_ns:
            end = declared.close_ns if kind == "daily" else min(cursor + _HOUR_NS, declared.close_ns)
            if end > _INT64_MAX - delay:
                raise ValueError("available_at_ns exceeds signed 64-bit range")
            intervals.append(SessionWindow(declared.trading_date, cursor, end, end + delay))
            if kind == "daily":
                break
            cursor = end
        return tuple(intervals)

    def session_close_grid(self) -> tuple[int, ...]:
        """Return unshifted closes only; no sampling or annualization is inferred."""
        return tuple(day.close_ns for day in self.days if day.close_ns is not None)
