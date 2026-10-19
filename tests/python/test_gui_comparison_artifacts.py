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
            "kind": kind, "source": f"caller-declared invented unit fixture {name}; not real historical data",
            "source_sha256": sha256(raw).hexdigest(), "availability_policy": "bar_end_ns",
            "corporate_action_policy": "action_free_fixture",
        },
    }


def _export(tmp_path, workflow: str = "historical_experiment", *, kind: str = "historical",
            bar_factory=_bars, candidates=None, experiment=None, holdout_regime="low_vol",
            validation_windows=1) -> dict[str, bytes]:
    raw = [bar_factory(day) for day in range(1, validation_windows + 3)]
    windows = [
        _window("train", "train", "low_vol", "train.csv", raw[0], kind=kind),
        *[_window("validation" if index == 1 else f"validation-{index}", "validation", "high_vol",
                  f"validation-{index}.csv", raw[index], kind=kind)
          for index in range(1, validation_windows + 1)],
        _window("holdout", "test", holdout_regime, "holdout.csv", raw[-1], kind=kind),
    ]
    if workflow == "moving_average_comparison":
        windows = [windows[0], windows[-1]]
    config = {
        "schema_version": 1, "workflow": workflow, "windows": windows,
        "candidates": candidates if candidates is not None else [{
            "name": "sma", "type": "moving_average_regime", "symbol": "SPY",
            "parameters": {"fast_period": 2, "slow_period": 3, "quantity": 1},
        }],
        "experiment": experiment if experiment is not None else {},
    }
    for window, content in zip(windows, (raw[0], raw[-1]) if workflow == "moving_average_comparison" else raw,
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
        "complete.json",
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


def _round_trip_bars(day):
    # Invented caller-declaration unit fixture, not historical research evidence.
    prices = ((100, 100), (101, 101), (103, 103), (104, 110), (108, 108), (104, 104), (105, 105))
    rows = ["symbol,timestamp,open,high,low,close,volume"]
    for index, (open_, close) in enumerate(prices):
        rows.append(f"SPY,2024-01-{day:02d}T00:{index:02d}:00Z,{open_},"
                    f"{max(open_, close) + 1},{min(open_, close) - 1},{close},1000")
    return ("\n".join(rows) + "\n").encode()


@pytest.mark.parametrize("regime,status,reasons", [
    ("high_vol", "passed_research_screen_only", []),
    ("low_vol", "failed", ["insufficient_regime_coverage"]),
])
def test_selected_candidate_holdout_outputs_stay_protected(tmp_path, regime, status, reasons):
    files = _export(tmp_path, bar_factory=_round_trip_bars,
                    experiment={"min_closed_trades": 1, "min_validation_windows": 1,
                                "required_regimes": ["high_vol"]}, holdout_regime=regime)
    checked = verify_comparison_files(files)
    report = json.loads(files["report.json"])
    assert report["selected_candidate"] == "sma"
    assert report["holdout_status"] == status
    assert report["holdout_reasons"] == reasons
    assert len(report["scenarios"]) == 6
    assert all(row["status"] == "ok" and row["trade_count"] == 1 and row["fill_count"] == 2
               for row in report["scenarios"])
    assert checked["protected"] == {"inputs/window-002.csv"} | {
        f"runs/scenario-{index:06d}/{name}" for index in (4, 5)
        for name in ("equity.csv", "fills.csv", "orders.csv")}
    from qte.gui.artifacts import ArtifactError
    from qte.gui.catalog import Catalog
    target = tmp_path / "build" / "comparison"
    target.parent.mkdir()
    (tmp_path / "export").rename(target)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    assert item["status"]["integrity"] == "verified"
    assert item["status"]["holdout_evaluation"] == "unknown"
    for name in checked["protected"]:
        with pytest.raises(ArtifactError) as denied:
            catalog.read_file(item["id"], sha256(name.encode()).hexdigest())
        assert denied.value.code == "holdout_protected"


def test_failed_trial_keeps_successful_mapping_indexes(tmp_path, monkeypatch):
    import qte
    actual = qte.BacktestEngine.run
    attempts = 0
    def fail_once(engine, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("deliberate reader failure fixture")
        return actual(engine, *args, **kwargs)
    monkeypatch.setattr(qte.BacktestEngine, "run", fail_once)
    files = _export(tmp_path)
    checked = verify_comparison_files(files)
    report, manifest = (json.loads(files[name]) for name in ("report.json", "manifest.json"))
    assert report["scenarios"][0]["status"] == "error"
    assert len(report["scenarios"]) == 4
    assert [item["index"] for item in manifest["scenario_files"]] == [0, 1, 2]
    assert manifest["scenario_files"][0]["cost_scenario"] == "stress"
    assert checked["known"] == set(files)


@pytest.mark.parametrize("workflow", ["historical_experiment", "moving_average_comparison"])
def test_rehashed_invalid_engine_snapshot_is_rejected(tmp_path, workflow):
    files = _export(tmp_path, workflow)
    report = json.loads(files["report.json"])
    report["scenarios"][0]["normalized_config"] = "garbage"
    _replace_json(files, "report.json", report)
    with pytest.raises(ValueError, match="normalized config"):
        verify_comparison_files(files)


@pytest.mark.parametrize("mutation", ["final_equity", "nonfinite", "bad_side", "bad_symbol", "bad_order_link", "negative_commission", "bad_status", "duplicate_id", "reversed_time"])
def test_rehashed_event_inconsistency_is_rejected(tmp_path, mutation):
    files = _export(tmp_path, bar_factory=_round_trip_bars)
    base = "equity.csv" if mutation in {"final_equity", "nonfinite", "reversed_time"} else (
        "orders.csv" if mutation in {"bad_status", "duplicate_id"} else "fills.csv")
    name = "runs/scenario-000000/" + base
    rows = list(csv.reader(io.StringIO(files[name].decode())))
    if mutation == "final_equity": rows[-1][1] = "1234567.89"
    elif mutation == "nonfinite": rows[1][1] = "inf"
    elif mutation == "bad_side": rows[1][3] = "NOT_A_SIDE"
    elif mutation == "bad_symbol": rows[1][2] = "QQQ"
    elif mutation == "bad_order_link": rows[1][1] = "99"
    elif mutation == "negative_commission": rows[1][7] = "-1"
    elif mutation == "bad_status": rows[1][2] = "NOT_A_STATUS"
    elif mutation == "duplicate_id": rows[2][0] = rows[1][0]
    else: rows[-1][0] = rows[1][0]
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    files[name] = stream.getvalue().encode()
    _rehash_file(files, name)
    with pytest.raises(ValueError):
        verify_comparison_files(files)


@pytest.mark.parametrize("workflow", ["historical_experiment", "moving_average_comparison"])
@pytest.mark.parametrize("mutation", ["chronology", "currency", "symbol"])
def test_data_window_contracts_are_checked_before_trusting_report(tmp_path, workflow, mutation):
    files = _export(tmp_path, workflow)
    submitted, effective = (json.loads(files[name]) for name in ("submitted.json", "configuration.json"))
    name = "inputs/window-001.csv"
    if mutation == "currency":
        for config in (submitted, effective): config["windows"][1]["data"]["currency"] = "EUR"
        message = "currency"
    else:
        raw = files["inputs/window-000.csv"] if mutation == "chronology" else files[name].replace(b"SPY", b"QQQ")
        files[name] = raw
        _rehash_file(files, name)
        for config in (submitted, effective):
            config["windows"][1]["evidence"]["source_sha256"] = sha256(raw).hexdigest()
        message = "chronological|non-overlapping" if mutation == "chronology" else "symbol"
    _replace_json(files, "submitted.json", submitted)
    _replace_json(files, "configuration.json", effective)
    with pytest.raises(ValueError, match=message):
        verify_comparison_files(files)


def test_huge_metric_is_a_validation_error_not_overflow(tmp_path):
    files = _export(tmp_path)
    report = json.loads(files["report.json"])
    report["scenarios"][0]["total_return"] = 10**400
    _replace_json(files, "report.json", report)
    with pytest.raises(ValueError, match="finite"):
        verify_comparison_files(files)


def test_completion_uses_existing_catalog_limits_not_a_smaller_file_cap():
    from qte.gui.comparison_artifacts import _verify_completion
    from qte.gui.artifact_io import METADATA_LIMIT
    files = {f"files/{index}.csv": b"data\n" for index in range(513)}
    files["complete.json"] = _dump({"schema_version": 1, "sha256": {
        name: sha256(raw).hexdigest() for name, raw in files.items()}})
    assert len(_verify_completion(files)) == 513
    files["configuration.json"] = b" " * (METADATA_LIMIT + 1)
    with pytest.raises(ValueError, match="byte limits"):
        _verify_completion(files)


def test_maximum_supported_slate_fits_catalog_limits(tmp_path):
    from qte.gui.artifact_io import METADATA_LIMIT
    candidates = [{"name": f"sma-{index}", "type": "moving_average_regime", "symbol": "SPY",
                   "parameters": {"fast_period": 2, "slow_period": 3, "quantity": 1}}
                  for index in range(32)]
    files = _export(tmp_path, candidates=candidates, validation_windows=22, kind="synthetic")
    assert len(files) > 512
    assert len(files["report.json"]) > METADATA_LIMIT
    report = json.loads(files["report.json"])
    assert len(report["windows"]) == 24
    assert len(report["scenarios"]) == 32 * 23 * 2
    checked = verify_comparison_files(files)
    assert checked["known"] == set(files)
    assert checked["protected"] == {"inputs/window-023.csv"}
    assert checked["selection"] == "no_selection"
