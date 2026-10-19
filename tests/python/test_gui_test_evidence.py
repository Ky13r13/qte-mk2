"""Completed evidence is not automatically a passing/current-source test run."""
from copy import deepcopy
from hashlib import sha256
import json
import xml.etree.ElementTree as ET

import pytest

from qte.gui.test_evidence import (
    TestEvidenceError, fixed_test_command, read_test_evidence, verify_test_evidence_files,
)


def xml(cases='<testcase name="works" classname="fixture" time="0"/>', *, tests=1,
        failures=0, errors=0, skipped=0, runner="pytest"):
    tree = ET.fromstring(f"<cases>{cases}</cases>")
    for case in tree:
        case.attrib.setdefault("classname", "fixture")
        if runner == "ctest":
            case.attrib.setdefault("status", "fail" if case.find("failure") is not None else "run")
    cases = "".join(ET.tostring(case, encoding="unicode") for case in tree)
    error_attribute = f'errors="{errors}" ' if runner == "pytest" else ""
    body = (f'<testsuite name="fixture" tests="{tests}" failures="{failures}" '
            f'{error_attribute}skipped="{skipped}" disabled="0">{cases}</testsuite>')
    return (f'<testsuites>{body}</testsuites>' if runner == "pytest" else body).encode()


def rehash(files):
    files["complete.json"] = json.dumps({"schema_version": 1, "sha256": {
        name: sha256(data).hexdigest() for name, data in files.items() if name != "complete.json"
    }}).encode()
    return files


def record(*, runner="pytest", junit=None, code=0, **changes):
    identity = {"source": {"python/qte/engine.py": "a" * 64},
                "tests": {"tests/python/test_engine.py": "b" * 64},
                "binaries": {"build/local-dev/tests/cpp/qte_bar_tests": "c" * 64},
                "build": {"build/local-dev/CTestTestfile.cmake": "d" * 64} if runner == "ctest"
                         else {"pyproject.toml": "d" * 64}}
    manifest = {"schema_version": 1, "artifact_kind": "test_evidence_v1", "runner": runner,
        "job_id": "1" * 32, "repository_path": "/local/qte", "cwd": "/local/qte",
        "argv": list(fixed_test_command("/local/qte", runner, "1" * 32)),
        "started_ns": "9223372036854775806", "finished_ns": "9223372036854775807",
        "exit_code": code, "termination": "exited", "log_truncated": False,
        "versions": {"python": "3.14.7", "pytest": "9.1.1" if runner == "pytest" else None,
                     "ctest": "4.1.0" if runner == "ctest" else None, "platform": "test fixture"},
        "identity_scope": "explicit_files_v1", "source_to_binary": "unknown",
        "start_identity": identity, "end_identity": deepcopy(identity)}
    manifest.update(changes)
    return rehash({"manifest.json": json.dumps(manifest).encode(),
                   "junit.xml": junit if junit is not None else xml(runner=runner),
                   "log.txt": b"Untrusted log: <script>fixture</script>\n"})


def edit_manifest(files, change):
    value = json.loads(files["manifest.json"])
    change(value)
    files["manifest.json"] = json.dumps(value).encode()
    return rehash(files)


@pytest.mark.parametrize("runner", ["pytest", "ctest"])
def test_complete_pass_has_exact_time_and_owned_metadata_not_build_proof(runner):
    evidence = verify_test_evidence_files(record(runner=runner))
    assert evidence.outcome == "passed" and evidence.report.passed == 1
    assert evidence.supports_passing_run
    assert dict(evidence.identity_stability) == dict.fromkeys(("source", "tests", "binaries", "build"), "same")
    assert evidence.manifest["finished_ns"] == "9223372036854775807"
    assert evidence.manifest["source_to_binary"] == "unknown"
    assert set(evidence.current_match(None).values()) == {"unknown"}
    changed = evidence.manifest
    changed["source_to_binary"] = "verified"
    assert evidence.manifest["source_to_binary"] == "unknown"


@pytest.mark.parametrize(("junit", "code", "outcome"), [
    (xml('<testcase name="f"><failure message="assertion"/></testcase>', failures=1), 1, "failed"),
    (xml('<testcase name="f"><error message="failed on setup"/></testcase>', errors=1), 1, "failed"),
    (xml('<testcase name="f"><error message="collection failure"/></testcase>', errors=1), 2, "collection_failed"),
    (xml('<testcase name="f"><error message="internal error"/></testcase>', errors=1), 3, "runner_failed"),
    (xml('', tests=0), 4, "runner_failed"),
    (xml('', tests=0), 5, "empty"),
    (xml('<testcase name="module" classname=""><skipped message="collection skipped"/></testcase>', skipped=1), 5, "skipped"),
    (xml('', tests=0), 0, "empty"),
    (xml('<testcase name="s"><skipped type="pytest.xfail"/></testcase>', skipped=1), 0, "skipped"),
    (xml('<testcase name="s"><skipped/></testcase><testcase name="p"/>', tests=2, skipped=1), 0, "passed_with_skips"),
    (xml(), 2, "interrupted"),
    (xml(), -15, "interrupted"),
])
def test_process_and_junit_outcomes_are_distinct(junit, code, outcome):
    evidence = verify_test_evidence_files(record(junit=junit, code=code))
    assert evidence.outcome == outcome


@pytest.mark.parametrize("termination", ["canceled", "timed_out", "interrupted"])
def test_partial_process_does_not_pass_even_with_passing_junit(termination):
    assert verify_test_evidence_files(record(termination=termination, code=None)).outcome == termination


@pytest.mark.parametrize("runner", ["pytest", "ctest"])
def test_failure_with_success_exit_is_rejected(runner):
    failed = xml('<testcase name="f"><failure/></testcase>', failures=1, runner=runner)
    with pytest.raises(TestEvidenceError):
        verify_test_evidence_files(record(runner=runner, junit=failed, code=0))
    assert verify_test_evidence_files(record(runner=runner, junit=failed, code=1)).outcome == "failed"


@pytest.mark.parametrize("code", [1, 5, 6, True, 256])
def test_contradictory_or_unsupported_pytest_exit_is_not_a_pass(code):
    with pytest.raises(TestEvidenceError):
        verify_test_evidence_files(record(code=code))


@pytest.mark.parametrize("group", ["source", "tests", "binaries", "build"])
def test_changed_identities_block_passing_provenance_independently(group):
    files = record(runner="ctest")
    edit_manifest(files, lambda m: m["end_identity"][group].update({next(iter(m["end_identity"][group])): "f" * 64}))
    evidence = verify_test_evidence_files(files)
    assert evidence.outcome == "passed"  # Preserve the process result separately.
    assert not evidence.supports_passing_run
    assert dict(evidence.identity_stability)[group] == "different"
    current = evidence.manifest["end_identity"]
    assert evidence.current_match(current)[group] == "same"
    current[group][next(iter(current[group]))] = "e" * 64
    assert evidence.current_match(current)[group] == "different"


@pytest.mark.parametrize("change", [
    {"artifact_kind": "single_run_v1"}, {"schema_version": True}, {"schema_version": 2},
    {"source_to_binary": "same"}, {"identity_scope": "complete_dependencies"},
    {"started_ns": "092"}, {"started_ns": 42}, {"finished_ns": "9223372036854775808"},
    {"finished_ns": "1"}, {"termination": "success"}, {"exit_code": None},
    {"cwd": "/other"}, {"repository_path": "/local/../qte"}, {"log_truncated": 0},
    {"argv": ["echo", "passed"]}, {"versions": {"pytest": "9"}}, {"extra": True},
])
def test_manifest_rejects_unsupported_or_fabricated_fields(change):
    with pytest.raises(TestEvidenceError):
        verify_test_evidence_files(record(**change))


def test_integrity_exact_inventory_and_duplicate_keys_fail_closed():
    original = record()
    for name in original:
        missing = dict(original); missing.pop(name)
        with pytest.raises(TestEvidenceError): verify_test_evidence_files(missing)
        tampered = dict(original); tampered[name] += b"!"
        with pytest.raises(TestEvidenceError): verify_test_evidence_files(tampered)
    extra = {**original, "extra.txt": b"extra"}
    with pytest.raises(TestEvidenceError): verify_test_evidence_files(rehash(extra))
    bad = dict(original)
    bad["manifest.json"] = bad["manifest.json"].replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    with pytest.raises(TestEvidenceError): verify_test_evidence_files(rehash(bad))


def test_fixed_commands_are_local_exact_and_never_contain_user_test_arguments():
    for runner in ("pytest", "ctest"):
        command = fixed_test_command("/local/qte", runner, "a" * 32)
        assert command[0].startswith("/local/qte/.venv/bin/")
        assert any("/local/qte/build/gui/jobs/" in item and item.endswith("/artifacts/junit.xml") for item in command)
        assert not any(item in {"--build", "install", "--collect-only"} for item in command)
    command = fixed_test_command("/local/qte", "pytest", "a" * 32)
    assert command[-1] == "xfail_strict=true"
    with pytest.raises(TestEvidenceError): fixed_test_command("/local/qte", "shell", "a" * 32)


def test_bounded_reader_verifies_selected_root_and_rejects_symlinks(tmp_path):
    root = tmp_path / "build/evidence"
    root.mkdir(parents=True)
    for name, data in record().items(): (root / name).write_bytes(data)
    assert read_test_evidence(tmp_path, "build/evidence").outcome == "passed"
    (root / "extra").write_bytes(b"extra")
    with pytest.raises(TestEvidenceError): read_test_evidence(tmp_path, "build/evidence")
    (root / "extra").unlink()
    (root / "log.txt").unlink()
    (root / "log.txt").symlink_to(root / "junit.xml")
    with pytest.raises(TestEvidenceError): read_test_evidence(tmp_path, "build/evidence")
    with pytest.raises(TestEvidenceError): read_test_evidence(tmp_path, "../evidence")


def test_missing_or_invalid_junit_never_produces_completed_evidence():
    with pytest.raises(TestEvidenceError): verify_test_evidence_files(record(junit=b""))
    with pytest.raises(TestEvidenceError): verify_test_evidence_files(record(junit=b"<broken>"))


@pytest.mark.parametrize("group", ["source", "tests", "binaries", "build"])
def test_completed_record_requires_explicit_nonempty_identity_scopes(group):
    files = record()
    edit_manifest(files, lambda m: m["start_identity"].update({group: {}}))
    with pytest.raises(TestEvidenceError): verify_test_evidence_files(files)


def test_no_executor_or_artifact_writer_is_called(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("unexpected execution"))
    assert verify_test_evidence_files(record()).outcome == "passed"
    assert list(tmp_path.iterdir()) == []
