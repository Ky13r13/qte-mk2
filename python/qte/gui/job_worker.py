"""Private per-job supervisor; never accepts browser-controlled command lines."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import re
import threading
import ctypes
from contextlib import contextmanager


class _SupervisorSignal(BaseException):
    pass


_DOWNLOAD_ENVIRONMENT = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
_DOWNLOAD_SECRET_LIMIT = 1024


def _result(fd: int, **value) -> None:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(data) > 4096:
        data = b'{"exit_code":null,"reason":"worker_setup_failed","state":"failed"}'
    try: os.write(fd, data)
    except OSError: pass


def _snapshot(repository: Path, job_id: str, expected: dict[str, str]):
    try:
        from qte.gui.job_inputs import verify_snapshot
        return verify_snapshot(repository, job_id, expected)
    except Exception:
        return None


def _snapshot_kind(value) -> tuple[str, str | None] | None:
    """Extract the fixed v2 kind, retaining v1 as implicit research only."""
    try:
        from qte.gui.inline_inputs import InlineSnapshotInputs
        from qte.gui.job_inputs import SnapshotInputs
    except Exception:
        return None
    if isinstance(value, InlineSnapshotInputs):
        kind, hashes = value.kind, value.hashes
    elif isinstance(value, SnapshotInputs):
        kind, hashes = "research_run", value.hashes
    else:
        return None
    canonical = hashes.get("canonical_config_sha256") if isinstance(hashes, dict) else None
    if (not isinstance(kind, str) or kind not in {"research_run", "dataset_validate", "strategy_lab", "download_alpaca"}
            or not isinstance(canonical, str) or re.fullmatch(r"[0-9a-f]{64}", canonical) is None):
        return None
    return kind, canonical


def _enable_subreaper() -> bool:
    """Adopt orphaned CLI descendants so this supervisor can reap its group."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        # Linux prctl(PR_SET_CHILD_SUBREAPER, 1). This private process has no
        # unrelated children, and is the only process allowed to adopt a CLI
        # descendant after its direct parent exits.
        return libc.prctl(36, 1, 0, 0, 0) == 0
    except (AttributeError, OSError):
        return False


@contextmanager
def _cleanup_signal_mask():
    """Do not let a second termination signal interrupt process-group reaping."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {value: signal.signal(value, signal.SIG_IGN) for value in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for value, handler in previous.items():
            signal.signal(value, handler)


def _terminate(process: subprocess.Popen) -> None:
    """Stop the entire owned process group, even after its leader has exited.

    A CLI leader can exit while a descendant that inherited its process group is
    still alive.  Waiting on ``Popen`` alone only reaps that leader, so it is
    not a sufficient owner-cleanup boundary for releasing the scheduler lock.
    """
    group = process.pid

    def group_alive() -> bool:
        try:
            os.killpg(group, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # This is our freshly-created session; an unexpected permission
            # boundary must not be treated as evidence that cleanup completed.
            return True

    def reap_adopted_group() -> None:
        # Popen owns the leader's status; only drain descendants after Popen
        # has reaped it. As subreaper, orphaned members of this new session are
        # adopted here instead of lingering as init-owned zombies.
        if process.poll() is None:
            return
        while True:
            try:
                reaped, _ = os.waitpid(-group, os.WNOHANG)
            except ChildProcessError:
                return
            if reaped == 0:
                return

    with _cleanup_signal_mask():
        if not group_alive():
            if process.poll() is None:
                process.wait()
            return
        try: os.killpg(group, signal.SIGTERM)
        except ProcessLookupError: return
        deadline = time.monotonic() + 2
        while group_alive() and time.monotonic() < deadline:
            # The direct child may already be reaped while a descendant remains,
            # therefore do not use only process.wait(timeout=...) as the grace
            # period clock.
            process.poll()
            reap_adopted_group()
            time.sleep(0.02)
        if group_alive():
            try: os.killpg(group, signal.SIGKILL)
            except ProcessLookupError: pass
            # Retain the inherited owner lock until every adopted group member
            # is reaped. A D-state process may keep this private supervisor
            # alive, which is fail-closed rather than allowing a second owner.
            while group_alive():
                process.poll()
                reap_adopted_group()
                time.sleep(0.02)
        if process.poll() is None:
            process.wait()
        reap_adopted_group()


def _signal_cleanup_handlers():
    """Record termination signals; cleanup starts after a process handle exists."""
    pending = [False]
    if threading.current_thread() is not threading.main_thread():
        return {}, pending
    previous = {}
    def interrupted(_signum, _frame):
        # Keep async-handler work limited to a local flag. In particular, do
        # not throw between fork and Popen assigning the returned handle.
        pending[0] = True
    for value in (signal.SIGTERM, signal.SIGINT):
        previous[value] = signal.signal(value, interrupted)
    return previous, pending


def _restore_handlers(previous) -> None:
    for value, handler in previous.items():
        signal.signal(value, handler)


def supervise(spec_path: Path, parent_fd: int, cancel_fd: int, result_fd: int) -> int:
    process = None
    handlers, pending_signal = _signal_cleanup_handlers()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        required = {"repository", "executable", "config", "output", "job_id", "kind", "snapshot_hashes",
                    "source_identity", "timeout_seconds", "environment"}
        if not isinstance(spec, dict) or set(spec) != required: raise ValueError
        repository, executable = Path(spec["repository"]), Path(spec["executable"])
        config, output = Path(spec["config"]), Path(spec["output"])
        hashes = spec["snapshot_hashes"]
        if (not isinstance(hashes, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in hashes.items())
                or type(spec["timeout_seconds"]) is not int): raise ValueError
        job_id = spec["job_id"]
        job_root = repository / "build/gui/jobs" / job_id
        safe_environment = {"LANG", "LC_ALL", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE",
                            "TMPDIR", "XDG_CACHE_HOME"}
        kind = spec["kind"]
        if (not isinstance(job_id, str) or re.fullmatch(r"[0-9a-f]{32}", job_id) is None
                or not isinstance(kind, str) or kind not in {"research_run", "dataset_validate", "strategy_lab", "download_alpaca"}
                or executable != Path(sys.executable).absolute()
                or config != job_root / "inputs/effective-config.json"
                or output != job_root / "artifacts"
                or not isinstance(spec["environment"], dict) or set(spec["environment"]) != safe_environment
                or spec["environment"].get("TMPDIR") != (job_root / "tmp").as_posix()
                or spec["environment"].get("XDG_CACHE_HOME") != (job_root / "cache").as_posix()):
            raise ValueError
        if kind == "download_alpaca" and any(
                not isinstance(os.environ.get(name), str) or not 1 <= len(os.environ[name]) <= _DOWNLOAD_SECRET_LIMIT
                for name in _DOWNLOAD_ENVIRONMENT):
            raise ValueError
        if pending_signal[0]:
            raise _SupervisorSignal
        from qte.provenance import source_identity
        ready, _, _ = select.select([parent_fd, cancel_fd], [], [], 0)
        if parent_fd in ready and not os.read(parent_fd, 1):
            _result(result_fd, state="interrupted", reason="owner_lost", exit_code=None)
            return 1
        if cancel_fd in ready and os.read(cancel_fd, 1):
            _result(result_fd, state="canceled", reason="canceled", exit_code=None)
            return 1
        verified = _snapshot(repository, job_id, hashes)
        if (source_identity() != spec["source_identity"] or _snapshot_kind(verified) is None
                or _snapshot_kind(verified)[0] != kind):
            _result(result_fd, state="failed", reason="provenance_changed", exit_code=None)
            return 1
        if not _enable_subreaper():
            raise RuntimeError("subreaper unavailable")
        argv = ([str(executable), "-m", "qte", "run", "--config", str(config), "--output", str(output),
                 "--export-version", "2"] if kind == "research_run" else
                [str(executable), "-m", "qte", "validate-data", "--config", str(config), "--output", str(output)]
                if kind == "dataset_validate" else
                [str(executable), "-m", "qte", "lab", "--config", str(config), "--output", str(output)]
                if kind == "strategy_lab" else
                [str(executable), "-m", "qte", "download-alpaca-config", "--config", str(config), "--output", str(output)])
        child_environment = dict(spec["environment"])
        if kind == "download_alpaca":
            child_environment.update({name: os.environ[name] for name in _DOWNLOAD_ENVIRONMENT})
        process = subprocess.Popen(argv, cwd=repository, env=child_environment, shell=False,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        if pending_signal[0]:
            raise _SupervisorSignal
        started = time.monotonic(); stopped = None
        while True:
            if pending_signal[0]:
                raise _SupervisorSignal
            status = process.poll()
            if status is not None: break
            ready, _, _ = select.select([parent_fd, cancel_fd], [], [], 0.1)
            # The CLI may finish while select is waiting. Recheck completion
            # before consuming a late cancellation so verified completion wins
            # that race rather than being overwritten as canceled.
            status = process.poll()
            if status is not None:
                break
            if parent_fd in ready and not os.read(parent_fd, 1):
                stopped = "owner_lost"
            if cancel_fd in ready and os.read(cancel_fd, 1) and stopped is None:
                stopped = "canceled"
            if time.monotonic() - started >= spec["timeout_seconds"] and stopped is None:
                stopped = "timeout"
            if stopped:
                _terminate(process); status = process.returncode; break
        # A direct CLI exit is not, by itself, proof that its session is gone.
        # Drain an unexpected surviving descendant before inspecting artifacts
        # or publishing any completion result.
        if not stopped:
            _terminate(process)
        # Completion wins a simultaneous cancellation only when the fixed CLI
        # exited cleanly and every accepted provenance check still succeeds.
        verified = _snapshot(repository, job_id, hashes)
        verified_info = _snapshot_kind(verified)
        if (status == 0 and verified_info is not None and verified_info[0] == kind
                and source_identity() == spec["source_identity"]):
            try:
                if kind == "research_run":
                    from qte.gui.artifacts import inspect_artifact
                    artifact = inspect_artifact(output)
                    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
                    valid = (artifact.kind == "research_export_v2" and artifact.metadata.get("integrity") == "verified"
                             and manifest.get("source_identity") == spec["source_identity"])
                elif kind == "strategy_lab":
                    canonical = verified_info[1]
                    if canonical is None:
                        valid = False
                    else:
                        from qte.gui.job_outputs import verify_fixed_lab_output
                        valid = bool(verify_fixed_lab_output(output, spec["source_identity"], canonical))
                elif kind == "dataset_validate":
                    from qte.data_validation import verify_dataset_output
                    validation = verify_dataset_output(output)
                    expected_data = verified.hashes.get("data_sha256")
                    configuration_bytes = config.read_bytes()
                    expected_configuration = json.loads(configuration_bytes)
                    input_name = ("input.csv" if expected_configuration.get("data", {}).get("adapter") == "csv"
                                  else "input.json")
                    expected_configuration["data"]["path"] = input_name
                    canonical = lambda value: json.dumps(
                        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
                    valid = (isinstance(validation, dict)
                             and hashlib.sha256(configuration_bytes).hexdigest()
                             == verified.hashes.get("effective_config_sha256")
                             and canonical(validation.get("configuration")) == canonical(expected_configuration)
                             and validation.get("manifest", {}).get("data_sha256") == expected_data
                             and validation.get("manifest", {}).get("source_identity")
                             == spec["source_identity"])
                else:
                    from qte.download_artifact import verify_download_output
                    download = verify_download_output(output)
                    configuration_bytes = config.read_bytes()
                    canonical = lambda value: json.dumps(
                        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
                    valid = (isinstance(download, dict)
                             and set(download) == {"manifest", "configuration", "summary"}
                             and canonical(download.get("configuration"))
                             == canonical(json.loads(configuration_bytes))
                             and hashlib.sha256(configuration_bytes).hexdigest()
                             == verified.hashes.get("effective_config_sha256")
                             and hashlib.sha256(canonical(json.loads(configuration_bytes)).encode("utf-8")).hexdigest()
                             == verified.hashes.get("canonical_config_sha256")
                             and isinstance(download.get("summary"), dict)
                             and isinstance(download.get("manifest"), dict)
                             and download["manifest"].get("artifact_kind") == "alpaca_download_v1"
                             and download["manifest"].get("source_identity") == spec["source_identity"])
            except Exception:
                valid = False
            if valid:
                _result(result_fd, state="succeeded", reason=None, exit_code=0)
                return 0
        final_snapshot = _snapshot_kind(_snapshot(repository, job_id, hashes))
        unchanged = (final_snapshot is not None and final_snapshot[0] == kind
                     and source_identity() == spec["source_identity"])
        reason = stopped or ("provenance_changed" if not unchanged else "execution_failed")
        state = "canceled" if reason == "canceled" else "interrupted" if reason in {"owner_lost", "timeout"} else "failed"
        _result(result_fd, state=state,
                reason=reason, exit_code=status)
        return 1
    except _SupervisorSignal:
        if process is not None: _terminate(process)
        _result(result_fd, state="interrupted", reason="supervisor_interrupted", exit_code=process.returncode if process else None)
        return 1
    except BaseException:
        if process is not None: _terminate(process)
        _result(result_fd, state="failed", reason="worker_setup_failed", exit_code=None)
        return 1
    finally:
        _restore_handlers(handlers)


def main(argv=None) -> int:
    values = sys.argv[1:] if argv is None else argv
    if len(values) != 4: return 2
    return supervise(Path(values[0]), *(int(value) for value in values[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
