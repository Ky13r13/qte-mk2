"""Pure captured-dataset validation and immutable validation artifacts."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from qte.adapters.common import AdapterResult
from qte.provenance import source_identity
from qte.research_config import _load_data, _preflight_capture, fields


_MANIFEST = "dataset_validation.json"
_BASE_FILES = frozenset({_MANIFEST, "configuration.json", "summary.json", "complete.json"})
_MAX_FILE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class PreparedData:
    configuration: dict[str, Any]
    loaded: AdapterResult
    data_sha256: str
    source_identity: str


def _data_section(configuration: dict[str, Any]) -> dict[str, Any]:
    fields(configuration, ("data",), ("data",))
    return fields(
        configuration["data"],
        ("adapter", "path", "interval_ns", "tick_size", "currency", "action_free", "schema", "expected_sha256"),
        ("adapter", "path", "interval_ns"),
    )


def capture_data_inputs(config_path: Path) -> tuple[dict[str, Any], bytes]:
    """Read one validation config and its referenced source exactly once each."""
    path = Path(config_path)
    configuration = json.loads(path.read_text(encoding="utf-8"))
    data = _data_section(configuration)
    _preflight_capture(data)
    return configuration, (path.parent / data["path"]).read_bytes()


def prepare_data(
    configuration: dict[str, Any], data_bytes: bytes, *, build_identity: str | None = None,
) -> PreparedData:
    """Validate captured bytes through the existing adapters and core preflight."""
    if type(data_bytes) is not bytes:
        raise TypeError("dataset data must be captured bytes")
    config = copy.deepcopy(configuration)
    data = _data_section(config)
    _preflight_capture(data)
    loaded = _load_data(data, data_bytes)
    identity = source_identity() if build_identity is None else build_identity
    return PreparedData(config, loaded, hashlib.sha256(data_bytes).hexdigest(), identity)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _values(prepared: PreparedData, copied_name: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    dataset = prepared.loaded.dataset
    provenance = asdict(prepared.loaded.provenance)
    provenance["assumptions"] = list(provenance["assumptions"])
    configuration = copy.deepcopy(prepared.configuration)
    configuration["data"]["path"] = copied_name
    manifest = {
        "schema_version": 1,
        "artifact_kind": "dataset_validation_v1",
        "source_identity": prepared.source_identity,
        "data_sha256": prepared.data_sha256,
        "dataset_hash": dataset.hash,
        "adapter": prepared.configuration["data"]["adapter"],
    }
    summary = {
        "dataset_start_ns": dataset.start_ns,
        "dataset_end_ns": dataset.end_ns,
        "interval_ns": dataset.interval_ns,
        "bar_count": dataset.bar_count,
        "gap_count": dataset.gap_count,
        "symbols": list(dataset.symbols),
        "currency": dataset.currency,
        "source_id": dataset.source_id,
        "provenance": provenance,
        "availability": {
            "timestamp_basis": "UTC",
            "bar_interval": "fixed",
            "available_at": "bar_end_ns",
            "exchange_calendar_validation": False,
            "gap_repair": False,
            "bar_filling": False,
        },
        "assumptions": [
            "Timestamps are explicit UTC instants and bars have the configured fixed interval.",
            "A bar is available only at its end_ns; no exchange calendar or session semantics are inferred.",
            "Observed gaps are reported without repair or bar filling.",
            *provenance["assumptions"],
        ],
    }
    return manifest, configuration, summary


def write_dataset_output(config_path: Path, output: Path) -> Path:
    configuration, raw = capture_data_inputs(config_path)
    prepared = prepare_data(configuration, raw)
    copied_name = "input.csv" if configuration["data"]["adapter"] == "csv" else "input.json"
    manifest, copied_config, summary = _values(prepared, copied_name)
    files = {
        _MANIFEST: _json_bytes(manifest),
        "configuration.json": _json_bytes(copied_config),
        "summary.json": _json_bytes(summary),
        copied_name: raw,
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        with (output / name).open("xb") as stream:
            stream.write(content)
    complete = {"schema_version": 1, "sha256": {
        name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())
    }}
    with (output / "complete.json").open("xb") as stream:
        stream.write(_json_bytes(complete))
    return output


def _json_file(files: Mapping[str, bytes], name: str) -> Any:
    raw = files[name]
    if type(raw) is not bytes:
        raise ValueError("artifact files must be captured bytes")
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("dataset validation JSON contains a duplicate key")
            result[key] = value
        return result

    def invalid_constant(_: str) -> None:
        raise ValueError("dataset validation JSON contains a nonfinite number")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("dataset validation JSON is invalid") from error
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > 64:
            raise ValueError("dataset validation JSON exceeds the nesting limit")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values()
                           if isinstance(item, (dict, list)))
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current
                           if isinstance(item, (dict, list)))
    return value


def verify_dataset_files(files: Mapping[str, bytes]) -> dict[str, Any]:
    """Strictly verify one stable, already-captured dataset artifact mapping."""
    if not isinstance(files, Mapping) or any(type(name) is not str for name in files):
        raise ValueError("artifact files must be a mapping")
    if any(type(content) is not bytes for content in files.values()):
        raise ValueError("artifact files must be captured bytes")
    names = set(files)
    if not _BASE_FILES.issubset(names):
        raise ValueError("dataset validation artifact is incomplete")
    inputs = names - _BASE_FILES
    if len(inputs) != 1 or next(iter(inputs)) not in ("input.csv", "input.json"):
        raise ValueError("dataset validation artifact has an invalid inventory")
    input_name = next(iter(inputs))
    complete = _json_file(files, "complete.json")
    expected_names = names - {"complete.json"}
    if (not isinstance(complete, dict) or set(complete) != {"schema_version", "sha256"}
            or type(complete["schema_version"]) is not int or complete["schema_version"] != 1
            or not isinstance(complete["sha256"], dict)
            or set(complete["sha256"]) != expected_names):
        raise ValueError("dataset validation completion inventory is invalid")
    for name in expected_names:
        digest = complete["sha256"].get(name)
        if (not isinstance(digest, str) or hashlib.sha256(files[name]).hexdigest() != digest):
            raise ValueError("dataset validation checksum mismatch")
    manifest = _json_file(files, _MANIFEST)
    configuration = _json_file(files, "configuration.json")
    summary = _json_file(files, "summary.json")
    data = configuration.get("data") if isinstance(configuration, dict) else None
    if not isinstance(data, dict) or data.get("path") != input_name:
        raise ValueError("dataset validation configuration does not identify its captured input")
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or not isinstance(manifest.get("source_identity"), str)
            or not manifest["source_identity"].startswith("sha256:")
            or len(manifest["source_identity"]) != 71
            or any(char not in "0123456789abcdef" for char in manifest["source_identity"][7:])):
        raise ValueError("dataset validation manifest shape is invalid")
    prepared = prepare_data(
        configuration, files[input_name], build_identity=manifest["source_identity"],
    )
    replay_manifest, replay_configuration, replay_summary = _values(prepared, input_name)
    if (files[_MANIFEST] != _json_bytes(replay_manifest)
            or files["configuration.json"] != _json_bytes(replay_configuration)
            or files["summary.json"] != _json_bytes(replay_summary)):
        raise ValueError("dataset validation artifact does not match canonical replay")
    return {"manifest": manifest, "configuration": configuration, "summary": summary}


def verify_dataset_output(root: Path) -> dict[str, Any]:
    """Capture and verify a directory with no extra, missing, or symlinked files."""
    root = Path(root)
    first = sorted(entry.name for entry in os.scandir(root))
    if len(first) > 5:
        raise ValueError("dataset validation artifact has too many files")
    captured: dict[str, bytes] = {}
    for name in first:
        path = root / name
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_FILE_BYTES:
            raise ValueError("dataset validation artifact contains an invalid file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise ValueError("dataset validation artifact changed during capture") from error
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or
                    (opened.st_dev, opened.st_ino, opened.st_size, opened.st_ctime_ns)
                    != (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns)):
                raise ValueError("dataset validation artifact changed during capture")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read(_MAX_FILE_BYTES + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        final_path = path.lstat()
        if len(content) != before.st_size or len(content) > _MAX_FILE_BYTES or (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) or (
                final_path.st_dev, final_path.st_ino, final_path.st_size,
                final_path.st_mtime_ns, final_path.st_ctime_ns
        ) != (opened.st_dev, opened.st_ino, opened.st_size,
              opened.st_mtime_ns, opened.st_ctime_ns):
            raise ValueError("dataset validation artifact changed during capture")
        captured[name] = content
    if first != sorted(entry.name for entry in os.scandir(root)):
        raise ValueError("dataset validation artifact changed during capture")
    return verify_dataset_files(captured)
