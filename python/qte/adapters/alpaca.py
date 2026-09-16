from __future__ import annotations

import hashlib
import json
from pathlib import Path

from qte import Bar, Dataset

from .common import AdapterResult, SourceProvenance, parse_rfc3339_utc_ns, require_number


ADAPTER_VERSION = "alpaca-stock-bars-v1"
SCHEMA_ID = "alpaca-market-data-v2-stock-bars"
_REQUIRED_BAR_FIELDS = frozenset({"t", "o", "h", "l", "c", "v"})


def load_alpaca_fixture(
    path: str | Path,
    *,
    interval_ns: int,
    tick_size: float = 0.01,
    currency: str = "USD",
) -> AdapterResult:
    """Load one complete saved Alpaca stock-bars response; never performs I/O beyond the file."""
    fixture_path = Path(path)
    raw = fixture_path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Alpaca fixture must be valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("Alpaca fixture root must be an object")
    envelope = payload.get("_qte")
    if not isinstance(envelope, dict):
        raise ValueError("Alpaca fixture requires a _qte assumptions envelope")
    required_envelope = {"schema", "feed", "timeframe", "adjustment", "action_free"}
    missing_envelope = required_envelope.difference(envelope)
    if missing_envelope:
        raise ValueError(f"Alpaca fixture is missing _qte fields: {sorted(missing_envelope)}")
    if envelope["schema"] != SCHEMA_ID:
        raise ValueError(f"unsupported Alpaca schema: {envelope['schema']!r}")
    if envelope["adjustment"] != "raw":
        raise ValueError("initial QTE profile requires Alpaca adjustment='raw'")
    if envelope["action_free"] is not True:
        raise ValueError("initial QTE profile requires a declared action-free fixture")
    if payload.get("next_page_token") is not None:
        raise ValueError("Alpaca fixture is an incomplete paginated response")
    grouped = payload.get("bars")
    if not isinstance(grouped, dict) or not grouped:
        raise ValueError("Alpaca fixture bars must be a non-empty symbol object")

    bars: list[Bar] = []
    for symbol, records in grouped.items():
        if not isinstance(symbol, str) or not isinstance(records, list) or not records:
            raise ValueError("each Alpaca symbol must map to a non-empty bar list")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"Alpaca bar for {symbol} must be an object")
            missing = _REQUIRED_BAR_FIELDS.difference(record)
            if missing:
                raise ValueError(f"Alpaca bar for {symbol} is missing fields: {sorted(missing)}")
            start_ns = parse_rfc3339_utc_ns(record["t"])
            bars.append(
                Bar(
                    symbol,
                    start_ns,
                    start_ns + interval_ns,
                    require_number(record["o"], "o"),
                    require_number(record["h"], "h"),
                    require_number(record["l"], "l"),
                    require_number(record["c"], "c"),
                    require_number(record["v"], "v"),
                )
            )

    digest = hashlib.sha256(raw).hexdigest()
    source_id = f"alpaca:{SCHEMA_ID}:{digest}"
    data = Dataset.from_bars(
        bars,
        interval_ns=interval_ns,
        source_id=source_id,
        tick_size=tick_size,
        currency=currency,
        volume_unit="shares",
    )
    provenance = SourceProvenance(
        provider="alpaca",
        schema_id=SCHEMA_ID,
        adapter_version=ADAPTER_VERSION,
        source_sha256=digest,
        assumptions=(
            f"feed={envelope['feed']}",
            f"timeframe={envelope['timeframe']}",
            "adjustment=raw",
            "action_free=true",
            "complete_page_set=true",
        ),
    )
    return AdapterResult(data, provenance)
