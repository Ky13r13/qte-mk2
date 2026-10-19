"""Fixed test execution inside the existing private, lock-owning supervisor."""
from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

from . import job_worker as lifecycle
from .job_inputs import _job_inputs_fd, _mkdir_open
from .test_evidence import fixed_test_command
from .test_evidence_writer import finalize_test_evidence
from .test_preflight import prepare_test_run
from .test_snapshots import load_test_snapshot, recheck_test_snapshot


LOG_LIMIT = 16 * 1024 * 1024


def test_environment(root: Path) -> dict[str, str]:
    """No inherited Python/pytest configuration, provider secrets or user cache."""
    return {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(root / "tmp"),
            "XDG_CACHE_HOME": str(root / "cache"), "PATH": "/usr/bin:/bin",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}


test_environment.__test__ = False


def _control(parent_fd: int, cancel_fd: int, pending: list[bool]) -> str | None:
    if pending[0]:
        return "supervisor_interrupted"
    ready, _, _ = select.select([parent_fd, cancel_fd], [], [], 0)
    if parent_fd in ready and not os.read(parent_fd, 1):
        return "owner_lost"
    if cancel_fd in ready and os.read(cancel_fd, 1):
        return "canceled"
    return None


def _cleanup(process):
    lifecycle._terminate(process)
    # Pytest itself exercises supervisors that start nested sessions. As the
    # private subreaper, we own their orphaned children even outside the initial
    # group. Do not release the scheduler lock while an adopted child survives.
    # This is deliberately private-process-only, never called in the GUI server.
    with lifecycle._cleanup_signal_mask():
        children_path = Path(f"/proc/self/task/{os.getpid()}/children")
        deadline = time.monotonic() + 2
        while True:
            children = [int(value) for value in children_path.read_text().split()]
            if not children:
                return
            for child in children:
                try:
                    reaped, _ = os.waitpid(child, os.WNOHANG)
                    if reaped == 0:
                        # An unreaped child cannot have its PID reused here.
                        os.kill(child, signal.SIGTERM if time.monotonic() < deadline else signal.SIGKILL)
                except (ChildProcessError, ProcessLookupError):
                    pass
            time.sleep(0.02)


def _write(fd, data):
    offset = 0
    while offset < len(data):
        count = os.write(fd, data[offset:])
        if not count:
            raise OSError("Log write failed.")
        offset += count


def _execute(repository, argv, environment, timeout, parent_fd, cancel_fd, pending, log_fd):
    """Retain a bounded prefix but always drain output while handling controls."""
    process = None
    retained = bytearray()
    truncated = False
    started_ns = time.time_ns()
    stopped = None
    try:
        process = subprocess.Popen(argv, cwd=repository, env=environment, shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True)
        pipe = process.stdout.fileno()
        os.set_blocking(pipe, False)
        deadline = time.monotonic() + timeout
        eof = False

        def drain():
            nonlocal truncated, eof
            try:
                data = os.read(pipe, 65536)
            except BlockingIOError:
                return
            if not data:
                eof = True
                return
            keep = data[:max(0, LOG_LIMIT - len(retained))]
            _write(log_fd, keep)
            retained.extend(keep)
            truncated = truncated or len(keep) != len(data)

        while process.poll() is None:
            ready, _, _ = select.select(([pipe] if not eof else []) + [parent_fd, cancel_fd], [], [], 0.05)
            if pipe in ready:
                drain()
            # Completion wins a simultaneous cancellation, as for research jobs.
            if process.poll() is not None:
                break
            stopped = _control(parent_fd, cancel_fd, pending)
            if stopped is None and time.monotonic() >= deadline:
                stopped = "timeout"
            if stopped:
                break
        _cleanup(process)
        # Cleanup precedes EOF waiting: a descendant may have inherited stdout.
        end = time.monotonic() + 1
        while not eof:
            if time.monotonic() >= end:
                raise OSError("Runner output did not close.")
            ready, _, _ = select.select([pipe], [], [], 0.05)
            if ready:
                drain()
        os.fsync(log_fd)
        return process.returncode, stopped, bytes(retained), truncated, started_ns, time.time_ns()
    finally:
        if process is not None:
            _cleanup(process)
            if process.stdout is not None:
                process.stdout.close()


def supervise_test(spec: dict, parent_fd: int, cancel_fd: int, result_fd: int, pending: list[bool]) -> int:
    descriptors, log_fd = [], None
    code, stopped = None, None
    try:
        required = {"repository", "executable", "config", "output", "job_id", "kind",
                    "snapshot_hashes", "source_identity", "timeout_seconds", "environment"}
        if set(spec) != required or spec["kind"] != "test_run":
            raise ValueError
        repository = Path(spec["repository"])
        job_id = spec["job_id"]
        # This call validates canonical paths/ID before any path creation.
        fixed_test_command(str(repository), "pytest", job_id)
        root = repository / "build/gui/jobs" / job_id
        environment = test_environment(root)
        base_environment = {key: value for key, value in environment.items()
                            if key not in {"PATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"}}
        if (repository != Path.cwd() or repository.resolve() != repository
                or spec["executable"] != str(repository / ".venv/bin/python")
                or Path(sys.executable).absolute() != repository / ".venv/bin/python"
                or spec["config"] != str(root / "inputs/test-request.json")
                or spec["output"] != str(root / "artifacts")
                or spec["environment"] != base_environment
                or type(spec["timeout_seconds"]) is not int or not 1 <= spec["timeout_seconds"] <= 1800):
            raise ValueError
        stopped = _control(parent_fd, cancel_fd, pending)
        if stopped:
            return _finish(result_fd, stopped, code)
        try:
            snapshot, prepared = recheck_test_snapshot(repository, job_id, spec["snapshot_hashes"])
            if spec["source_identity"] != "sha256:" + snapshot.request_sha256:
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
        argv = fixed_test_command(str(repository), accepted["runner"], job_id)
        code, stopped, log, truncated, started, finished = _execute(
            repository, argv, environment, spec["timeout_seconds"], parent_fd, cancel_fd, pending, log_fd)
        try:
            if load_test_snapshot(repository, job_id, spec["snapshot_hashes"]) != (snapshot, prepared):
                raise ValueError
            end = prepare_test_run(repository, accepted["runner"])
        except Exception:
            return _finish(result_fd, stopped or "provenance_changed", code)
        manifest = {"schema_version": 1, "artifact_kind": "test_evidence_v1",
            "runner": accepted["runner"], "job_id": job_id, "repository_path": str(repository),
            "cwd": str(repository), "argv": list(argv), "started_ns": str(started), "finished_ns": str(finished),
            "exit_code": code, "termination": ("canceled" if stopped == "canceled" else
                "timed_out" if stopped == "timeout" else "interrupted" if stopped else "exited"),
            "versions": accepted["versions"], "identity_scope": "explicit_files_v1",
            "start_identity": accepted["identity"], "end_identity": end.value["identity"],
            "source_to_binary": "unknown", "log_truncated": truncated}
        try:
            evidence = finalize_test_evidence(repository, job_id, manifest, log)
        except Exception:
            return _finish(result_fd, stopped or "execution_failed", code)
        if stopped:
            return _finish(result_fd, stopped, code)
        if end != prepared:
            return _finish(result_fd, "provenance_changed", code)
        if evidence.supports_passing_run:
            lifecycle._result(result_fd, state="succeeded", reason=None, exit_code=0)
            return 0
        return _finish(result_fd, "execution_failed", code)
    except BaseException:
        return _finish(result_fd, "supervisor_interrupted" if pending[0] else "worker_setup_failed", code)
    finally:
        if log_fd is not None:
            os.close(log_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _finish(result_fd, reason, code):
    state = ("canceled" if reason == "canceled" else "interrupted" if reason in
             {"owner_lost", "timeout", "supervisor_interrupted"} else "failed")
    lifecycle._result(result_fd, state=state, reason=reason, exit_code=code)
    return 1
