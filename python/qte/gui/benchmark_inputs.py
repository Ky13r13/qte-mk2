"""Bounded synthetic benchmark requests and immutable private snapshots.

Reuse the conservative Python test preflight's explicit file/runtime scope.
This only captures identities: it does not run tests or establish build provenance.
"""
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re

from .artifacts import strict_json
from .job_inputs import _job_inputs_fd, _write_exclusive
from .test_preflight import (
    _canonical, _repository_text, prepare_test_run, validate_prepared as validate_runtime,
)
from .test_snapshots import _key, _read


MAX_BARS = 100_000
FILES = frozenset({"benchmark-request.json", "input-manifest.json"})


@dataclass(frozen=True)
class PreparedBenchmark:
    payload: bytes

    @property
    def value(self):
        return strict_json(self.payload)


def validate_parameters(bars, strategy):
    if (type(bars) is not int or not 1 <= bars <= MAX_BARS
            or type(strategy) is not str or strategy not in {"hold", "sma"}):
        raise ValueError("Invalid bounded benchmark workload.")


def validate_prepared(value):
    if not isinstance(value, PreparedBenchmark) or type(value.payload) is not bytes or len(value.payload) > 1024 * 1024:
        raise ValueError("Invalid benchmark request.")
    data = value.value
    if type(data) is not dict or set(data) != {"schema_version", "bars", "strategy", "runtime"}:
        raise ValueError("Invalid benchmark request.")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Invalid benchmark request.")
    validate_parameters(data["bars"], data["strategy"])
    runtime = validate_runtime(data["runtime"])
    if runtime.value["runner"] != "pytest":
        raise ValueError("Invalid benchmark runtime scope.")
    return PreparedBenchmark(_canonical({**data, "runtime": runtime.value}))


def prepare_benchmark(repository, bars, strategy):
    validate_parameters(bars, strategy)
    return validate_prepared(PreparedBenchmark(_canonical({
        "schema_version": 1, "bars": bars, "strategy": strategy,
        "runtime": prepare_test_run(repository, "pytest").value,
    })))


def recheck_prepared(repository, prepared):
    prepared = validate_prepared(prepared)
    if prepare_benchmark(repository, prepared.value["bars"], prepared.value["strategy"]) != prepared:
        raise ValueError("Benchmark scope changed.")
    return prepared


def fixed_command(repository, job_id, bars, strategy):
    repository = _repository_text(str(repository))
    if type(job_id) is not str or re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
        raise ValueError("Invalid benchmark identity.")
    validate_parameters(bars, strategy)
    return (f"{repository}/.venv/bin/python", f"{repository}/scripts/benchmark_replay.py",
            "--bars", str(bars), "--strategy", strategy)


def _manifest(job_id, prepared):
    return _canonical({"schema_version": 5, "kind": "benchmark", "job_id": job_id,
                       "request_sha256": sha256(prepared.payload).hexdigest()}) + b"\n"


def _bound(repository, job_id, prepared):
    data = prepared.value
    fixed_command(repository, job_id, data["bars"], data["strategy"])
    if data["runtime"]["repository_path"] != str(repository):
        raise ValueError("Benchmark repository mismatch.")


def _snapshot(job_id, prepared, manifest):
    digest = sha256(prepared.payload).hexdigest()
    return {"job_id": job_id, "request_sha256": digest,
            "request_path": f"build/gui/jobs/{job_id}/inputs/benchmark-request.json",
            "hashes": {"request_sha256": digest},
            "snapshot_hashes": {"benchmark-request.json": digest,
                                "input-manifest.json": sha256(manifest).hexdigest()}}


def write_snapshot(repository, job_id, prepared):
    prepared = validate_prepared(prepared)
    _bound(repository, job_id, prepared)
    recheck_prepared(repository, prepared)
    manifest = _manifest(job_id, prepared)
    descriptors, directory = _job_inputs_fd(Path(repository), job_id, create=True)
    try:
        _write_exclusive(directory, "benchmark-request.json", prepared.payload)
        _write_exclusive(directory, "input-manifest.json", manifest)
        os.fchmod(directory, 0o500)
        for descriptor in reversed(descriptors):
            os.fsync(descriptor)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return _snapshot(job_id, prepared, manifest)


def load_snapshot(repository, job_id, hashes):
    if (type(hashes) is not dict or set(hashes) != FILES
            or any(type(v) is not str or re.fullmatch(r"[0-9a-f]{64}", v) is None for v in hashes.values())):
        raise ValueError("Invalid benchmark snapshot.")
    descriptors, reopened = [], []
    try:
        descriptors, directory = _job_inputs_fd(Path(repository), job_id, create=False)
        if set(os.listdir(directory)) != FILES:
            raise ValueError("Invalid benchmark inventory.")
        values, states = {}, {}
        for name in sorted(FILES):
            values[name], states[name] = _read(directory, name)
            if sha256(values[name]).hexdigest() != hashes[name]:
                raise ValueError("Benchmark snapshot changed.")
        prepared = validate_prepared(PreparedBenchmark(values["benchmark-request.json"]))
        _bound(repository, job_id, prepared)
        if (values["benchmark-request.json"] != prepared.payload
                or values["input-manifest.json"] != _manifest(job_id, prepared)
                or set(os.listdir(directory)) != FILES
                or any(_key(os.stat(name, dir_fd=directory, follow_symlinks=False)) != states[name] for name in FILES)):
            raise ValueError("Benchmark snapshot changed.")
        reopened, current = _job_inputs_fd(Path(repository), job_id, create=False)
        if _key(os.fstat(current)) != _key(os.fstat(directory)):
            raise ValueError("Benchmark snapshot changed.")
        return _snapshot(job_id, prepared, values["input-manifest.json"]), prepared
    finally:
        for descriptor in reversed(reopened + descriptors):
            os.close(descriptor)


def recheck_snapshot(repository, job_id, hashes):
    result = load_snapshot(repository, job_id, hashes)
    recheck_prepared(repository, result[1])
    if load_snapshot(repository, job_id, hashes) != result:
        raise ValueError("Benchmark snapshot changed.")
    return result
