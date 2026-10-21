"""Controlled subprocess regressions for the fixed synthetic benchmark worker."""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from qte.gui import benchmark_evidence, benchmark_inputs
from qte.gui.test_preflight import validate_prepared as validate_runtime


JOB = "b" * 32


def _prepared(repository: Path):
    digest = "a" * 64
    runtime = validate_runtime({
        "schema_version": 1, "runner": "pytest", "repository_path": str(repository),
        "identity_scope": "explicit_files_v1", "source_to_binary": "unknown",
        "identity": {"source": {"python/qte/a.py": digest}, "tests": {"tests/python/test_a.py": digest},
                     "binaries": {".venv/lib/python3.14/site-packages/qte/_core.so": digest}, "build": {"pyproject.toml": digest}},
        "versions": {"python": "3.14", "pytest": "9", "ctest": "4", "platform": "Linux"},
        "ctest_tests": [],
    }).value
    return benchmark_inputs.validate_prepared(benchmark_inputs.PreparedBenchmark(benchmark_inputs._canonical({
        "schema_version": 1, "bars": 7, "strategy": "hold", "runtime": runtime,
    })))


def _launcher(mode: str) -> str:
    measurement = {
        "schema_version": 1, "artifact_kind": "benchmark_measurement_v1", "kind": "synthetic_not_historical",
        "workload_version": "synthetic-scale-v1", "workload_label": "no_op", "bars": 7, "strategy": "hold",
        "load_seconds": 0.01, "replay_seconds": 0.5, "bars_per_second": 14.0, "fills": 0,
        "final_equity": 100000.0, "source_identity": "sha256:" + "c" * 64,
        "machine": {"system": "fixture", "machine": "fixture", "python": "3.14", "cpu_count": 1},
        "timing": {"sample_count": 1, "warmup_count": 0, "clock": "time.perf_counter",
                   "load_scope": "synthetic_generation_and_dataset", "replay_scope": "strategy_construction_and_engine_run"},
    }
    if mode == "sleep": body = "import time; time.sleep(30)"
    elif mode == "invalid": body = "print('{not json}')"
    elif mode == "noise": body = "import sys; sys.stdout.write('x' * 4096)"
    else: body = f"print({json.dumps(json.dumps(measurement))})"
    code = 1 if mode == "nonzero" else 0
    return f"""#!{sys.executable}
import os, sys
from pathlib import Path
assert sys.argv[1:] == ['--bars', '7', '--strategy', 'hold']
assert os.environ['PYTHONNOUSERSITE'] == '1' and os.environ['PYTHONDONTWRITEBYTECODE'] == '1'
assert 'APCA_API_KEY_ID' not in os.environ and 'PYTHONPATH' not in os.environ
(Path(__file__).parents[1] / 'started').touch()
{body}
raise SystemExit({code})
"""


@pytest.fixture
def run_worker(tmp_path, monkeypatch):
    processes = []

    def run(mode="success", *, timeout=5, cancel=False, owner_eof=False, before_changed=False, end_changed=False,
            mutate_spec=None):
        repository = (tmp_path / f"repo-{len(processes)}").resolve()
        (repository / ".venv/bin").mkdir(parents=True)
        os.symlink(sys.executable, repository / ".venv/bin/python")
        script = repository / "scripts/benchmark_replay.py"; script.parent.mkdir()
        script.write_text(_launcher(mode), encoding="utf-8"); script.chmod(0o755)
        prepared = _prepared(repository)
        monkeypatch.setattr(benchmark_inputs, "recheck_prepared", lambda root, value: value)
        snapshot = benchmark_inputs.write_snapshot(repository, JOB, prepared)
        root = repository / f"build/gui/jobs/{JOB}"
        spec = {"repository": str(repository), "executable": str(repository / ".venv/bin/python"),
                "config": str(root / "inputs/benchmark-request.json"), "output": str(root / "artifacts"),
                "job_id": JOB, "kind": "benchmark", "snapshot_hashes": snapshot["snapshot_hashes"],
                "source_identity": "sha256:" + snapshot["request_sha256"], "timeout_seconds": timeout,
                "environment": {key: value for key, value in {
                    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": str(root / "tmp"), "XDG_CACHE_HOME": str(root / "cache")}.items()}}
        if mutate_spec:
            mutate_spec(spec)
        spec_path = repository / "spec.json"; spec_path.write_text(json.dumps(spec), encoding="utf-8")
        owner_r, owner_w = os.pipe(); cancel_r, cancel_w = os.pipe(); result_r, result_w = os.pipe()
        child = """
import os, sys
from pathlib import Path
repo, spec, owner, cancel, result = sys.argv[1:]
os.chdir(repo); sys.executable = str(Path(repo) / '.venv/bin/python')
from qte.gui import benchmark_inputs, benchmark_worker, job_worker, test_worker
prepared = benchmark_inputs.load_snapshot(Path(repo), 'b' * 32, HASHES)[1]
benchmark_inputs.recheck_prepared = lambda repository, value: (_ for _ in ()).throw(ValueError('changed')) if BEFORE else value
benchmark_worker.prepare_benchmark = lambda repository, bars, strategy: (_ for _ in ()).throw(ValueError('changed')) if END else prepared
test_worker.LOG_LIMIT = 1024
raise SystemExit(job_worker.supervise(Path(spec), int(owner), int(cancel), int(result)))
""".replace("HASHES", repr(snapshot["snapshot_hashes"])).replace("BEFORE", repr(before_changed)).replace("END", repr(end_changed))
        env = dict(os.environ); env["PYTHONPATH"] = str(Path(__file__).parents[2] / "python")
        env["APCA_API_KEY_ID"] = "fixture-not-a-real-key"
        process = subprocess.Popen([sys.executable, "-c", child, str(repository), str(spec_path), str(owner_r), str(cancel_r), str(result_w)],
                                   pass_fds=(owner_r, cancel_r, result_w), env=env)
        processes.append(process)
        os.close(owner_r); os.close(cancel_r); os.close(result_w)
        if owner_eof: os.close(owner_w); owner_w = None
        if cancel:
            deadline = time.monotonic() + 3
            while not (repository / "started").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert (repository / "started").exists(), "cancel must exercise an active benchmark"
            os.write(cancel_w, b"x")
        try:
            ready, _, _ = select.select([result_r], [], [], 8)
            if not ready:
                process.kill(); process.wait(timeout=2); pytest.fail("benchmark supervisor did not return a bounded result")
            result = json.loads(os.read(result_r, 4096)); process.wait(timeout=8)
        finally:
            for fd in (owner_w, cancel_w, result_r):
                if fd is not None:
                    try: os.close(fd)
                    except OSError: pass
        return result, repository, snapshot

    yield run
    for process in processes:
        if process.poll() is None: process.kill(); process.wait()


def _complete(repository: Path) -> Path:
    return repository / f"build/gui/jobs/{JOB}/artifacts/complete.json"


def test_fixed_argv_environment_and_successful_synthetic_json_become_evidence(run_worker):
    result, repository, snapshot = run_worker()
    assert result == {"state": "succeeded", "reason": None, "exit_code": 0}
    evidence = benchmark_evidence.read_evidence(repository, JOB)
    assert evidence["measurement"]["bars"] == 7
    assert evidence["measurement"]["strategy"] == "hold"
    assert evidence["argv"] == list(benchmark_inputs.fixed_command(repository, JOB, 7, "hold"))
    assert evidence["request_sha256"] == snapshot["request_sha256"]


@pytest.mark.parametrize(("mode", "reason"), [("invalid", "execution_failed"), ("nonzero", "execution_failed"),
                                                   ("noise", "execution_failed")])
def test_partial_invalid_or_truncated_runner_output_never_completes(mode, reason, run_worker):
    result, repository, _ = run_worker(mode)
    assert result["state"] == "failed" and result["reason"] == reason
    assert not _complete(repository).exists()


@pytest.mark.parametrize("mutate", [
    lambda spec: spec.update(config="/tmp/not-the-request"),
    lambda spec: spec.update(output="/tmp/not-the-output"),
    lambda spec: spec.update(executable="/usr/bin/python"),
    lambda spec: spec["environment"].update(PYTHONPATH="/tmp/untrusted"),
    lambda spec: spec.update(timeout_seconds=True),
    lambda spec: spec.update(command=["arbitrary"]),
])
def test_mutated_worker_spec_is_rejected_before_execution(run_worker, mutate):
    result, repository, _ = run_worker(mutate_spec=mutate)
    assert result["state"] == "failed" and result["reason"] == "worker_setup_failed"
    assert not (repository / "started").exists()
    assert not _complete(repository).exists()


@pytest.mark.parametrize(("options", "reason"), [
    ({"before_changed": True}, "provenance_changed"), ({"end_changed": True}, "provenance_changed"),
    ({"cancel": True}, "canceled"), ({"owner_eof": True}, "owner_lost"), ({"timeout": 1}, "timeout"),
])
def test_runtime_change_or_lifecycle_stop_never_writes_evidence(options, reason, run_worker):
    mode = "sleep" if reason in {"canceled", "owner_lost", "timeout"} else "success"
    result, repository, _ = run_worker(mode, **options)
    assert result["reason"] == reason
    assert result["state"] == ("canceled" if reason == "canceled" else "interrupted" if reason in {"owner_lost", "timeout"} else "failed")
    assert not _complete(repository).exists()
