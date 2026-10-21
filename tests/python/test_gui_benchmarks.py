"""Fail-closed integration tests for bounded benchmark jobs and evidence."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from qte.gui import benchmark_inputs
from qte.gui.artifact_io import ArtifactIOError
from qte.gui.artifacts import ArtifactError
from qte.gui.benchmark_evidence import bound_evidence, read_evidence, write_evidence
from qte.gui.benchmark_inputs import PreparedBenchmark, fixed_command, validate_prepared
from qte.gui.catalog import Catalog
from qte.gui.jobs import JobError, JobManager
from test_gui_test_preflight import _value


def wait(manager, job_id, state):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["state"] == state:
            return job
        if job["state"] in {"succeeded", "failed", "canceled", "interrupted"}:
            raise AssertionError(job)
        time.sleep(0.01)
    raise AssertionError(manager.get(job_id))


def measurement(bars=24, strategy="hold", python="3.14.7"):
    replay = 0.25
    return {
        "schema_version": 1, "artifact_kind": "benchmark_measurement_v1",
        "kind": "synthetic_not_historical", "workload_version": "synthetic-scale-v1",
        "workload_label": "no_op" if strategy == "hold" else "sma",
        "bars": bars, "strategy": strategy, "load_seconds": 0.125,
        "replay_seconds": replay, "bars_per_second": bars / replay,
        "fills": 0, "final_equity": 100000.0,
        "source_identity": "sha256:" + "a" * 64,
        "machine": {"system": "TestOS", "machine": "test64", "python": python, "cpu_count": 4},
        "timing": {"sample_count": 1, "warmup_count": 0, "clock": "time.perf_counter",
                   "load_scope": "synthetic_generation_and_dataset",
                   "replay_scope": "strategy_construction_and_engine_run"},
    }


def evidence(repository, spec, mode="pass"):
    request = json.loads((repository / f'build/gui/jobs/{spec["job_id"]}/inputs/benchmark-request.json').read_bytes())
    output = Path(spec["output"])
    output.mkdir()
    if mode == "partial":
        (output / "manifest.json").write_bytes(b"{}")
        return None
    observed_bars = request["bars"] + 1 if mode == "binding" else request["bars"]
    end_runtime = deepcopy(request["runtime"])
    if mode == "runtime":
        end_runtime["versions"]["python"] = "3.14.8"
    manifest = {
        "schema_version": 1, "artifact_kind": "benchmark_evidence_v1",
        "job_id": spec["job_id"], "repository_path": str(repository),
        "argv": list(fixed_command(repository, spec["job_id"], observed_bars, request["strategy"])),
        "request_sha256": spec["source_identity"].removeprefix("sha256:"),
        "started_ns": "1", "finished_ns": "2",
        "start_runtime": request["runtime"], "end_runtime": end_runtime,
        "source_to_binary": "unknown",
        "measurement": measurement(observed_bars, request["strategy"],
                                   request["runtime"]["versions"]["python"]),
    }
    return write_evidence(repository, spec["job_id"], manifest)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    runtime = _value()
    runtime["repository_path"] = str(tmp_path)
    prepared = validate_prepared(PreparedBenchmark(json.dumps({
        "schema_version": 1, "bars": 24, "strategy": "hold", "runtime": runtime,
    }, sort_keys=True, separators=(",", ":")).encode()))
    monkeypatch.setattr(benchmark_inputs, "recheck_prepared",
                        lambda repository, item: validate_prepared(item))
    monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv/bin/python"))
    launched = []
    gate = threading.Event()
    mode = ["pass"]

    class Running:
        process = SimpleNamespace(pid=424242)
        canceled = False

        def __init__(self, spec): self.spec = spec
        def cancel(self): self.canceled = True; gate.set()
        def close_owner(self): pass
        def wait(self):
            assert gate.wait(5)
            if self.canceled:
                return {"state": "canceled", "reason": "canceled", "exit_code": None}
            if mode[0] != "lie":
                evidence(tmp_path, self.spec, mode[0])
            if mode[0] == "partial":
                return {"state": "failed", "reason": "execution_failed", "exit_code": 1}
            return {"state": "succeeded", "reason": None, "exit_code": 0}

    class Supervisor:
        def __init__(self, repository, lock_fd):
            assert repository == tmp_path and lock_fd >= 0
        def launch(self, path, environment):
            spec = json.loads(path.read_bytes())
            launched.append((spec, environment))
            return Running(spec)

    def forbidden(*args):
        pytest.fail("benchmark acquired research authorization or publication")

    manager = JobManager(tmp_path, SimpleNamespace(), supervisor_factory=Supervisor,
                         input_authorizer=forbidden, output_protector=forbidden)
    yield manager, prepared, gate, mode, launched
    gate.set()
    manager.close()


def submit(manager, prepared, key="benchmark_request_123456", timeout=30):
    return manager.submit("request", "benchmark", prepared, key, timeout)


def test_request_snapshot_queue_idempotency_and_sanitized_success(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    first = submit(manager, prepared)
    assert submit(manager, prepared)["id"] == first["id"]
    with pytest.raises(JobError) as error:
        submit(manager, prepared, timeout=31)
    assert error.value.code == "idempotency_conflict"
    changed = prepared.value
    changed["bars"] = 25
    with pytest.raises(JobError) as error:
        submit(manager, validate_prepared(PreparedBenchmark(json.dumps(
            changed, sort_keys=True, separators=(",", ":")).encode())))
    assert error.value.code == "idempotency_conflict"
    wait(manager, first["id"], "running")
    second = submit(manager, prepared, "benchmark_request_789012")
    assert second["state"] == "queued" and len(launched) == 1
    gate.set()
    result = wait(manager, first["id"], "succeeded")
    wait(manager, second["id"], "succeeded")
    spec, environment = launched[0]
    assert spec["kind"] == "benchmark"
    assert set(spec["snapshot_hashes"]) == {"benchmark-request.json", "input-manifest.json"}
    assert set(environment) == {"LANG", "LC_ALL", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE",
                                "TMPDIR", "XDG_CACHE_HOME"}
    assert result["input_hashes"] == {} and result["source_identity"] is None
    assert result["exit_code"] is None and "measurement" not in result
    assert read_evidence(tmp_path, first["id"])["measurement"]["bars"] == 24
    with pytest.raises(ArtifactError) as error:
        Catalog(tmp_path).register(result["output_path"])
    assert error.value.code == "job_output_unavailable"


@pytest.mark.parametrize("mode_name", ["lie", "runtime", "binding"])
def test_parent_rejects_false_success_runtime_change_and_request_mismatch(setup, mode_name):
    manager, prepared, gate, mode, launched = setup
    mode[0] = mode_name
    job = submit(manager, prepared)
    gate.set()
    result = wait(manager, job["id"], "failed")
    assert result["reason"] == "benchmark_job_failed"


def test_changed_request_snapshot_before_dispatch_is_rejected(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    active = submit(manager, prepared)
    wait(manager, active["id"], "running")
    queued = submit(manager, prepared, "other_benchmark_request")
    request = tmp_path / f'build/gui/jobs/{queued["id"]}/inputs/benchmark-request.json'
    request.chmod(0o600)
    request.write_bytes(b"{}")
    gate.set()
    wait(manager, active["id"], "succeeded")
    assert wait(manager, queued["id"], "failed")["reason"] == "benchmark_job_failed"
    assert len(launched) == 1


def test_parent_rechecks_current_runtime_after_child_success(setup, monkeypatch):
    manager, prepared, gate, mode, launched = setup
    job = submit(manager, prepared)
    wait(manager, job["id"], "running")
    monkeypatch.setattr(benchmark_inputs, "recheck_prepared",
                        lambda *args: (_ for _ in ()).throw(ValueError("runtime changed")))
    gate.set()
    assert wait(manager, job["id"], "failed")["reason"] == "benchmark_job_failed"


@pytest.mark.parametrize("mutation", ["changed", "rehashed", "extra", "symlink", "missing"])
def test_artifact_mutations_fail_closed(setup, tmp_path, mutation):
    manager, prepared, gate, mode, launched = setup
    job = submit(manager, prepared)
    gate.set()
    wait(manager, job["id"], "succeeded")
    spec = launched[0][0]
    root = Path(spec["output"])
    manifest = root / "manifest.json"
    complete = root / "complete.json"
    if mutation == "changed":
        manifest.write_bytes(manifest.read_bytes() + b" ")
    elif mutation == "rehashed":
        value = json.loads(manifest.read_bytes())
        value["measurement"]["bars"] += 1
        value["measurement"]["bars_per_second"] += 4
        value["argv"][3] = str(value["measurement"]["bars"])
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        manifest.write_bytes(encoded)
        complete.write_bytes(json.dumps({"schema_version": 1, "sha256": {
            "manifest.json": sha256(encoded).hexdigest()}}, sort_keys=True,
            separators=(",", ":")).encode())
    elif mutation == "extra":
        (root / "extra.json").write_bytes(b"{}")
    elif mutation == "symlink":
        manifest.unlink()
        manifest.symlink_to(complete.name)
    else:
        complete.unlink()
    with pytest.raises((ValueError, ArtifactIOError)):
        bound_evidence(tmp_path, job["id"], spec["snapshot_hashes"], spec["source_identity"])


def test_partial_and_canceled_jobs_never_become_timing_evidence(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    mode[0] = "partial"
    partial = submit(manager, prepared)
    gate.set()
    assert wait(manager, partial["id"], "failed")["reason"] == "benchmark_job_failed"
    with pytest.raises((ValueError, ArtifactIOError)):
        read_evidence(tmp_path, partial["id"])


def test_cancel_queued_benchmark_never_launches_or_creates_evidence(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    active = submit(manager, prepared)
    wait(manager, active["id"], "running")
    queued = submit(manager, prepared, "queued_benchmark_request")
    assert manager.cancel(queued["id"])["state"] == "canceled"
    gate.set()
    wait(manager, active["id"], "succeeded")
    assert len(launched) == 1
    with pytest.raises((ValueError, ArtifactIOError)):
        read_evidence(tmp_path, queued["id"])


def test_benchmark_requires_typed_prepared_request(setup, tmp_path):
    manager, prepared, gate, mode, launched = setup
    with pytest.raises(JobError) as error:
        submit(manager, prepared.value)
    assert error.value.code == "invalid_job_request"
    assert not (tmp_path / "build/gui/jobs").exists()
