from __future__ import annotations

import csv
from hashlib import sha256
import io
import json

import pytest

from qte.comparison_export import run_comparison
from qte.gui.comparison_artifacts import verify_comparison_files


INTERVAL = 60_000_000_000


def _bars(day: int) -> bytes:
    rows = ["symbol,timestamp,open,high,low,close,volume"]
    for index in range(16):
        value = 100 + day * 10 + index
        rows.append(
            f"SPY,2024-01-{day:02d}T00:{index:02d}:00Z,{value},{value + 1},"
            f"{value - 1},{value + .5},100"
        )
    return ("\n".join(rows) + "\n").encode()


def _window(name: str, role: str, regime: str, path: str, raw: bytes, *, kind: str = "historical") -> dict:
    return {
        "name": name, "role": role, "regime": regime,
        "data": {"adapter": "csv", "path": path, "interval_ns": INTERVAL, "action_free": True},
        "evidence": {
            "kind": kind, "source": f"unit fixture {name}",
            "source_sha256": sha256(raw).hexdigest(), "availability_policy": "bar_end_ns",
            "corporate_action_policy": "action_free_fixture",
        },
    }


def _export(tmp_path, workflow: str = "historical_experiment", *, kind: str = "historical") -> dict[str, bytes]:
    raw = [_bars(day) for day in (1, 2, 3)]
    windows = [
        _window("train", "train", "low_vol", "train.csv", raw[0], kind=kind),
        _window("validation", "validation", "high_vol", "validation.csv", raw[1], kind=kind),
        _window("holdout", "test", "low_vol", "holdout.csv", raw[2], kind=kind),
    ]
    if workflow == "moving_average_comparison":
        windows = [windows[0], windows[2]]
    config = {
        "schema_version": 1, "workflow": workflow, "windows": windows,
        "candidates": [{
            "name": "sma", "type": "moving_average_regime", "symbol": "SPY",
            "parameters": {"fast_period": 2, "slow_period": 3, "quantity": 1},
        }],
        "experiment": {},
    }
    for window, content in zip(windows, (raw[0], raw[2]) if workflow == "moving_average_comparison" else raw,
                               strict=True):
        (tmp_path / window["data"]["path"]).write_bytes(content)
    config_path = tmp_path / "comparison.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output = run_comparison(config_path, tmp_path / "export")
    return {path.relative_to(output).as_posix(): path.read_bytes()
            for path in output.rglob("*") if path.is_file()}


def _dump(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _replace_json(files: dict[str, bytes], name: str, value: object) -> None:
    files[name] = _dump(value)
    complete = json.loads(files["complete.json"])
    complete["sha256"][name] = sha256(files[name]).hexdigest()
    files["complete.json"] = _dump(complete)


def _rehash_file(files: dict[str, bytes], name: str) -> None:
    complete = json.loads(files["complete.json"])
    complete["sha256"][name] = sha256(files[name]).hexdigest()
    files["complete.json"] = _dump(complete)


def test_historical_reader_returns_only_role_aware_catalog_metadata(tmp_path):
    files = _export(tmp_path)
    checked = verify_comparison_files(files)

    assert set(checked) == {
        "protected", "mixed", "known", "protocol_ids", "source_identities",
        "evidence", "selection", "holdout_evaluation",
    }
    assert checked["protected"] == {"inputs/window-002.csv"}
    assert checked["mixed"] == {
        "comparison_artifact.json", "submitted.json", "configuration.json", "manifest.json",
        "report.json", "summary.json", "comparison.csv",
    }
    assert checked["known"] == set(files)
    assert checked["evidence"] == "historical_declared"
    assert checked["selection"] == "no_selection"
    assert checked["holdout_evaluation"] == "not_evaluated"
    assert len(checked["protocol_ids"]) == len(checked["source_identities"]) == 1
    assert not any(key in checked for key in ("report", "metrics", "holdout_status", "holdout_reasons"))


def test_legacy_reader_recognizes_evaluated_holdout_without_inventing_protocol(tmp_path):
    files = _export(tmp_path, "moving_average_comparison")
    checked = verify_comparison_files(files)

    assert checked["protected"] == {"inputs/window-001.csv"}
    assert checked["protocol_ids"] == []
    assert checked["selection"] == "no_selection"
    assert checked["holdout_evaluation"] == "evaluated"


@pytest.mark.parametrize("mutation", ["checksum", "unknown", "holdout_label", "marker"])
def test_rehashed_inventory_and_declarations_cannot_bypass_derived_roles(tmp_path, mutation):
    files = _export(tmp_path)
    if mutation == "checksum":
        files["report.json"] += b" "
    elif mutation == "unknown":
        files["extra.json"] = b"{}\n"
        complete = json.loads(files["complete.json"])
        complete["sha256"]["extra.json"] = sha256(files["extra.json"]).hexdigest()
        files["complete.json"] = _dump(complete)
    elif mutation == "holdout_label":
        manifest = json.loads(files["manifest.json"])
        manifest["holdout_files"] = ["inputs/window-000.csv"]
        _replace_json(files, "manifest.json", manifest)
    else:
        marker = json.loads(files["comparison_artifact.json"])
        marker["catalog_status"] = "supported"
        _replace_json(files, "comparison_artifact.json", marker)

    with pytest.raises(ValueError):
        verify_comparison_files(files)


def test_rehashed_event_timestamp_cannot_relabel_a_run_to_another_window(tmp_path):
    files = _export(tmp_path)
    name = "runs/scenario-000000/equity.csv"
    rows = list(csv.reader(io.StringIO(files[name].decode()), strict=True))
    rows[1][0] = "0"
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    files[name] = stream.getvalue().encode()
    _rehash_file(files, name)

    with pytest.raises(ValueError, match="outside its mapped window"):
        verify_comparison_files(files)


def test_report_role_tampering_fails_even_when_report_and_completion_are_rehashed(tmp_path):
    files = _export(tmp_path)
    report = json.loads(files["report.json"])
    report["scenarios"][0]["role"] = "test"
    _replace_json(files, "report.json", report)

    with pytest.raises(ValueError, match="ordering or identity"):
        verify_comparison_files(files)


def test_stored_factory_hash_is_historical_evidence_not_current_source_gate(tmp_path):
    files = _export(tmp_path)
    report = json.loads(files["report.json"])
    report["candidates"][0]["factory_source_identity"] = "sha256:" + "0" * 64
    protocol = {
        "source_identity": report["source_identity"],
        "config": json.loads(report["config_json"]),
        "candidates": report["candidates"],
        "windows": report["windows"],
    }
    report["protocol_id"] = "sha256:" + sha256(
        json.dumps(protocol, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    _replace_json(files, "report.json", report)
    manifest = json.loads(files["manifest.json"])
    manifest["protocol_id"] = report["protocol_id"]
    _replace_json(files, "manifest.json", manifest)

    checked = verify_comparison_files(files)
    assert checked["protocol_ids"] == [report["protocol_id"]]


def test_synthetic_evidence_is_never_reported_as_historical(tmp_path):
    files = _export(tmp_path, kind="synthetic")
    checked = verify_comparison_files(files)
    assert checked["evidence"] == "synthetic"
    assert checked["selection"] == "no_selection"
