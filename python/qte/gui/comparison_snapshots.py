"""Strict version-3 multi-input snapshots; not a job launch or authorization API."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path

from qte.comparison_config import prepare_comparison

from .artifacts import strict_json
from .comparison_inputs import (
    ComparisonSource, PreparedComparisonInputs, TOTAL_DATA_LIMIT, _build_prepared,
    _canonical, _hex, _object, _source_record, input_name, validate_prepared,
)
from .job_inputs import CONFIG_LIMIT, DATA_LIMIT, JobInputError, _error, _job_inputs_fd, _read_regular, _write_exclusive


_FIXED = {"submitted-comparison.json", "comparison-config.json", "effective-config.json", "input-manifest.json"}
_SOURCE_KEYS = {"validation_id", "validation_identity", "validation_path", "source_path", "adapter",
                "data_configuration", "data_sha256", "identities", "input_name"}


@dataclass(frozen=True)
class ComparisonSnapshotInputs:
    job_id: str
    kind: str
    input_root: str
    effective_config_path: str
    data_paths: tuple[str, ...]
    hashes: dict[str, str]
    snapshot_hashes: dict[str, str]
    source_identity: str
    fingerprint: str


def _validated(value):
    value = validate_prepared(value)
    # Rebuild causal/data contracts too, not only a self-consistent hash list.
    prepare_comparison(strict_json(value.configuration_bytes), tuple(item.data_bytes for item in value.sources))
    return value


def _contents(value):
    return {"submitted-comparison.json": value.submitted_form_bytes,
            "comparison-config.json": value.configuration_bytes,
            "effective-config.json": value.effective_config_bytes,
            **{input_name(index, source): source.data_bytes for index, source in enumerate(value.sources)}}


def _manifest(job_id, value, hashes):
    return _canonical({"schema_version": 3, "kind": "comparison_run", "job_id": job_id,
                       "source_identity": value.source_identity, "fingerprint": value.fingerprint,
                       "sources": [_source_record(index, source) for index, source in enumerate(value.sources)],
                       "hashes": hashes}) + b"\n"


def _result(job_id, value, hashes):
    root = f"build/gui/jobs/{job_id}/inputs"
    return ComparisonSnapshotInputs(job_id, "comparison_run", root, f"{root}/effective-config.json",
        tuple(f"{root}/{input_name(index, source)}" for index, source in enumerate(value.sources)),
        {"submitted_configuration_sha256": hashes["submitted-comparison.json"],
         "canonical_config_sha256": hashes["comparison-config.json"],
         "effective_config_sha256": hashes["effective-config.json"],
         "input_manifest_sha256": hashes["input-manifest.json"]},
        dict(hashes), value.source_identity, value.fingerprint)


def write_comparison_snapshot(repository: Path, job_id: str, prepared: PreparedComparisonInputs) -> ComparisonSnapshotInputs:
    """Validate before mkdir, then copy only captured bytes to a fresh private root.

    The caller must separately reserve/recheck roles at actual job acceptance.
    This primitive starts no job and creates no public artifact or disclosure.
    """
    try:
        value = _validated(prepared)
        contents = _contents(value)
        hashes = {name: sha256(raw).hexdigest() for name, raw in contents.items()}
        manifest = _manifest(job_id, value, hashes)
    except (JobInputError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise _error("invalid") from None
    descriptors, directory = _job_inputs_fd(Path(repository), job_id, create=True)
    try:
        for name, raw in sorted(contents.items()):
            _write_exclusive(directory, name, raw)
        # Last file: a partial write is not a valid snapshot and is never reused.
        _write_exclusive(directory, "input-manifest.json", manifest)
        hashes["input-manifest.json"] = sha256(manifest).hexdigest()
        os.fsync(directory)
        os.fchmod(directory, 0o500)
        return _result(job_id, value, hashes)
    except OSError:
        raise _error("snapshot") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def verify_comparison_snapshot(repository: Path, job_id: str, expected_snapshot_hashes: dict[str, str]) -> ComparisonSnapshotInputs:
    """Retain the snapshot-only interface for existing callers."""
    return load_comparison_snapshot(repository, job_id, expected_snapshot_hashes)[0]


def load_comparison_snapshot(repository: Path, job_id: str, expected_snapshot_hashes: dict[str, str]) -> tuple[ComparisonSnapshotInputs, PreparedComparisonInputs]:
    """Privately reconstruct authorized records from independently pinned bytes."""
    descriptors = []
    try:
        if (type(expected_snapshot_hashes) is not dict or not 6 <= len(expected_snapshot_hashes) <= 28
                or any(type(name) is not str for name in expected_snapshot_hashes)):
            raise ValueError
        expected = {name: _hex(digest) for name, digest in expected_snapshot_hashes.items()}
        if not _FIXED <= set(expected):
            raise ValueError
        data_names = set(expected) - _FIXED
        # Names are generated, never supplied as paths by the browser/manifest.
        possible = {f"window-{index:03d}.{suffix}" for index in range(24) for suffix in ("csv", "json")}
        if not data_names <= possible:
            raise ValueError
        descriptors, directory = _job_inputs_fd(Path(repository), job_id, create=False)
        if set(os.listdir(directory)) != set(expected):
            raise ValueError
        contents, total = {}, 0
        for name in sorted(expected):
            limit = DATA_LIMIT if name in data_names else CONFIG_LIMIT + (1 if name == "input-manifest.json" else 0)
            raw = _read_regular(Path(repository), f"build/gui/jobs/{job_id}/inputs/{name}", limit)
            if name in data_names:
                total += len(raw)
                if total > TOTAL_DATA_LIMIT:
                    raise ValueError
            if sha256(raw).hexdigest() != expected[name]:
                raise ValueError
            contents[name] = raw
        manifest = _object(strict_json(contents["input-manifest.json"]),
                           {"schema_version", "kind", "job_id", "source_identity", "fingerprint", "sources", "hashes"})
        if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 3
                or manifest["job_id"] != job_id or manifest["kind"] != "comparison_run"
                or type(manifest["sources"]) is not list or len(manifest["sources"]) != len(data_names)
                or type(manifest["hashes"]) is not dict
                or manifest["hashes"] != {name: digest for name, digest in expected.items() if name != "input-manifest.json"}):
            raise ValueError
        sources = []
        for index, record in enumerate(manifest["sources"]):
            _object(record, _SOURCE_KEYS)
            name = f"window-{index:03d}.{'csv' if record['adapter'] == 'csv' else 'json'}"
            if record["input_name"] != name or name not in data_names or type(record["identities"]) is not list:
                raise ValueError
            sources.append(ComparisonSource(record["validation_id"], record["validation_identity"], record["validation_path"],
                record["source_path"], record["adapter"], _canonical(record["data_configuration"]), contents[name],
                record["data_sha256"], tuple(record["identities"])))
        value = _validated(_build_prepared(contents["submitted-comparison.json"], tuple(sources), manifest["source_identity"]))
        if (_contents(value) != {name: raw for name, raw in contents.items() if name != "input-manifest.json"}
                or value.fingerprint != manifest["fingerprint"]
                or _manifest(job_id, value, manifest["hashes"]) != contents["input-manifest.json"]):
            raise ValueError
        return _result(job_id, value, expected), value
    except (JobInputError, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise _error("snapshot") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
