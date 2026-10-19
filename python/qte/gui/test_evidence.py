"""Private G8 evidence verification; no execution, catalog registration or HTTP.

Integrity is not authenticity. Explicit file identities establish only byte
equality, never complete dependency coverage or source-to-binary correspondence.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

from .artifact_io import FILE_LIMIT, METADATA_LIMIT, inventory, read_stable, validate_root
from .artifacts import strict_json
from .junit import JUnitReport, parse_junit
from .test_identity import compare_identity, validate_identity


class TestEvidenceError(ValueError):
    __test__ = False


FILES = frozenset({"manifest.json", "junit.xml", "log.txt", "complete.json"})
_HEX = re.compile(r"[0-9a-f]{64}")
_JOB = re.compile(r"[0-9a-f]{32}")
_MANIFEST_FIELDS = frozenset({
    "schema_version", "artifact_kind", "runner", "job_id", "repository_path",
    "argv", "cwd", "started_ns", "finished_ns", "exit_code", "termination",
    "versions", "identity_scope", "start_identity", "end_identity",
    "source_to_binary", "log_truncated",
})


def _require(condition):
    if not condition:
        raise TestEvidenceError("Test evidence is invalid or unsupported.")


def _repository_path(value):
    _require(type(value) is str and 1 < len(value) <= 1024)
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in value) and "\\" not in value)
    path = PurePosixPath(value)
    _require(path.is_absolute() and str(path) == value and ".." not in path.parts
             and not value.startswith("//"))
    return path


def fixed_test_command(repository_path: str, runner: str, job_id: str) -> tuple[str, ...]:
    """Describe the later fixed runner command; never execute manifest content."""
    root = _repository_path(repository_path)
    _require(type(job_id) is str and _JOB.fullmatch(job_id) is not None)
    _require(runner in {"pytest", "ctest"})
    output = root / "build/gui/jobs" / job_id
    junit = str(output / "artifacts/junit.xml")
    if runner == "ctest":
        # CTest presets resolve relative report paths against the binary dir.
        return (str(root / ".venv/bin/ctest"), "--preset", "dev",
                "--output-on-failure", "--output-junit", junit)
    return (str(root / ".venv/bin/python"), "-m", "pytest", "tests/python",
            f"--junitxml={junit}", f"--basetemp={output / 'test-tmp'}",
            "-o", f"cache_dir={output / 'pytest-cache'}", "-o", "xfail_strict=true")


def _timestamp(value):
    _require(type(value) is str and re.fullmatch(r"0|[1-9][0-9]{0,18}", value) is not None)
    _require(int(value) < 2**63)
    return int(value)


def _manifest(value):
    _require(type(value) is dict and set(value) == _MANIFEST_FIELDS)
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["artifact_kind"] == "test_evidence_v1")
    command = fixed_test_command(value["repository_path"], value["runner"], value["job_id"])
    _require(type(value["argv"]) is list and value["argv"] == list(command)
             and value["cwd"] == value["repository_path"])
    _require(_timestamp(value["finished_ns"]) >= _timestamp(value["started_ns"]))
    code = value["exit_code"]
    _require(code is None or type(code) is int and -128 <= code <= 255)
    _require(value["termination"] in {"exited", "canceled", "timed_out", "interrupted"})
    _require(value["termination"] != "exited" or code is not None)
    versions = value["versions"]
    _require(type(versions) is dict and set(versions) == {"python", "pytest", "ctest", "platform"})
    for name, version in versions.items():
        _require(version is None or type(version) is str and 0 < len(version) <= 256
                 and all(ord(c) >= 32 and ord(c) != 127 for c in version))
        if name in {"python", "platform", value["runner"]}:
            _require(version is not None)
    _require(value["identity_scope"] == "explicit_files_v1"
             and value["source_to_binary"] == "unknown"
             and type(value["log_truncated"]) is bool)
    validate_identity(value["start_identity"])
    validate_identity(value["end_identity"])
    for identity in (value["start_identity"], value["end_identity"]):
        _require(all(identity[group] for group in ("source", "tests", "binaries", "build")))
    return value


def _outcome(manifest: dict, report: JUnitReport) -> str:
    """Terminal process outcome, not a job-success or current-code pass badge."""
    if manifest["termination"] != "exited":
        return manifest["termination"]
    code = manifest["exit_code"]
    failures = report.failed + report.errors
    if code < 0:
        return "interrupted"
    if code == 0:
        _require(failures == 0)
    elif manifest["runner"] == "pytest":
        _require(code in {1, 2, 3, 4, 5})
        if code == 1:
            _require(failures > 0)
            return "failed"
        if code == 2:
            if any(event.kind == "error" and event.message == "collection failure"
                   for case in report.cases for event in case.events):
                return "collection_failed"
            return "interrupted"
        if code in {3, 4}:
            return "runner_failed"
        # Module-level collection skips produce JUnit rows but no collected
        # tests: pytest legitimately exits 5 for that all-skipped report.
        _require(report.passed == 0 and failures == 0)
        return "skipped" if report.skipped else "empty"
    else:
        return "failed" if failures else "runner_failed"
    if report.tests == 0:
        return "empty"
    if report.passed == 0:
        return "skipped"
    return "passed_with_skips" if report.skipped else "passed"


@dataclass(frozen=True)
class VerifiedTestEvidence:
    manifest_bytes: bytes
    report: JUnitReport
    outcome: str
    identity_stability: tuple[tuple[str, str], ...]

    @property
    def manifest(self) -> dict:
        """Return an owned value, not mutable verification state."""
        return strict_json(self.manifest_bytes)

    def current_match(self, identity: dict | None) -> dict:
        return compare_identity(self.manifest["end_identity"], identity)

    @property
    def supports_passing_run(self) -> bool:
        """Only the recorded run/listed bytes, not current source or build lineage."""
        return (self.outcome in {"passed", "passed_with_skips"}
                and all(status == "same" for _, status in self.identity_stability))


def verify_test_evidence_files(files: dict[str, bytes]) -> VerifiedTestEvidence:
    """Validate an inert exact inventory. Failed/skipped evidence can be complete."""
    try:
        _require(type(files) is dict and set(files) == FILES)
        for name, data in files.items():
            limit = METADATA_LIMIT if name in {"manifest.json", "complete.json"} else FILE_LIMIT
            _require(type(data) is bytes and len(data) <= limit)
        marker = strict_json(files["complete.json"])
        _require(type(marker) is dict and set(marker) == {"schema_version", "sha256"}
                 and type(marker["schema_version"]) is int and marker["schema_version"] == 1)
        checksums = marker["sha256"]
        _require(type(checksums) is dict and set(checksums) == FILES - {"complete.json"})
        for name, digest in checksums.items():
            _require(type(digest) is str and _HEX.fullmatch(digest) is not None
                     and sha256(files[name]).hexdigest() == digest)
        manifest = _manifest(strict_json(files["manifest.json"]))
        report = parse_junit(files["junit.xml"], manifest["runner"])
        outcome = _outcome(manifest, report)
        stability = compare_identity(manifest["start_identity"], manifest["end_identity"])
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return VerifiedTestEvidence(canonical, report, outcome, tuple(sorted(stability.items())))
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise TestEvidenceError("Test evidence is invalid or unsupported.") from exc


def read_test_evidence(repository: Path, relative: str) -> VerifiedTestEvidence:
    """Read a caller-selected allowed artifact root, with no discovery or writes."""
    from .artifact_io import ArtifactIOError
    try:
        root = validate_root(repository, relative)
        before = inventory(root)
        _require(set(before) == FILES)
        files = {name: read_stable(root, name,
                 METADATA_LIMIT if name in {"manifest.json", "complete.json"} else FILE_LIMIT, snapshot)
                 for name, snapshot in before.items()}
        result = verify_test_evidence_files(files)
        _require(inventory(root) == before)
        return result
    except (ArtifactIOError, OSError, TestEvidenceError) as exc:
        raise TestEvidenceError("Test evidence is unavailable or invalid.") from exc
