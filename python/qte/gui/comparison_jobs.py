"""Private comparison job correspondence and role-aware publication gates."""
from __future__ import annotations

import copy

from qte.provenance import source_identity

from .artifact_io import inventory, validate_root
from .artifacts import inspect_artifact, strict_json
from .comparison_inputs import _canonical, _read_json, authorize_comparison_inputs, input_name
from .comparison_snapshots import load_comparison_snapshot
from .job_inputs import JobInputError


def _unavailable():
    return JobInputError("comparison_job_unavailable", 409, "Comparison job inputs or output are unavailable.")


def load_authorized_comparison(catalog, job_id, hashes, expected_source):
    """Do not trust a cached prepared object or serialized role lineage."""
    try:
        snapshot, prepared = load_comparison_snapshot(catalog.repository, job_id, hashes)
        if prepared.source_identity != expected_source or source_identity() != expected_source:
            raise _unavailable()
        lineage = authorize_comparison_inputs(catalog, prepared)
        return snapshot, prepared, lineage
    except Exception:
        raise _unavailable() from None


def verify_comparison_job_output(repository, job_id, expected_snapshot_hashes, expected_source_identity):
    """An unrelated, otherwise valid export is not this job's accepted result."""
    try:
        _, prepared = load_comparison_snapshot(repository, job_id, expected_snapshot_hashes)
        if prepared.source_identity != expected_source_identity or source_identity() != expected_source_identity:
            raise _unavailable()
        root = validate_root(repository, f"build/gui/jobs/{job_id}/artifacts")
        artifact = inspect_artifact(root)
        if artifact.kind != "comparison_export_v1" or artifact.metadata.get("integrity") != "verified":
            raise _unavailable()
        effective = strict_json(prepared.effective_config_bytes)
        submitted = _read_json(artifact, "submitted.json")
        configuration = _read_json(artifact, "configuration.json")
        manifest = _read_json(artifact, "manifest.json")
        copied = copy.deepcopy(effective)
        expected_inputs = {}
        for index, (window, source) in enumerate(zip(copied["windows"], prepared.sources, strict=True)):
            name = f"inputs/{input_name(index, source)}"
            window["data"]["path"] = name
            expected_inputs[name] = source.data_sha256
        if (_canonical(submitted) != prepared.effective_config_bytes
                or _canonical(configuration) != _canonical(copied)
                or manifest.get("source_identity") != expected_source_identity
                or manifest.get("input_sha256") != expected_inputs
                or any(artifact.metadata["file_hashes"].get(name) != digest for name, digest in expected_inputs.items())
                or inventory(root) != artifact.snapshots):
            raise _unavailable()
        return artifact
    except Exception:
        raise _unavailable() from None


def publish_comparison_job(catalog, job_id, hashes, expected_source):
    """Protect intrinsic mixed/test files before success, never all safe results."""
    try:
        load_authorized_comparison(catalog, job_id, hashes, expected_source)
        artifact = verify_comparison_job_output(catalog.repository, job_id, hashes, expected_source)
        catalog._validate_job_disclosure_storage()
        catalog._record_protection(artifact)
        if not all(catalog._job_input_protected(identity) for identity in artifact.protected_identities()):
            raise _unavailable()
        load_authorized_comparison(catalog, job_id, hashes, expected_source)
        if inventory(artifact.root) != artifact.snapshots:
            raise _unavailable()
    except Exception:
        raise _unavailable() from None
