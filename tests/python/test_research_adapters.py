"""Captured-byte seams must preserve the existing adapter contract exactly."""
from __future__ import annotations

from pathlib import Path

import pytest

from qte.adapters.alpaca import _load_alpaca_bytes, load_alpaca_fixture
from qte.adapters.csv_bars import CsvBarSchema, _load_csv_bytes, load_csv_bars


ROOT = Path(__file__).resolve().parents[2]
HOUR_NS = 3_600_000_000_000
CSV = ROOT / "tests/fixtures/csv/stock_bars_complete.csv"
ALPACA = ROOT / "tests/fixtures/alpaca/stock_bars_complete.json"


@pytest.mark.parametrize(("path", "path_loader", "byte_loader", "kwargs"), [
    (CSV, load_csv_bars, _load_csv_bytes, {"action_free": True}),
    (ALPACA, load_alpaca_fixture, _load_alpaca_bytes, {}),
])
def test_captured_bytes_match_path_adapter_identity_and_dataset(path, path_loader, byte_loader, kwargs):
    path_result = path_loader(path, interval_ns=HOUR_NS, **kwargs)
    byte_result = byte_loader(path.read_bytes(), interval_ns=HOUR_NS, **kwargs)
    assert byte_result.provenance == path_result.provenance
    assert byte_result.dataset.source_id == path_result.dataset.source_id
    assert byte_result.dataset.bar_count == path_result.dataset.bar_count
    assert byte_result.dataset.start_ns == path_result.dataset.start_ns
    assert byte_result.dataset.end_ns == path_result.dataset.end_ns


@pytest.mark.parametrize(("path", "path_loader", "kwargs"), [
    (CSV, load_csv_bars, {"action_free": True}),
    (ALPACA, load_alpaca_fixture, {}),
])
def test_path_adapter_captures_its_source_bytes_once(monkeypatch, path, path_loader, kwargs):
    original, reads = Path.read_bytes, []

    def read_once(self):
        reads.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_once)
    path_loader(path, interval_ns=HOUR_NS, **kwargs)
    assert reads == [path]


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"action_free": True, "interval_ns": 0}, "interval_ns must be a positive integer"),
    ({"action_free": False, "interval_ns": HOUR_NS}, "action_free=True"),
    ({"action_free": True, "interval_ns": HOUR_NS,
      "schema": CsvBarSchema(symbol="symbol", timestamp="symbol")}, "mappings must be unique"),
])
def test_csv_invalid_options_fail_before_opening_the_path(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        load_csv_bars(tmp_path / "missing-or-fifo.csv", **kwargs)


@pytest.mark.parametrize(("suffix", "raw", "path_loader", "byte_loader", "kwargs"), [
    ("csv", b"symbol,timestamp\nSPY,2024-01-02T14:00:00Z\n", load_csv_bars, _load_csv_bytes,
     {"action_free": True}),
    ("json", b'{"bars":', load_alpaca_fixture, _load_alpaca_bytes, {}),
])
def test_invalid_captured_bytes_fail_with_the_same_adapter_error(tmp_path, suffix, raw, path_loader, byte_loader, kwargs):
    path = tmp_path / f"invalid.{suffix}"
    path.write_bytes(raw)
    with pytest.raises(ValueError) as from_path:
        path_loader(path, interval_ns=HOUR_NS, **kwargs)
    with pytest.raises(ValueError) as from_bytes:
        byte_loader(raw, interval_ns=HOUR_NS, **kwargs)
    assert str(from_bytes.value) == str(from_path.value)
