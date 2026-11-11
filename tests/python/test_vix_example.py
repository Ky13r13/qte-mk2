from __future__ import annotations

import calendar
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[2]
PYTHON = sys.executable
EXAMPLE = ROOT / "examples/vix_expected_moves.py"
URL_DATE = "2026-11-03"


def utc(day: str) -> int:
    return calendar.timegm(datetime.strptime(day, "%Y-%m-%d").timetuple()) * 1_000_000_000


def run(*arguments: str):
    return subprocess.run(
        [PYTHON, str(EXAMPLE), *arguments], cwd=ROOT,
        text=True, capture_output=True, check=False,
    )


def finite(value):
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, dict):
        return all(finite(item) for item in value.values())
    if isinstance(value, list):
        return all(finite(item) for item in value)
    return True


def test_default_example_is_reproducible_finite_and_clearly_synthetic():
    first, second = run(), run()
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    output = json.loads(first.stdout)
    assert output["kind"] == "synthetic_only"
    assert output["target_input_kind"] == "synthetic_nq_demo_completed_ohlc"
    assert output["input_provenance"]["provider"] == "synthetic"
    assert output["analysis"]["selected_future"]["evidence"] == "synthetic"
    assert output["analysis"]["proxy"]["volatility_scale"] == 1.0
    assert output["analysis"]["gamma"]["dollars_per_point_squared"] == 0.02
    assert [item["label"] for item in output["analysis"]["level_candles"]] == ["1d", "1h"]
    assert [item["horizon"]["label"] for item in output["analysis"]["moves"]] == [
        "1m", "5m", "15m", "30m", "1h", "4h", "1d_calendar",
    ]
    assert finite(output)


def test_saved_csv_preserves_hash_retrieval_and_explicit_contract_selection(tmp_path):
    raw = (
        "Product,Symbol,Expiration Date,Price\r\n"
        "VX,VX/X6,2026-11-18,19.8993\r\n"
        "VX,VX44/X6,2026-11-04,18.5\r\n"
    ).encode()
    path = tmp_path / "saved vx.csv"
    path.write_bytes(raw)
    retrieved = utc("2026-11-04")
    result = run(
        "--vx-csv", str(path),
        "--settlement-date", URL_DATE,
        "--retrieved-at-ns", str(retrieved),
        "--contract-id", "VX/X6@2026-11-18",
        "--volatility-scale", "1.25",
        "--gamma-dollars-per-point-squared", "-50",
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["kind"] == "saved_cboe_vx_mixed_with_synthetic_target"
    assert output["analysis"]["timestamp_ns"] == retrieved
    selected = output["analysis"]["selected_future"]
    assert selected["contract_id"] == "VX/X6@2026-11-18"
    assert selected["available_ns"] == retrieved
    assert selected["reference_ns"] == utc("2026-11-04")
    assert selected["discretionary"] is None
    assert output["analysis"]["proxy"]["volatility_scale"] == 1.25
    assert output["analysis"]["gamma"]["dollars_per_point_squared"] == -50.0
    provenance = output["input_provenance"]
    assert provenance["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert f"retrieved_at_ns={retrieved}" in provenance["assumptions"]
    assert any("status is unknown" in warning for warning in output["analysis"]["warnings"])
    assert finite(output)


@pytest.mark.parametrize("arguments", [
    ("--vx-csv", "missing.csv"),
    ("--settlement-date", URL_DATE),
    ("--retrieved-at-ns", str(utc("2026-11-04"))),
    ("--contract-id", "VX/X6@2026-11-18"),
    ("--vx-csv", "missing.csv", "--settlement-date", URL_DATE,
     "--retrieved-at-ns", str(utc("2026-11-04"))),
])
def test_saved_mode_requires_all_four_explicit_inputs(arguments):
    result = run(*arguments)
    assert result.returncode == 2
    assert "must be supplied together" in result.stderr


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_volatility_scale_must_be_positive_and_finite(value):
    result = run("--volatility-scale", value)
    assert result.returncode == 2


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_gamma_must_be_finite(value):
    result = run("--gamma-dollars-per-point-squared", value)
    assert result.returncode == 2


def test_saved_mode_rejects_expired_explicit_contract(tmp_path):
    path = tmp_path / "expired.csv"
    path.write_bytes(
        b"Product,Symbol,Expiration Date,Price\r\n"
        b"VX,VX44/X6,2026-11-04,18.5\r\n"
    )
    result = run(
        "--vx-csv", str(path), "--settlement-date", URL_DATE,
        "--retrieved-at-ns", str(utc("2026-11-04")),
        "--contract-id", "VX44/X6@2026-11-04",
    )
    assert result.returncode == 2
    assert "missing, unavailable, stale or expired" in result.stderr
