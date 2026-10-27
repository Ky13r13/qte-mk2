"""Strict bridge from canonical bars to caller-declared session windows."""
from __future__ import annotations

from datetime import date, timedelta
import re

from qte import Bar, BarObservation, Dataset
from qte.session_calendar import SessionSchedule

from .common import AdapterResult, SourceProvenance

_DAY_NS = 86_400_000_000_000
_EPOCH = date(1970, 1, 1)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _source_id(value: object) -> str:
    if (type(value) is not str or not value or value.strip() != value
            or len(value) > 256 or not value.isprintable()):
        raise ValueError("source_id must be nonempty, trimmed, printable, and at most 256 characters")
    return value


def _trading_date(timestamp_ns: int) -> str:
    try:
        return (_EPOCH + timedelta(days=timestamp_ns // _DAY_NS)).isoformat()
    except OverflowError:
        raise ValueError("bar timestamp is outside the supported UTC date range") from None


def build_session_dataset(
    bars,
    *,
    schedule: SessionSchedule,
    kind: str = "hourly",
    publication_delay_ns: int = 0,
    source_id,
    source_sha256,
    action_free,
    tick_size: float = 0.01,
) -> AdapterResult:
    """Bind canonical bars to exact declared buckets without filling or repair."""
    if type(schedule) is not SessionSchedule:
        raise ValueError("schedule must be a SessionSchedule")
    # Validate bucket policy and delay even when the bar collection is empty.
    schedule.buckets(
        schedule.coverage_start, kind=kind,
        publication_delay_ns=publication_delay_ns)
    source_id = _source_id(source_id)
    if type(source_sha256) is not str or _SHA256.fullmatch(source_sha256) is None:
        raise ValueError("source_sha256 must be 64 lowercase hexadecimal characters")
    if action_free is not True:
        raise ValueError("session bars require an explicit action_free=True assertion")
    if type(bars) not in {list, tuple}:
        raise ValueError("bars must be a list or tuple of canonical Bar objects")

    windows_by_date = {}
    observations = []
    for bar in bars:
        if not isinstance(bar, Bar):
            raise ValueError("bars must contain only canonical Bar objects")
        label = _trading_date(bar.start_ns)
        windows = windows_by_date.get(label)
        if windows is None:
            windows = schedule.buckets(
                label, kind=kind,
                publication_delay_ns=publication_delay_ns)
            windows_by_date[label] = windows
        matched = next(
            (window for window in windows
             if window.start_ns == bar.start_ns and window.end_ns == bar.end_ns),
            None,
        )
        if matched is None:
            raise ValueError("bar does not exactly match a declared session bucket")
        observations.append(BarObservation(bar, matched.available_at_ns))

    timing_source_id = (
        f"session_windows_v1:{schedule.snapshot_sha256}:{kind}:"
        f"{publication_delay_ns}"
    )
    dataset = Dataset.from_observations(
        observations,
        source_id=source_id,
        timing_source_id=timing_source_id,
        tick_size=tick_size,
        currency="USD",
        volume_unit="shares",
    )
    provenance = SourceProvenance(
        provider="session_declared",
        schema_id="qte-session-bars-v1",
        adapter_version="1",
        source_sha256=source_sha256,
        assumptions=(
            f"calendar_snapshot_sha256={schedule.snapshot_sha256}",
            f"calendar_source_id={schedule.source_id}",
            f"calendar_source_sha256={schedule.source_sha256}",
            f"calendar_evidence={schedule.evidence}",
            f"bucket_kind={kind}",
            f"publication_delay_ns={publication_delay_ns}",
            "execution=at_bar_start_v1",
            "ordering=strict_per_symbol_v1",
            "calendar_verification=declared-not-verified",
            "adjustment=raw",
            "action_free=true",
        ),
    )
    return AdapterResult(dataset, provenance)
