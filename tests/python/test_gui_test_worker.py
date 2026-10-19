"""End-to-end regressions for the private fixed test supervisor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from qte.gui.test_preflight import validate_prepared
from qte.gui import test_snapshots


JOB = "d" * 32


def _prepared(repository: Path):
    digest = "a" * 64
    return validate_prepared({
        "schema_version": 1, "runner": "ctest", "repository_path": str(repository),
        "identity_scope": "explicit_files_v1", "source_to_binary": "unknown",
        "identity": {
            "source": {"python/qte/a.py": digest},
            "tests": {"tests/cpp/test_a.cpp": digest},
            "binaries": {
                ".venv/bin/ctest": digest,
                ".venv/lib/python3.14/site-packages/cmake/data/bin/ctest": digest,
                "build/local-dev/tests/cpp/qte_a_tests": digest,
            },
            "build": {"pyproject.toml": digest},
        },
        "versions": {"python": "3.14", "pytest": "9", "ctest": "4", "platform": "Linux"},
        "ctest_tests": [{"name": "qte.a", "argv": [
            str(repository / "build/local-dev/tests/cpp/qte_a_tests")]}],
    })


def _launcher(mode: str) -> str:
    body = {
        "pass": "<testsuite name='fixture' tests='1' failures='0' skipped='0' disabled='0'><testcase name='ok' classname='fixture' status='run'/></testsuite>",
        "noise": "<testsuite name='fixture' tests='1' failures='0' skipped='0' disabled='0'><testcase name='ok' classname='fixture' status='run'/></testsuite>",
        "exited_leader": "<testsuite name='fixture' tests='1' failures='0' skipped='0' disabled='0'><testcase name='ok' classname='fixture' status='run'/></testsuite>",
        "fail": "<testsuite name='fixture' tests='1' failures='1' skipped='0' disabled='0'><testcase name='bad' classname='fixture' status='fail'><failure/></testcase></testsuite>",
        "skip": "<testsuite name='fixture' tests='1' failures='0' skipped='1' disabled='0'><testcase name='s' classname='fixture' status='notrun'><skipped/></testcase></testsuite>",
        "empty": "<testsuite name='fixture' tests='0' failures='0' skipped='0' disabled='0'></testsuite>",
        "invalid": "<broken>",
    }.get(mode)
    setup = ""
    if mode == "sleep": setup = "time.sleep(30)"
    elif mode == "noise": setup = "os.write(1, b'x' * (17 * 1024 * 1024))"
    elif mode in {"descendant", "exited_leader"}:
        setup = ("child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
                 "start_new_session=True); pid_file = Path(junit).parents[1] / 'child.pid'; "
                 "temporary = pid_file.with_suffix('.tmp'); temporary.write_text(str(child.pid)); "
                 "os.replace(temporary, pid_file); " +
                 ("time.sleep(30)" if mode == "descendant" else ""))
    write = "" if mode in {"missing", "sleep", "descendant"} else f"open(junit, 'w').write({body!r})"
    code = 1 if mode == "fail" else 0
    return f"""#!{sys.executable}
import os, subprocess, sys, time
from pathlib import Path
expected = ['--preset', 'dev', '--output-on-failure', '--output-junit']
assert sys.argv[1:5] == expected and len(sys.argv) == 6
junit = sys.argv[5]
{setup}
{write}
raise SystemExit({code})
"""


@pytest.fixture
def run_worker(tmp_path, monkeypatch):
    processes = []

    def run(mode="pass", *, timeout=5, cancel=False, owner_eof=False, stale=False,
            end_fail=False, mutate_spec=None):
        repository = (tmp_path / f"repo-{len(processes)}").resolve()
        (repository / ".venv/bin").mkdir(parents=True)
        os.symlink(sys.executable, repository / ".venv/bin/python")
        ctest = repository / ".venv/bin/ctest"
        ctest.write_text(_launcher(mode), encoding="utf-8")
        ctest.chmod(0o755)
        prepared = _prepared(repository)
        monkeypatch.setattr(test_snapshots, "recheck_prepared", lambda root, value: value)
        snapshot = test_snapshots.write_test_snapshot(repository, JOB, prepared)
        root = repository / f"build/gui/jobs/{JOB}"
        spec = {
            "repository": str(repository), "executable": str(repository / ".venv/bin/python"),
            "config": str(root / "inputs/test-request.json"), "output": str(root / "artifacts"),
            "job_id": JOB, "kind": "test_run", "snapshot_hashes": snapshot.snapshot_hashes,
            "source_identity": "sha256:" + snapshot.request_sha256, "timeout_seconds": timeout,
            "environment": {key: value for key, value in {
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(root / "tmp"),
                "XDG_CACHE_HOME": str(root / "cache")}.items()},
        }
        if mutate_spec: mutate_spec(spec)
        spec_path = repository / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        owner_r, owner_w = os.pipe(); cancel_r, cancel_w = os.pipe(); result_r, result_w = os.pipe()
        script = """
import os, sys
from pathlib import Path
repo, spec, owner, cancel, result = sys.argv[1:]
os.chdir(repo); sys.executable = str(Path(repo) / '.venv/bin/python')
from qte.gui import job_worker, test_snapshots, test_worker
def recheck(repository, prepared):
    if STALE: raise ValueError('changed')
    return prepared
test_snapshots.recheck_prepared = recheck
def prepare(repository, runner):
    if END_FAIL: raise ValueError('changed after execution')
    return test_snapshots.load_test_snapshot(repository, 'd'*32, SPEC_HASHES)[1]
test_worker.prepare_test_run = prepare
raise SystemExit(job_worker.supervise(Path(spec), int(owner), int(cancel), int(result)))
""".replace("SPEC_HASHES", repr(snapshot.snapshot_hashes)).replace("STALE", repr(stale)).replace(
            "END_FAIL", repr(end_fail))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).parents[2] / "python")
        process = subprocess.Popen([sys.executable, "-c", script, str(repository), str(spec_path),
                                    str(owner_r), str(cancel_r), str(result_w)],
                                   pass_fds=(owner_r, cancel_r, result_w), env=env)
        processes.append(process)
        os.close(owner_r); os.close(cancel_r); os.close(result_w)
        if owner_eof: os.close(owner_w); owner_w = None
        if cancel:
            if mode == "descendant":
                pid_file = root / "child.pid"
                deadline = time.monotonic() + 3
                while not pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                assert pid_file.exists(), "controlled descendant did not start"
            else:
                time.sleep(.15)
            os.write(cancel_w, b"x")
        try:
            ready, _, _ = select.select([result_r], [], [], 8)
            if not ready:
                process.kill()
                process.wait(timeout=2)
                pytest.fail("test supervisor did not return a bounded result")
            data = os.read(result_r, 4096)
            process.wait(timeout=8)
        finally:
            for fd in (owner_w, cancel_w, result_r):
                if fd is not None:
                    try: os.close(fd)
                    except OSError: pass
        return json.loads(data), repository

    yield run
    for process in processes:
        if process.poll() is None:
            process.kill(); process.wait()


@pytest.mark.parametrize(("mode", "state", "reason"), [
    ("pass", "succeeded", None), ("fail", "failed", "execution_failed"),
    ("skip", "failed", "execution_failed"), ("empty", "failed", "execution_failed"),
    ("invalid", "failed", "execution_failed"), ("missing", "failed", "execution_failed"),
])
def test_junit_and_exit_outcomes(mode, state, reason, run_worker):
    result, repository = run_worker(mode)
    assert result["state"] == state and result["reason"] == reason
    assert set(result) == {"state", "reason", "exit_code"}
    artifacts = repository / f"build/gui/jobs/{JOB}/artifacts"
    assert (artifacts / "complete.json").exists() is (mode in {"pass", "fail", "skip", "empty"})


def test_noisy_output_is_bounded_but_fully_drained(run_worker):
    result, repository = run_worker("noise")
    assert result == {"exit_code": 0, "reason": None, "state": "succeeded"}
    runner_log = repository / f"build/gui/jobs/{JOB}/runner.log"
    assert runner_log.read_bytes() == b"x" * (16 * 1024 * 1024)
    assert (repository / f"build/gui/jobs/{JOB}/artifacts/log.txt").read_bytes() == runner_log.read_bytes()
    manifest = json.loads((repository / f"build/gui/jobs/{JOB}/artifacts/manifest.json").read_bytes())
    assert manifest["log_truncated"] is True


@pytest.mark.parametrize(("options", "reason"), [
    ({"cancel": True}, "canceled"), ({"owner_eof": True}, "owner_lost"),
    ({"timeout": 1}, "timeout"),
])
def test_control_stops_runner(options, reason, run_worker):
    result, _ = run_worker("sleep", **options)
    assert result["reason"] == reason
    assert result["state"] == ("canceled" if reason == "canceled" else "interrupted")


def test_environment_override_is_rejected_before_launch(run_worker):
    result, repository = run_worker("pass", mutate_spec=lambda spec: spec["environment"].update(EVIL="1"))
    assert result == {"exit_code": None, "reason": "worker_setup_failed", "state": "failed"}
    assert not (repository / f"build/gui/jobs/{JOB}/runner.log").exists()


def test_stale_preflight_is_rejected_before_launch(run_worker):
    result, repository = run_worker("pass", stale=True)
    assert result == {"exit_code": None, "reason": "provenance_changed", "state": "failed"}
    assert not (repository / f"build/gui/jobs/{JOB}/runner.log").exists()


def test_cancel_cleans_nested_setsid_descendant(run_worker):
    started = time.monotonic()
    result, repository = run_worker("descendant", cancel=True)
    assert result["reason"] == "canceled"
    assert time.monotonic() - started < 5
    child = int((repository / f"build/gui/jobs/{JOB}/child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_exited_leader_does_not_leave_stdout_holding_setsid_descendant(run_worker):
    started = time.monotonic()
    result, repository = run_worker("exited_leader")
    assert result == {"exit_code": 0, "reason": None, "state": "succeeded"}
    assert time.monotonic() - started < 5
    child = int((repository / f"build/gui/jobs/{JOB}/child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_end_preflight_failure_keeps_raw_junit_but_no_completion(run_worker):
    result, repository = run_worker("pass", end_fail=True)
    assert result == {"exit_code": 0, "reason": "provenance_changed", "state": "failed"}
    artifacts = repository / f"build/gui/jobs/{JOB}/artifacts"
    assert (artifacts / "junit.xml").exists()
    assert not (artifacts / "complete.json").exists()
