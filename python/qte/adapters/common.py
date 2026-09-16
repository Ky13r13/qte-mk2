from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime

from qte import Dataset


_RFC3339_UTC = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?Z$"
)


@dataclass(frozen=True)
class SourceProvenance:
    provider: str
    schema_id: str
    adapter_version: str
    source_sha256: str
    assumptions: tuple[str, ...]


@dataclass(frozen=True)
class AdapterResult:
    dataset: Dataset
    provenance: SourceProvenance


def parse_rfc3339_utc_ns(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an RFC3339 UTC string")
    match = _RFC3339_UTC.fullmatch(value)
    if match is None:
        raise ValueError("timestamp must use RFC3339 UTC Z form with at most 9 fractional digits")
    whole = datetime.strptime(
        f"{match.group('date')}T{match.group('time')}", "%Y-%m-%dT%H:%M:%S"
    )
    seconds = calendar.timegm(whole.timetuple())
    fraction = (match.group("fraction") or "").ljust(9, "0")
    return seconds * 1_000_000_000 + (int(fraction) if fraction else 0)


def require_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)
