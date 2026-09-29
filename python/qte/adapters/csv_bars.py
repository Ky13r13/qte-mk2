from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

from qte import Bar, Dataset

from .common import AdapterResult, SourceProvenance, parse_rfc3339_utc_ns


ADAPTER_VERSION = "canonical-csv-bars-v1"


@dataclass(frozen=True)
class CsvBarSchema:
    symbol: str = "symbol"
    timestamp: str = "timestamp"
    open: str = "open"
    high: str = "high"
    low: str = "low"
    close: str = "close"
    volume: str = "volume"

    def columns(self) -> tuple[str, ...]:
        return (
            self.symbol,
            self.timestamp,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
        )


def load_csv_bars(
    path: str | Path,
    *,
    interval_ns: int,
    schema: CsvBarSchema = CsvBarSchema(),
    tick_size: float = 0.01,
    currency: str = "USD",
    volume_unit: str = "shares",
    adjustment: str = "raw",
    action_free: bool,
) -> AdapterResult:
    """Normalize explicitly mapped RFC3339-UTC CSV bars into an owned Dataset."""
    if type(interval_ns) is not int or interval_ns<=0:
        raise ValueError('interval_ns must be a positive integer')
    if adjustment != "raw":
        raise ValueError("initial QTE profile requires CSV adjustment='raw'")
    if action_free is not True:
        raise ValueError("initial QTE profile requires action_free=True")
    if len(set(schema.columns())) != len(schema.columns()):
        raise ValueError("CSV schema mappings must be unique")
    csv_path = Path(path)
    raw = csv_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("CSV fixture must be UTF-8") from error
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise ValueError("CSV fixture requires a header")
    if len(set(reader.fieldnames))!=len(reader.fieldnames):
        raise ValueError('duplicate CSV column names')
    missing = set(schema.columns()).difference(reader.fieldnames)
    if missing:
        raise ValueError(f"CSV fixture is missing mapped columns: {sorted(missing)}")
    bars: list[Bar] = []
    for line, record in enumerate(reader, start=2):
        if None in record or any(v is None for v in record.values()):
            raise ValueError(f'CSV field count mismatch at line {line}')
        try:
            start_ns = parse_rfc3339_utc_ns(record[schema.timestamp])
            bars.append(
                Bar(
                    record[schema.symbol],
                    start_ns,
                    start_ns + interval_ns,
                    float(record[schema.open]),
                    float(record[schema.high]),
                    float(record[schema.low]),
                    float(record[schema.close]),
                    float(record[schema.volume]),
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid CSV bar at line {line}: {error}") from error
    if not bars:
        raise ValueError("CSV fixture contains no bars")

    digest = hashlib.sha256(raw).hexdigest()
    source_id = f"csv:{ADAPTER_VERSION}:{digest}"
    data = Dataset.from_bars(
        bars,
        interval_ns=interval_ns,
        source_id=source_id,
        tick_size=tick_size,
        currency=currency,
        volume_unit=volume_unit,
    )
    provenance = SourceProvenance(
        provider="csv",
        schema_id="explicit-rfc3339-utc-csv",
        adapter_version=ADAPTER_VERSION,
        source_sha256=digest,
        assumptions=(
            "timestamp=RFC3339-UTC-nanoseconds",
            f"adjustment={adjustment}",
            "action_free=true",
            f"volume_unit={volume_unit}",
        ),
    )
    return AdapterResult(data, provenance)
