"""Process-level watchdog checks for the fixed local-job worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from qte.gui import job_worker
from qte.gui.job_inputs import SnapshotInputs


_REAL_POPEN = subprocess.Popen


def _verified_snapshot(kind="research_run"):
    if kind != "research_run":
        from qte.gui.inline_inputs import InlineSnapshotInputs
        return InlineSnapshotInputs(
            "a" * 32, kind, "build/gui/jobs/" + "a" * 32 + "/inputs",
            "build/gui/jobs/" + "a" * 32 + "/inputs/effective-config.json", None, None, None,
            "build/gui/jobs/" + "a" * 32 + "/inputs/effective-config.json", None, None,
            {"canonical_config_sha256": "a" * 64, "data_sha256": None}, {}, "b" * 64,
        )
    return SnapshotInputs(
        "a" * 32, "build/gui/jobs/" + "a" * 32 + "/inputs",
        "build/gui/jobs/" + "a" * 32 + "/inputs/effective-config.json", "examples/run.json", "data/bars.csv",
        "build/gui/jobs/" + "a" * 32 + "/inputs/effective-config.json", "build/gui/jobs/" + "a" * 32 + "/inputs/input.csv",
        {"canonical_config_sha256": "a" * 64}, {}, "b" * 64,
    )


def _spec(root: Path, *, timeout_seconds: int = 30, kind: str = "research_run") -> Path:
    job_id = "a" * 32
    job_root = root / "build/gui/jobs" / job_id
    (job_root / "inputs").mkdir(parents=True)
    environment = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
                   "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": (job_root / "tmp").as_posix(),
                   "XDG_CACHE_HOME": (job_root / "cache").as_posix()}
    value = {"repository": root.as_posix(), "executable": Path(sys.executable).absolute().as_posix(),
             "config": (job_root / "inputs/effective-config.json").as_posix(),
             "output": (job_root / "artifacts").as_posix(), "job_id": job_id, "kind": kind,
             "snapshot_hashes": {"input-manifest.json": "0" * 64}, "source_identity": "test-source",
             "timeout_seconds": timeout_seconds, "environment": environment}
    path = root / "worker.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _child_script(marker: Path, fd_probe: int | None, *, ignore_term: bool,
                  descendant_marker: Path | None = None, exit_after: float | None = None) -> str:
    descendant_path = repr(descendant_marker.as_posix()) if descendant_marker else "None"
    return f'''import os, signal, time
import subprocess, sys
marker = {marker.as_posix()!r}
probe = {fd_probe!r}
if {ignore_term!r}:
    signal.signal(signal.SIGTERM, lambda *_: None)
descendant_marker = {descendant_path}
if descendant_marker is not None:
    descendant = """import os, signal, time
signal.signal(signal.SIGTERM, lambda *_: None)
marker = {descendant_path}
temporary = marker + "." + str(os.getpid()) + ".tmp"
with open(temporary, "x", encoding="ascii") as stream:
    stream.write(str(os.getpid()))
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, marker)
while True:
    time.sleep(0.02)
"""
    subprocess.Popen([sys.executable, "-c", descendant], close_fds=True)
    while not os.path.exists(descendant_marker):
        time.sleep(0.005)
state = "not_checked"
if probe is not None:
    try:
        os.fstat(probe)
        state = "inherited"
    except OSError:
        state = "closed"
temporary = marker + "." + str(os.getpid()) + ".tmp"
with open(temporary, "x", encoding="ascii") as stream:
    stream.write(state)
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, marker)
if {exit_after!r} is not None:
    time.sleep({exit_after!r})
    raise SystemExit(0)
while True:
    time.sleep(0.02)
'''


def _run_worker(monkeypatch, spec: Path, marker: Path, *, ignore_term=False, fd_probe=None,
                descendant_marker=None, exit_after=None, snapshot=None, identity=None):
    """Launch supervise in a thread, but give its real Popen a controlled child."""
    calls, launched = [], threading.Event()

    def controlled_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        process = _REAL_POPEN([sys.executable, "-c", _child_script(
            marker, fd_probe, ignore_term=ignore_term, descendant_marker=descendant_marker,
            exit_after=exit_after)], **kwargs)
        calls.append(process)
        launched.set()
        return process

    monkeypatch.setattr(job_worker, "_snapshot", snapshot or (lambda *_: _verified_snapshot()))
    monkeypatch.setattr(job_worker, "_enable_subreaper", lambda: True)
    import qte.provenance
    monkeypatch.setattr(qte.provenance, "source_identity", identity or (lambda: "test-source"))
    monkeypatch.setattr(job_worker.subprocess, "Popen", controlled_popen)
    parent_r, parent_w = os.pipe()
    cancel_r, cancel_w = os.pipe()
    result_r, result_w = os.pipe()
    outcome = {}

    def target():
        try:
            outcome["return"] = job_worker.supervise(spec, parent_r, cancel_r, result_w)
        finally:
            for descriptor in (parent_r, cancel_r, result_w):
                try: os.close(descriptor)
                except OSError: pass

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert launched.wait(2), "worker did not launch its fixed child"
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists(), "controlled child did not become ready"

    def finish():
        thread.join(5)
        process = calls[1]
        if thread.is_alive():
            process.kill()
            thread.join(3)
        for descriptor in (parent_w, cancel_w):
            try: os.close(descriptor)
            except OSError: pass
        payload = json.loads(os.read(result_r, 4096))
        os.close(result_r)
        assert not thread.is_alive(), "watchdog did not return"
        return outcome["return"], payload, calls[0], process

    return parent_w, cancel_w, finish


def _standalone_supervisor(root: Path, *, child_ignores_term: bool, lock_fd: int | None = None,
                           child_spawns_descendant: bool = False):
    """Run the supervisor in a real process, including its Linux subreaper."""
    spec = _spec(root)
    marker = root / "standalone-child.json"
    descendant_marker = root / "standalone-descendant.json"
    descendant = f'''import json, os, signal, time
signal.signal(signal.SIGTERM, lambda *_: None)
marker = {descendant_marker.as_posix()!r}
temporary = marker + "." + str(os.getpid()) + ".tmp"
with open(temporary, "x", encoding="ascii") as stream:
    json.dump({{"pid": os.getpid()}}, stream)
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, marker)
while True:
    time.sleep(0.02)
'''
    spawn_descendant = f'''import subprocess
subprocess.Popen([sys.executable, "-c", {descendant!r}], close_fds=True)
while not os.path.exists({descendant_marker.as_posix()!r}):
    time.sleep(0.005)
descendant_pid = json.loads(open({descendant_marker.as_posix()!r}, encoding="ascii").read())["pid"]
''' if child_spawns_descendant else "descendant_pid = None"
    child = f'''import json, os, signal, time
import sys
if {child_ignores_term!r}:
    signal.signal(signal.SIGTERM, lambda *_: None)
{spawn_descendant}
marker = {marker.as_posix()!r}
temporary = marker + "." + str(os.getpid()) + ".tmp"
with open(temporary, "x", encoding="ascii") as stream:
    json.dump({{"pid": os.getpid(), "descendant_pid": descendant_pid,
               "blocked": sorted(signal.pthread_sigmask(signal.SIG_BLOCK, set()))}}, stream)
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, marker)
while True:
    time.sleep(0.02)
'''
    script = f'''import subprocess, sys
from pathlib import Path
from qte.gui import job_worker
from qte.gui.job_inputs import SnapshotInputs
import qte.provenance
real_popen = subprocess.Popen
qte.provenance.source_identity = lambda: "test-source"
job_worker._snapshot = lambda *_: SnapshotInputs("a" * 32, "inputs", "inputs/effective-config.json", "examples/run.json", "data/bars.csv", "inputs/effective-config.json", "inputs/input.csv", {{"canonical_config_sha256": "a" * 64}}, {{}}, "b" * 64)
def controlled(_argv, **kwargs):
    return real_popen([sys.executable, "-c", {child!r}], **kwargs)
job_worker.subprocess.Popen = controlled
raise SystemExit(job_worker.supervise(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])))
'''
    parent_r, parent_w = os.pipe()
    cancel_r, cancel_w = os.pipe()
    result_r, result_w = os.pipe()
    descriptors = [parent_r, cancel_r, result_w]
    if lock_fd is not None:
        descriptors.append(lock_fd)
    process = _REAL_POPEN([sys.executable, "-c", script, str(spec), str(parent_r), str(cancel_r), str(result_w)],
                          cwd=root, pass_fds=tuple(descriptors), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    for descriptor in (parent_r, cancel_r, result_w):
        os.close(descriptor)
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not marker.exists():
        # A fixture setup failure must not strand its real supervisor or any
        # inherited job descriptors. TERM takes the tested cleanup path first;
        # the hard kill is only a bounded test-fixture fallback.
        try:
            process.terminate()
            # Allow the worker's two-second TERM grace plus descendant reaping
            # to finish before the fixture ever considers killing supervisor.
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        finally:
            for descriptor in (parent_w, cancel_w, result_r):
                try: os.close(descriptor)
                except OSError: pass
        raise AssertionError("standalone supervisor did not launch its child")

    def finish():
        assert process.wait(timeout=8) == 1
        payload = json.loads(os.read(result_r, 4096))
        os.close(result_r)
        for descriptor in (parent_w, cancel_w):
            try: os.close(descriptor)
            except OSError: pass
        return payload, json.loads(marker.read_text(encoding="ascii"))

    return process, parent_w, cancel_w, finish


def test_owner_eof_terminates_owned_child_and_uses_fixed_environment(monkeypatch, tmp_path):
    secret_r, secret_w = os.pipe()
    try:
        spec = _spec(tmp_path)
        parent_w, cancel_w, finish = _run_worker(monkeypatch, spec, tmp_path / "ready", fd_probe=secret_r)
        os.close(parent_w)  # EOF is the actual owner-liveness signal.
        code, payload, call, process = finish()
        assert code == 1
        assert payload == {"state": "interrupted", "reason": "owner_lost", "exit_code": process.returncode}
        argv, kwargs = call
        assert argv == [str(Path(sys.executable).absolute()), "-m", "qte", "run", "--config",
                       json.loads(spec.read_text())["config"], "--output", json.loads(spec.read_text())["output"],
                       "--export-version", "2"]
        assert kwargs["shell"] is False and kwargs["close_fds"] is True and kwargs["start_new_session"] is True
        assert kwargs["env"] == json.loads(spec.read_text())["environment"]
        assert (tmp_path / "ready").read_text(encoding="ascii") == "closed"
    finally:
        for descriptor in (secret_r, secret_w):
            try: os.close(descriptor)
            except OSError: pass


def test_owner_eof_wins_when_owner_crash_closes_cancel_pipe_too(monkeypatch, tmp_path):
    parent_w, cancel_w, finish = _run_worker(monkeypatch, _spec(tmp_path), tmp_path / "ready")
    os.close(parent_w)
    os.close(cancel_w)
    code, payload, _, process = finish()
    assert code == 1
    assert payload == {"state": "interrupted", "reason": "owner_lost", "exit_code": process.returncode}


@pytest.mark.parametrize("changed", ["source", "snapshot"])
def test_prelaunch_provenance_mutation_never_starts_a_child(monkeypatch, tmp_path, changed):
    spec = _spec(tmp_path)
    import qte.provenance
    monkeypatch.setattr(qte.provenance, "source_identity",
                        lambda: "changed-source" if changed == "source" else "test-source")
    monkeypatch.setattr(job_worker, "_snapshot", lambda *_: _verified_snapshot() if changed != "snapshot" else None)
    monkeypatch.setattr(job_worker.subprocess, "Popen",
                        lambda *_args, **_kwargs: pytest.fail("provenance failure launched a child"))
    parent_r, parent_w = os.pipe()
    cancel_r, cancel_w = os.pipe()
    result_r, result_w = os.pipe()
    try:
        assert job_worker.supervise(spec, parent_r, cancel_r, result_w) == 1
        os.close(result_w)
        assert json.loads(os.read(result_r, 4096)) == {
            "state": "failed", "reason": "provenance_changed", "exit_code": None}
    finally:
        for descriptor in (parent_r, parent_w, cancel_r, cancel_w, result_r, result_w):
            try: os.close(descriptor)
            except OSError: pass


@pytest.mark.parametrize("verified", [True, False, object(), SimpleNamespace(
    kind="research_run", hashes={"canonical_config_sha256": "a" * 64})])
def test_untyped_snapshot_result_never_launches_a_child(monkeypatch, tmp_path, verified):
    monkeypatch.setattr(job_worker, "_snapshot", lambda *_: verified)
    monkeypatch.setattr(job_worker.subprocess, "Popen",
                        lambda *_args, **_kwargs: pytest.fail("untyped result launched a child"))
    parent_r, parent_w = os.pipe(); cancel_r, cancel_w = os.pipe(); result_r, result_w = os.pipe()
    try:
        assert job_worker.supervise(_spec(tmp_path), parent_r, cancel_r, result_w) == 1
        os.close(result_w)
        assert json.loads(os.read(result_r, 4096)) == {
            "state": "failed", "reason": "provenance_changed", "exit_code": None}
    finally:
        for descriptor in (parent_r, parent_w, cancel_r, cancel_w, result_r, result_w):
            try: os.close(descriptor)
            except OSError: pass


def test_standalone_supervisor_child_has_unblocked_term_and_reaps_it(tmp_path):
    supervisor, parent_w, cancel_w, finish = _standalone_supervisor(tmp_path, child_ignores_term=False)
    os.kill(supervisor.pid, signal.SIGTERM)
    payload, child = finish()
    assert payload == {"state": "interrupted", "reason": "supervisor_interrupted", "exit_code": -signal.SIGTERM}
    assert signal.SIGTERM not in child["blocked"] and signal.SIGINT not in child["blocked"]
    with pytest.raises(ProcessLookupError):
        os.kill(child["pid"], 0)


def test_standalone_repeated_term_reaps_group_before_inherited_lock_releases(tmp_path):
    lock_path = tmp_path / "jobs.lock"
    owner_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl = __import__("fcntl")
    contender = None
    try:
        fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        supervisor, parent_w, cancel_w, finish = _standalone_supervisor(
            tmp_path, child_ignores_term=True, lock_fd=owner_fd)
        os.close(owner_fd)
        owner_fd = -1
        contender = os.open(lock_path, os.O_RDWR)
        os.kill(supervisor.pid, signal.SIGTERM)
        time.sleep(0.1)
        os.kill(supervisor.pid, signal.SIGTERM)
        with pytest.raises(BlockingIOError):
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload, child = finish()
        assert payload == {"state": "interrupted", "reason": "supervisor_interrupted", "exit_code": -signal.SIGKILL}
        with pytest.raises(ProcessLookupError):
            os.kill(child["pid"], 0)
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        for descriptor in (owner_fd, contender):
            try: os.close(descriptor)
            except (OSError, TypeError): pass


def test_standalone_subreaper_adopts_descendant_before_releasing_lock(tmp_path):
    lock_path = tmp_path / "jobs.lock"
    owner_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl = __import__("fcntl")
    contender = None
    try:
        fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        supervisor, parent_w, cancel_w, finish = _standalone_supervisor(
            tmp_path, child_ignores_term=False, lock_fd=owner_fd, child_spawns_descendant=True)
        os.close(owner_fd)
        owner_fd = -1
        contender = os.open(lock_path, os.O_RDWR)
        os.kill(supervisor.pid, signal.SIGTERM)
        with pytest.raises(BlockingIOError):
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload, child = finish()
        assert payload == {"state": "interrupted", "reason": "supervisor_interrupted", "exit_code": -signal.SIGTERM}
        for pid in (child["pid"], child["descendant_pid"]):
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        for descriptor in (owner_fd, contender):
            try: os.close(descriptor)
            except (OSError, TypeError): pass


def test_cancel_pipe_cancels_a_real_running_child(monkeypatch, tmp_path):
    parent_w, cancel_w, finish = _run_worker(monkeypatch, _spec(tmp_path), tmp_path / "ready")
    os.write(cancel_w, b"x")
    code, payload, _, process = finish()
    assert code == 1
    assert payload == {"state": "canceled", "reason": "canceled", "exit_code": process.returncode}


def test_timeout_terminates_child(monkeypatch, tmp_path):
    parent_w, cancel_w, finish = _run_worker(monkeypatch, _spec(tmp_path, timeout_seconds=1), tmp_path / "ready")
    code, payload, _, process = finish()
    assert code == 1
    assert payload == {"state": "interrupted", "reason": "timeout", "exit_code": process.returncode}


def test_term_ignoring_child_is_killed_after_grace(monkeypatch, tmp_path):
    parent_w, cancel_w, finish = _run_worker(monkeypatch, _spec(tmp_path), tmp_path / "ready", ignore_term=True)
    os.write(cancel_w, b"x")
    started = time.monotonic()
    code, payload, _, process = finish()
    assert time.monotonic() - started >= 1.8
    assert code == 1
    assert payload == {"state": "canceled", "reason": "canceled", "exit_code": -signal.SIGKILL}
    assert process.returncode == -signal.SIGKILL


def test_cleanup_reaps_term_ignoring_descendant_after_leader_exit(monkeypatch, tmp_path):
    descendant = tmp_path / "descendant"
    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, _spec(tmp_path), tmp_path / "ready", descendant_marker=descendant)
    deadline = time.monotonic() + 2
    while not descendant.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert descendant.exists(), "controlled descendant did not become ready"
    descendant_pid = int(descendant.read_text(encoding="ascii"))
    os.write(cancel_w, b"x")
    code, payload, _, process = finish()
    assert code == 1
    assert payload == {"state": "canceled", "reason": "canceled", "exit_code": process.returncode}
    # The leader has already exited from SIGTERM, so this proves cleanup did
    # not stop at Popen.wait(): the same-group TERM-ignoring child is gone too.
    with pytest.raises(ProcessLookupError):
        os.kill(descendant_pid, 0)


@pytest.mark.parametrize("mutation", ["source", "snapshot"])
def test_postrun_provenance_mutation_cannot_publish_success(monkeypatch, tmp_path, mutation):
    source = ["test-source"]
    snapshot = [_verified_snapshot()]
    parent_w, cancel_w, finish = _run_worker(
        monkeypatch, _spec(tmp_path), tmp_path / "ready", exit_after=0.15,
        identity=lambda: source[0], snapshot=lambda *_: snapshot[0])
    if mutation == "source":
        source[0] = "changed-source"
    else:
        snapshot[0] = None
    code, payload, _, _ = finish()
    assert code == 1
    assert payload == {"state": "failed", "reason": "provenance_changed", "exit_code": 0}


@pytest.mark.parametrize("invalid", ["integrity", "source_identity"])
def test_incomplete_or_rehashed_export_never_publishes_success(monkeypatch, tmp_path, invalid):
    spec = _spec(tmp_path)
    output = Path(json.loads(spec.read_text(encoding="utf-8"))["output"])
    output.mkdir()
    manifest_source = "changed-source" if invalid == "source_identity" else "test-source"
    (output / "manifest.json").write_text(json.dumps({"source_identity": manifest_source}), encoding="utf-8")
    import qte.gui.artifacts
    integrity = "invalid" if invalid == "integrity" else "verified"
    monkeypatch.setattr(qte.gui.artifacts, "inspect_artifact",
                        lambda _output: SimpleNamespace(kind="research_export_v2", metadata={"integrity": integrity}))
    parent_w, cancel_w, finish = _run_worker(monkeypatch, spec, tmp_path / "ready", exit_after=0.1)
    code, payload, _, _ = finish()
    assert code == 1
    assert payload == {"state": "failed", "reason": "execution_failed", "exit_code": 0}


def test_verified_completion_wins_cancel_arriving_while_select_waits(monkeypatch, tmp_path):
    spec = _spec(tmp_path)
    output = Path(json.loads(spec.read_text(encoding="utf-8"))["output"])
    output.mkdir()
    (output / "manifest.json").write_text(json.dumps({"source_identity": "test-source"}), encoding="utf-8")
    import qte.gui.artifacts
    monkeypatch.setattr(qte.gui.artifacts, "inspect_artifact", lambda _output:
                        SimpleNamespace(kind="research_export_v2", metadata={"integrity": "verified"}))
    original_select, entered, release = job_worker.select.select, threading.Event(), threading.Event()
    calls = 0

    def delayed_select(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(2), "test did not release late cancellation"
        return original_select(*args, **kwargs)

    monkeypatch.setattr(job_worker.select, "select", delayed_select)
    parent_w, cancel_w, finish = _run_worker(monkeypatch, spec, tmp_path / "ready", exit_after=0.05)
    assert entered.wait(2), "worker never entered its cancellation wait"
    time.sleep(0.1)  # CLI has exited while the select wait remains deliberately held.
    os.write(cancel_w, b"x")
    release.set()
    code, payload, _, _ = finish()
    assert code == 0
    assert payload == {"state": "succeeded", "reason": None, "exit_code": 0}


@pytest.mark.parametrize(("raised", "expected"), [
    (OSError, {"state": "failed", "reason": "worker_setup_failed", "exit_code": None}),
    (job_worker._SupervisorSignal, {"state": "interrupted", "reason": "supervisor_interrupted", "exit_code": None}),
])
def test_supervisor_exception_or_signal_reaps_a_real_owned_child(monkeypatch, tmp_path, raised, expected):
    original_select, calls, trigger = job_worker.select.select, 0, threading.Event()

    def fail_after_preflight(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1 and trigger.is_set():
            raise raised("controlled supervisor failure") if raised is OSError else raised()
        return original_select(*args, **kwargs)

    monkeypatch.setattr(job_worker.select, "select", fail_after_preflight)
    parent_w, cancel_w, finish = _run_worker(monkeypatch, _spec(tmp_path), tmp_path / "ready")
    trigger.set()
    code, payload, _, process = finish()
    assert code == 1
    if raised is job_worker._SupervisorSignal:
        expected = {**expected, "exit_code": process.returncode}
    assert payload == expected
    assert process.poll() is not None
