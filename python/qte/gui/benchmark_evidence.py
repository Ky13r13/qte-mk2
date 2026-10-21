"""Completion-last synthetic timing evidence, bound to a pinned GUI request."""
from hashlib import sha256
import os

from qte.benchmark import validate_measurement
from .artifact_io import METADATA_LIMIT, inventory, read_stable, validate_root
from .artifacts import strict_json
from .benchmark_inputs import _canonical, fixed_command, load_snapshot, recheck_snapshot
from .test_identity import compare_identity
from .test_preflight import validate_prepared as validate_runtime
from .test_evidence_writer import (
    _artifacts_fd, _names, _write_exclusive, _read_regular, _check_states, _check_path,
)


FILES = frozenset({"manifest.json", "complete.json"})
FIELDS = frozenset({"schema_version", "artifact_kind", "job_id", "repository_path", "argv",
                    "request_sha256", "started_ns", "finished_ns", "start_runtime", "end_runtime",
                    "source_to_binary", "measurement"})


def validate_manifest(value):
    if (type(value) is not dict or set(value) != FIELDS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["artifact_kind"] != "benchmark_evidence_v1"
            or value["source_to_binary"] != "unknown"):
        raise ValueError("Invalid benchmark evidence.")
    measurement = validate_measurement(value["measurement"])
    argv = fixed_command(value["repository_path"], value["job_id"], measurement["bars"], measurement["strategy"])
    if value["argv"] != list(argv):
        raise ValueError("Invalid benchmark command.")
    digest = value["request_sha256"]
    if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid request identity.")
    for field in ("started_ns", "finished_ns"):
        text = value[field]
        if type(text) is not str or not text.isascii() or not text.isdecimal() or len(text) > 20 or str(int(text)) != text:
            raise ValueError("Invalid benchmark timestamp.")
    if int(value["finished_ns"]) < int(value["started_ns"]):
        raise ValueError("Reversed benchmark timestamps.")
    for field in ("start_runtime", "end_runtime"):
        runtime = validate_runtime(value[field]).value
        if runtime["runner"] != "pytest" or runtime["repository_path"] != value["repository_path"]:
            raise ValueError("Invalid benchmark runtime.")
    if measurement["machine"]["python"] != value["start_runtime"]["versions"]["python"]:
        raise ValueError("Benchmark runtime version mismatch.")
    return strict_json(_canonical(value))


def _verify(files):
    if set(files) != FILES or any(type(v) is not bytes or len(v) > METADATA_LIMIT for v in files.values()):
        raise ValueError("Invalid benchmark files.")
    marker = {"schema_version": 1, "sha256": {"manifest.json": sha256(files["manifest.json"]).hexdigest()}}
    if strict_json(files["complete.json"]) != marker or files["complete.json"] != _canonical(marker):
        raise ValueError("Invalid benchmark completion.")
    return validate_manifest(strict_json(files["manifest.json"]))


def read_evidence(repository, job_id):
    # Validate the job ID before traversing the path.
    fixed_command(repository, job_id, 1, "hold")
    relative = f"build/gui/jobs/{job_id}/artifacts"
    root = validate_root(repository, relative)
    before = inventory(root)
    if set(before) != FILES:
        raise ValueError("Invalid benchmark inventory.")
    files = {name: read_stable(root, name, METADATA_LIMIT, before[name]) for name in sorted(FILES)}
    value = _verify(files)
    if (value["repository_path"] != str(repository) or value["job_id"] != job_id
            or inventory(validate_root(repository, relative)) != before):
        raise ValueError("Benchmark evidence changed.")
    return value


def write_evidence(repository, job_id, manifest):
    # Freeze caller-owned data before writing anything.
    value = validate_manifest(manifest)
    if value["repository_path"] != str(repository) or value["job_id"] != job_id:
        raise ValueError("Benchmark evidence identity mismatch.")
    encoded = _canonical(value)
    files = {"manifest.json": encoded, "complete.json": _canonical({
        "schema_version": 1, "sha256": {"manifest.json": sha256(encoded).hexdigest()},
    })}
    _verify(files)
    descriptors, directory = _artifacts_fd(repository, job_id)
    try:
        if _names(directory):
            raise ValueError("Benchmark output already exists.")
        states = {}
        for name in ("manifest.json", "complete.json"):
            if states:
                _check_states(directory, states)
            _check_path(repository, job_id, directory)
            _write_exclusive(directory, name, files[name])
            data, states[name] = _read_regular(directory, name, METADATA_LIMIT)
            if data != files[name]:
                raise ValueError("Benchmark evidence changed.")
        _check_states(directory, states)
        _check_path(repository, job_id, directory)
        os.fsync(directory)
        if read_evidence(repository, job_id) != value:
            raise ValueError("Benchmark evidence changed.")
        return value
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def bound_evidence(repository, job_id, hashes, request_identity):
    snapshot, prepared = load_snapshot(repository, job_id, hashes)
    value = read_evidence(repository, job_id)
    accepted = prepared.value
    if (request_identity != "sha256:" + snapshot["request_sha256"]
            or value["request_sha256"] != snapshot["request_sha256"]
            or value["start_runtime"] != accepted["runtime"]
            or value["measurement"]["bars"] != accepted["bars"]
            or value["measurement"]["strategy"] != accepted["strategy"]):
        raise ValueError("Benchmark evidence does not match its accepted request.")
    if (load_snapshot(repository, job_id, hashes) != (snapshot, prepared)
            or read_evidence(repository, job_id) != value):
        raise ValueError("Benchmark evidence changed.")
    return value


def verify_job_output(repository, job_id, hashes, request_identity):
    value = bound_evidence(repository, job_id, hashes, request_identity)
    _, prepared = recheck_snapshot(repository, job_id, hashes)
    if value["end_runtime"] != prepared.value["runtime"]:
        raise ValueError("Benchmark runtime changed.")
    if bound_evidence(repository, job_id, hashes, request_identity) != value:
        raise ValueError("Benchmark evidence changed.")
    return value


def projection(value):
    # Do not expose raw log text, file names, argv, paths, or captured source text.
    measurement = dict(value["measurement"])
    measurement.pop("source_identity")
    return {"state": "verified", "measurement": measurement,
            "started_ns": value["started_ns"], "finished_ns": value["finished_ns"],
            "identity_stability": compare_identity(value["start_runtime"]["identity"], value["end_runtime"]["identity"]),
            "versions_match": "same" if value["start_runtime"]["versions"] == value["end_runtime"]["versions"] else "different",
            "source_to_binary": "unknown", "current_match": "not_observed"}
