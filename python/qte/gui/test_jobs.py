"""Private scheduler integration for fixed test requests; no HTTP/catalog API."""
from pathlib import Path

from .artifact_io import inventory, validate_root
from .test_evidence import read_test_evidence
from .test_snapshots import load_test_snapshot, recheck_test_snapshot


def snapshot_value(snapshot) -> dict:
    """Persist owned primitive metadata, not properties lost by asdict()."""
    return {"job_id": snapshot.job_id, "runner": snapshot.runner,
            "request_sha256": snapshot.request_sha256,
            "manifest_sha256": snapshot.manifest_sha256,
            "input_root": snapshot.input_root,
            "request_path": f"{snapshot.input_root}/test-request.json",
            "hashes": {"request_sha256": snapshot.request_sha256},
            "snapshot_hashes": snapshot.snapshot_hashes}


def verify_test_job_output(repository: Path, job_id: str, hashes: dict, request_identity: str):
    """Parent acceptance gate: a child success message alone proves nothing."""
    snapshot, prepared = load_test_snapshot(repository, job_id, hashes)
    if request_identity != "sha256:" + snapshot.request_sha256:
        raise ValueError("Test request changed.")
    relative = f"build/gui/jobs/{job_id}/artifacts"
    root = validate_root(repository, relative)
    before = inventory(root)
    evidence = read_test_evidence(repository, relative)
    manifest, accepted = evidence.manifest, prepared.value
    if (manifest["job_id"] != job_id or manifest["repository_path"] != str(repository)
            or manifest["runner"] != accepted["runner"]
            or manifest["versions"] != accepted["versions"]
            or manifest["start_identity"] != accepted["identity"]
            or manifest["end_identity"] != accepted["identity"]
            or not evidence.supports_passing_run
            or manifest["termination"] != "exited" or manifest["exit_code"] != 0):
        raise ValueError("Test evidence does not match its accepted request.")
    if recheck_test_snapshot(repository, job_id, hashes) != (snapshot, prepared):
        raise ValueError("Test request changed.")
    if read_test_evidence(repository, relative) != evidence or inventory(root) != before:
        raise ValueError("Test evidence changed.")
    return evidence
