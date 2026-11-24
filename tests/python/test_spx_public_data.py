from datetime import datetime, timedelta, timezone
from hashlib import sha256
from zipfile import ZipFile

import pytest

from qte.spx_public_data import load_firstrate_spx


def _rows(first_minute, count):
    start = datetime.fromisoformat(first_minute)
    return [
        f"{start + timedelta(minutes=i):%Y-%m-%d %H:%M:%S},6000,6002,5999,6001"
        for i in range(count)
    ]


def _archive(path, rows):
    with ZipFile(path, "w") as archive:
        archive.writestr("SPX_1min_sample.csv", "\n".join(["timestamp,open,high,low,close", *rows]))


def _utc_ns(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def test_spx_sample_normalizes_bar_ends_and_dst_and_reports_incomplete_sessions(tmp_path):
    path = tmp_path / "sample.zip"
    _archive(path, [
        *_rows("2025-10-31 09:30", 390),
        *_rows("2025-10-31 16:00", 1),
        *_rows("2025-11-03 09:30", 390),
        *_rows("2025-11-04 09:31", 389),
    ])
    sessions, source = load_firstrate_spx(path)

    assert [item["id"] for item in sessions] == ["2025-10-31", "2025-11-03"]
    assert sessions[0]["open_ns"] == _utc_ns("2025-10-31 13:30")
    assert sessions[1]["open_ns"] == _utc_ns("2025-11-03 14:30")
    assert sessions[1]["end_ns"] == _utc_ns("2025-11-03 21:00")
    first_bar = sessions[1]["bars"][0]
    assert first_bar == {"timestamp_ns": _utc_ns("2025-11-03 14:31"),
                         "available_ns": _utc_ns("2025-11-03 14:31"),
                         "open": 6000, "high": 6002, "low": 5999, "close": 6001}
    assert sessions[1]["bars"][-1]["available_ns"] == sessions[1]["end_ns"]
    assert source["source_sha256"] == sha256(path.read_bytes()).hexdigest()
    assert source["rth_rows_used"] == 780
    assert source["outside_regular_rows"] == 1
    assert source["skipped_sessions"] == {"2025-11-04": {"reason": "non_full_regular_session", "rth_rows": 389}}


@pytest.mark.parametrize(("rows", "message"), [
    (_rows("2025-11-03 09:30", 1) * 2, "invalid or unordered"),
    (["2025-11-03 09:30:00,6000,nan,5999,6001"], "invalid FirstRate SPX OHLC"),
])
def test_spx_sample_rejects_duplicate_or_invalid_market_observations(tmp_path, rows, message):
    path = tmp_path / "invalid.zip"
    _archive(path, rows)
    with pytest.raises(ValueError, match=message):
        load_firstrate_spx(path)
