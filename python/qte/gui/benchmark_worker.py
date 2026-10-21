"""Fixed benchmark command under the existing bounded process supervisor."""
import os
from pathlib import Path
import sys

from qte.benchmark import validate_measurement
from . import job_worker as lifecycle
from .artifacts import strict_json
from .benchmark_inputs import fixed_command, recheck_snapshot, load_snapshot, prepare_benchmark
from .benchmark_evidence import write_evidence
from .job_inputs import _job_inputs_fd, _mkdir_open
from .test_worker import _control, _execute, _finish, test_environment


def supervise_benchmark(spec, parent_fd, cancel_fd, result_fd, pending):
    descriptors, log_fd, code = [], None, None
    try:
        required = {"repository", "executable", "config", "output", "job_id", "kind",
                    "snapshot_hashes", "source_identity", "timeout_seconds", "environment"}
        if type(spec) is not dict or set(spec) != required or spec["kind"] != "benchmark":
            raise ValueError
        repository, job_id = Path(spec["repository"]), spec["job_id"]
        fixed_command(repository, job_id, 1, "hold")
        root = repository / "build/gui/jobs" / job_id
        environment = test_environment(root)
        base = {key: value for key, value in environment.items() if key not in {"PATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"}}
        if (repository != Path.cwd() or repository.resolve() != repository
                or spec["executable"] != str(repository / ".venv/bin/python")
                or Path(sys.executable).absolute() != repository / ".venv/bin/python"
                or spec["config"] != str(root / "inputs/benchmark-request.json")
                or spec["output"] != str(root / "artifacts") or spec["environment"] != base
                or type(spec["timeout_seconds"]) is not int or not 1 <= spec["timeout_seconds"] <= 1800):
            raise ValueError
        stopped = _control(parent_fd, cancel_fd, pending)
        if stopped:
            return _finish(result_fd, stopped, None)
        try:
            snapshot, prepared = recheck_snapshot(repository, job_id, spec["snapshot_hashes"])
            if spec["source_identity"] != "sha256:" + snapshot["request_sha256"]:
                raise ValueError
        except Exception:
            return _finish(result_fd, "provenance_changed", None)
        descriptors, _ = _job_inputs_fd(repository, job_id, create=False)
        job_fd = descriptors[-2]
        for name in ("tmp", "cache"):
            descriptors.append(_mkdir_open(job_fd, name))
        descriptors.append(_mkdir_open(job_fd, "artifacts", exclusive=True))
        log_fd = os.open("runner.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=job_fd)
        if not lifecycle._enable_subreaper():
            raise RuntimeError("Subreaper unavailable.")
        stopped = _control(parent_fd, cancel_fd, pending)
        if stopped:
            return _finish(result_fd, stopped, None)
        accepted = prepared.value
        argv = fixed_command(repository, job_id, accepted["bars"], accepted["strategy"])
        code, stopped, log, truncated, started, finished = _execute(
            repository, argv, environment, spec["timeout_seconds"], parent_fd, cancel_fd, pending, log_fd)
        # Partial or failed output never becomes timing evidence; private log remains.
        if stopped or code != 0 or truncated or len(log) > 65536:
            return _finish(result_fd, stopped or "execution_failed", code)
        try:
            measurement = validate_measurement(strict_json(log))
            if measurement["bars"] != accepted["bars"] or measurement["strategy"] != accepted["strategy"]:
                raise ValueError
        except Exception:
            return _finish(result_fd, "execution_failed", code)
        try:
            if load_snapshot(repository, job_id, spec["snapshot_hashes"]) != (snapshot, prepared):
                raise ValueError
            end = prepare_benchmark(repository, accepted["bars"], accepted["strategy"])
        except Exception:
            return _finish(result_fd, "provenance_changed", code)
        manifest = {"schema_version": 1, "artifact_kind": "benchmark_evidence_v1", "job_id": job_id,
            "repository_path": str(repository), "argv": list(argv),
            "request_sha256": snapshot["request_sha256"], "started_ns": str(started), "finished_ns": str(finished),
            "start_runtime": accepted["runtime"], "end_runtime": end.value["runtime"],
            "source_to_binary": "unknown", "measurement": measurement}
        write_evidence(repository, job_id, manifest)
        if end != prepared:
            return _finish(result_fd, "provenance_changed", code)
        lifecycle._result(result_fd, state="succeeded", reason=None, exit_code=0)
        return 0
    except BaseException:
        return _finish(result_fd, "supervisor_interrupted" if pending[0] else "worker_setup_failed", code)
    finally:
        if log_fd is not None:
            os.close(log_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
