"""Load one self-contained, versioned declaration of session hours and OHLCV."""
from hashlib import sha256

from qte import Bar
from qte.json_input import strict_json
from qte.session_calendar import SessionDay, SessionSchedule
from .common import AdapterResult, require_number
from .exchange_sessions import verify_imported_snapshot
from .session_bars import build_session_dataset


def _record(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"{label} requires exactly {', '.join(keys)}")
    return value


def schedule_from_dict(value) -> SessionSchedule:
    """Decode the shared schedule object used by research and the paper desk."""
    calendar = _record(value, (
        "coverage_start", "coverage_end", "source_id", "source_sha256", "evidence", "days",
    ), "session schedule")
    if type(calendar["days"]) is not list:
        raise ValueError("schedule days must be an ordered array")
    days = [SessionDay(**_record(day, ("trading_date", "open_ns", "close_ns"), "session day"))
            for day in calendar["days"]]
    schedule = SessionSchedule(**{**calendar, "days": days})
    verify_imported_snapshot(schedule)
    return schedule


def load_session_bundle(raw: bytes, *, tick_size: float = 0.01) -> AdapterResult:
    """Load exact captured bytes. Dates, closures and publication delay are explicit."""
    bundle = _record(strict_json(raw), (
        "schema", "source_id", "schedule", "kind", "publication_delay_ns",
        "action_free", "bars",
    ), "session bundle")
    if bundle["schema"] != "qte-session-bundle-v1":
        raise ValueError("unsupported session bundle schema")
    schedule = schedule_from_dict(bundle["schedule"])
    if type(tick_size) not in (int, float):
        raise ValueError("tick_size must be numeric")
    if type(bundle["bars"]) is not list or not bundle["bars"]:
        raise ValueError("session bundle requires a nonempty bar array")
    bars = []
    numeric = ("open", "high", "low", "close", "volume")
    for index, item in enumerate(bundle["bars"]):
        row = _record(item, ("symbol", "start_ns", "end_ns", *numeric), f"bar {index}")
        if type(row["symbol"]) is not str or any(
            type(row[key]) is not int or not -(2**63) <= row[key] < 2**63
            for key in ("start_ns", "end_ns")
        ):
            raise ValueError(f"bar {index} requires a symbol and exact UTC nanosecond timestamps")
        bars.append(Bar(row["symbol"], row["start_ns"], row["end_ns"],
                        *(require_number(row[key], key) for key in numeric)))
    return build_session_dataset(
        bars, schedule=schedule, kind=bundle["kind"], publication_delay_ns=bundle["publication_delay_ns"],
        action_free=bundle["action_free"], source_id=bundle["source_id"],
        source_sha256=sha256(raw).hexdigest(), tick_size=tick_size,
    )
