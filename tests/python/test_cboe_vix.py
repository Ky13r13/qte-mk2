from __future__ import annotations

import calendar
from datetime import datetime
import hashlib

import pytest

from qte.adapters.cboe_vix import parse_cboe_vx_settlements


URL = (
    "https://www-api.cboe.com/us/futures/market_statistics/settlement/csv/"
    "?dt=2026-11-03"
)


def utc(day: str) -> int:
    return calendar.timegm(datetime.strptime(day, "%Y-%m-%d").timetuple()) * 1_000_000_000


RETRIEVED = utc("2026-11-05")


def csv_bytes(*rows: str) -> bytes:
    return ("Product,Symbol,Expiration Date,Price\r\n" +
            "\r\n".join(rows) + "\r\n").encode()


def parse(raw: bytes, **changes):
    values = dict(
        settlement_date="2026-11-03",
        retrieved_at_ns=RETRIEVED,
        source_url=URL,
    )
    values.update(changes)
    return parse_cboe_vx_settlements(raw, **values)


def test_exact_cboe_schema_filters_products_and_preserves_discretionary_marker():
    raw = csv_bytes(
        "VX,VX/X6,2026-11-18,19.8993",
        "VXM,VXM/X6,2026-11-18,19.7",
        "VA,VA/X6,2026-11-18,20.1",
        "VX,VX/Z6,2026-12-16,20.25*",
    )
    snapshot = parse(raw)
    assert tuple(item.contract_id for item in snapshot.observations) == (
        "VX/X6@2026-11-18", "VX/Z6@2026-12-16")
    first, second = snapshot.observations
    assert first.value_pct == pytest.approx(19.8993)
    assert first.discretionary is None
    assert second.value_pct == pytest.approx(20.25)
    assert second.discretionary is True
    assert first.reference_ns == utc("2026-11-04")
    assert first.available_ns == RETRIEVED
    assert first.expiry_cutoff_ns == utc("2026-11-18")
    assert first.source_id == "cboe-vx-settlement:2026-11-03"
    assert first.evidence == "historical_declared"
    assert first.vintage_id.startswith("sha256:")
    assert len(first.vintage_id) == 71
    assert second.vintage_id == first.vintage_id

    provenance = snapshot.provenance
    assert provenance.provider == "cboe"
    assert provenance.schema_id == "cboe-vx-settlement-csv-v1"
    assert provenance.adapter_version == "1"
    assert provenance.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert "availability=retrieval_time_only" in provenance.assumptions
    assert "reference=end_of_settlement_date_utc" in provenance.assumptions
    assert "expiry_cutoff=start_of_expiration_date_utc_exclusive" in provenance.assumptions
    assert "discretionary_marker=trailing_asterisk_or_unknown_when_absent" in provenance.assumptions


def test_verified_monthly_and_weekly_symbol_shapes_are_preserved():
    raw = csv_bytes(
        "VX,VX/X6,2026-11-18,19.8993",
        "VX,VX44/X6,2026-11-04,18.5",
        "VX,VX45/X6,2026-11-11,18.8",
        "VX,VX47/X6,2026-11-25,20.1",
        "VX,VX48/Z6,2026-12-02,20.4",
        "VX,VX49/Z6,2026-12-09,20.7",
    )
    observations = parse(raw).observations
    assert tuple(item.contract_id for item in observations) == (
        "VX/X6@2026-11-18",
        "VX44/X6@2026-11-04",
        "VX45/X6@2026-11-11",
        "VX47/X6@2026-11-25",
        "VX48/Z6@2026-12-02",
        "VX49/Z6@2026-12-09",
    )
    assert all(item.discretionary is None for item in observations)


def test_expiry_qualified_ids_disambiguate_reused_short_contract_codes():
    observations = parse(csv_bytes(
        "VX,VX/X6,2026-11-18,19.8993",
        "VX,VX/X6,2036-11-19,29.8993",
    )).observations
    assert observations[0].contract_id != observations[1].contract_id
    assert {item.contract_id for item in observations} == {
        "VX/X6@2026-11-18", "VX/X6@2036-11-19",
    }


def test_vintage_commits_raw_bytes_retrieval_date_and_url():
    raw = csv_bytes("VX,VX/X6,2026-11-18,19.8993")
    baseline = parse(raw).observations[0].vintage_id
    assert parse(raw).observations[0].vintage_id == baseline
    assert parse(raw, retrieved_at_ns=RETRIEVED + 1).observations[0].vintage_id != baseline
    revised = csv_bytes("VX,VX/X6,2026-11-18,19.8994")
    assert parse(revised).observations[0].vintage_id != baseline


@pytest.mark.parametrize("changes", [
    {"settlement_date": "2026-11-3"},
    {"settlement_date": "not-a-date"},
    {"retrieved_at_ns": True},
    {"retrieved_at_ns": 1.5},
    {"retrieved_at_ns": utc("2026-11-03")},
    {"retrieved_at_ns": 2**63},
    {"source_url": URL + "&extra=1"},
    {"source_url": URL.replace("2026-11-03", "2026-11-02")},
])
def test_metadata_is_strict_and_retrieval_cannot_be_backdated(changes):
    with pytest.raises(ValueError):
        parse(csv_bytes("VX,VX/X6,2026-11-18,19.8993"), **changes)


@pytest.mark.parametrize("raw", [
    b"",
    b"Product,Symbol,Expiration Date\r\nVX,VX/X6,2026-11-18\r\n",
    b"Symbol,Product,Expiration Date,Price\r\nVX/X6,VX,2026-11-18,19\r\n",
    b"Product,Symbol,Expiration Date,Price,Extra\r\nVX,VX/X6,2026-11-18,19,x\r\n",
    b"Product,Symbol,Expiration Date,Price\r\nVX,VX/X6,2026-11-18\r\n",
    b"Product,Symbol,Expiration Date,Price\r\nVX,VX/X6,2026-11-18,19,x\r\n",
    b"Product,Symbol,Expiration Date,Price\r\nVXM,VXM/X6,2026-11-18,19\r\n",
    b"\xff\xfe",
])
def test_missing_unknown_ragged_non_utf8_and_no_vx_data_fail_closed(raw):
    with pytest.raises(ValueError):
        parse(raw)


@pytest.mark.parametrize("row", [
    "VX,VXM/X6,2026-11-18,19.0",
    "VX,VX0/X6,2026-11-18,19.0",
    "VX,VX54/X6,2026-11-18,19.0",
    "VX,VX44/Y6,2026-11-18,19.0",
    "VX,VX44/X123,2026-11-18,19.0",
    "VX,VX/X6,2026-11-18,0",
    "VX,VX/X6,2026-11-18,-1",
    "VX,VX/X6,2026-11-18,nan",
    "VX,VX/X6,2026-11-18,1e2",
    "VX,VX/X6,2026-11-18,19**",
    "VX,VX/X6,2026-11-18,19* ",
    "VX,VX/X6,2026-1-18,19",
])
def test_vx_symbol_price_and_expiry_fields_are_not_repaired(row):
    with pytest.raises(ValueError):
        parse(csv_bytes(row))


def test_duplicate_contract_and_expiration_is_rejected():
    raw = csv_bytes(
        "VX,VX/X6,2026-11-18,19.8993",
        "VX,VX/X6,2026-11-18,19.8994",
    )
    with pytest.raises(ValueError, match="duplicate"):
        parse(raw)


def test_raw_input_must_be_bytes():
    with pytest.raises(ValueError, match="bytes"):
        parse_cboe_vx_settlements(
            "Product,Symbol,Expiration Date,Price",  # type: ignore[arg-type]
            settlement_date="2026-11-03", retrieved_at_ns=RETRIEVED,
            source_url=URL,
        )
