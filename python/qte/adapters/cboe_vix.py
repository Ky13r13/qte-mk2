"""Strict offline adapter for saved Cboe VX settlement CSV bytes."""
from __future__ import annotations

import calendar
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import math
import re

from qte.volatility import VolatilityFutureObservation

from .common import SourceProvenance


_HEADERS = ("Product", "Symbol", "Expiration Date", "Price")
_URL_PREFIX = (
    "https://www-api.cboe.com/us/futures/market_statistics/settlement/csv/?dt="
)
_PRICE = re.compile(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(\*)?\Z")
_VX_SYMBOL = re.compile(
    r"VX(?:[1-9]|[1-4][0-9]|5[0-3])?/[FGHJKMNQUVXZ][0-9]{1,2}\Z"
)
_INT64_MAX = 2**63 - 1
_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class VixSettlementSnapshot:
    observations: tuple[VolatilityFutureObservation, ...]
    provenance: SourceProvenance


def _day(value: object, field: str) -> date:
    if type(value) is not str:
        raise ValueError(f"{field} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be a canonical ISO date") from None
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be a canonical ISO date")
    return parsed


def _midnight_ns(value: date) -> int:
    seconds = calendar.timegm(datetime(value.year, value.month, value.day).timetuple())
    result = seconds * _NS_PER_SECOND
    if result < -(2**63) or result > _INT64_MAX:
        raise ValueError("date is outside the signed nanosecond range")
    return result


def parse_cboe_vx_settlements(
    raw: bytes,
    *,
    settlement_date: str,
    retrieved_at_ns: int,
    source_url: str,
) -> VixSettlementSnapshot:
    """Parse saved bytes; availability is retrieval time, never backdated."""
    if type(raw) is not bytes:
        raise ValueError("raw must be bytes")
    settled = _day(settlement_date, "settlement_date")
    if type(retrieved_at_ns) is not int or isinstance(retrieved_at_ns, bool):
        raise ValueError("retrieved_at_ns must be an integer")
    if not -(2**63) <= retrieved_at_ns <= _INT64_MAX:
        raise ValueError("retrieved_at_ns is outside signed int64")
    if type(source_url) is not str or source_url != _URL_PREFIX + settlement_date:
        raise ValueError("source_url must be the dated public Cboe settlement endpoint")
    try:
        reference_ns = _midnight_ns(settled + timedelta(days=1))
    except OverflowError:
        raise ValueError("settlement_date has no representable next UTC day") from None
    if retrieved_at_ns < reference_ns:
        raise ValueError("retrieval cannot precede the settlement reference time")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Cboe settlement CSV must be UTF-8") from None

    rows = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        headers = next(rows)
    except (StopIteration, csv.Error):
        raise ValueError("Cboe settlement CSV is missing its header") from None
    if tuple(headers) != _HEADERS:
        raise ValueError("Cboe settlement CSV header does not match the supported schema")

    raw_sha256 = hashlib.sha256(raw).hexdigest()
    source_id = f"cboe-vx-settlement:{settlement_date}"
    canonical_vintage = json.dumps(
        {
            "raw_sha256": raw_sha256,
            "retrieved_at_ns": retrieved_at_ns,
            "settlement_date": settlement_date,
            "source_url": source_url,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    vintage_id = "sha256:" + hashlib.sha256(canonical_vintage).hexdigest()

    observations = []
    seen = set()
    try:
        for row in rows:
            if len(row) != len(_HEADERS):
                raise ValueError("Cboe settlement CSV contains a ragged row")
            product, symbol, expiration, price_text = row
            if product != "VX":
                continue
            if _VX_SYMBOL.fullmatch(symbol) is None:
                raise ValueError("VX row has an invalid contract symbol")
            expiry = _day(expiration, "Expiration Date")
            key = (symbol, expiration)
            if key in seen:
                raise ValueError("Cboe settlement CSV contains a duplicate VX contract/date")
            seen.add(key)
            match = _PRICE.fullmatch(price_text)
            if match is None:
                raise ValueError("VX settlement price has an invalid format")
            value = float(price_text[:-1] if match.group(1) else price_text)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError("VX settlement price must be finite and positive")
            observations.append(VolatilityFutureObservation(
                contract_id=f"{symbol}@{expiration}",
                expiry_cutoff_ns=_midnight_ns(expiry),
                value_pct=value,
                reference_ns=reference_ns,
                available_ns=retrieved_at_ns,
                source_id=source_id,
                vintage_id=vintage_id,
                evidence="historical_declared",
                discretionary=True if match.group(1) is not None else None,
            ))
    except csv.Error:
        raise ValueError("Cboe settlement CSV is malformed") from None
    if not observations:
        raise ValueError("Cboe settlement CSV contains no VX rows")

    provenance = SourceProvenance(
        provider="cboe",
        schema_id="cboe-vx-settlement-csv-v1",
        adapter_version="1",
        source_sha256=raw_sha256,
        assumptions=(
            f"source_url={source_url}",
            f"settlement_date={settlement_date}",
            f"retrieved_at_ns={retrieved_at_ns}",
            "availability=retrieval_time_only",
            "reference=end_of_settlement_date_utc",
            "expiry_cutoff=start_of_expiration_date_utc_exclusive",
            "evidence=historical_declared",
            "discretionary_marker=trailing_asterisk_or_unknown_when_absent",
        ),
    )
    return VixSettlementSnapshot(tuple(observations), provenance)
