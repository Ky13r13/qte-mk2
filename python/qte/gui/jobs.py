"""Durable, single-run local research job scheduler."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .artifacts import ArtifactError


class JobError(Exception):
    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message); self.code = code; self.status = status; self.message = message


def _plain(value):
    if is_dataclass(value): value = asdict(value)
    if isinstance(value, dict): return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [_plain(item) for item in value]
    if isinstance(value, Path): return value.as_posix()
    if isinstance(value, bytes): return {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if value is None or type(value) in (bool, int, float, str): return value
    raise JobError("invalid_job_input", 400, "Prepared job inputs are invalid.")


def _fingerprint(kind: str, prepared, timeout: int) -> str:
    canonical = getattr(prepared, "canonical_config_sha256", None)
    data = getattr(prepared, "data_sha256", None)
    if kind == "research_run":
        # Keep the accepted G5a research formula byte-for-byte unchanged.
        if not all(isinstance(value, str) for value in (canonical, data)):
            raise JobError("invalid_job_input", 400, "Prepared job inputs are invalid.")
    elif kind == "strategy_lab":
        if not isinstance(canonical, str) or data is not None:
            raise JobError("invalid_job_input", 400, "Prepared job inputs are invalid.")
    else:
        raise JobError("invalid_job_input", 400, "Prepared job inputs are invalid.")
    encoded = json.dumps({"kind": kind, "timeout_seconds": timeout,
                          "canonical_config_sha256": canonical, "data_sha256": data},
                         sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class RunningJob:
    def __init__(self, process: subprocess.Popen, liveness: int, cancel: int, result: int) -> None:
        self.process, self._liveness, self._cancel, self._result = process, liveness, cancel, result
        self._fd_guard = threading.Lock()

    def cancel(self) -> None:
        # Keep the descriptor valid while writing. wait() invalidates every
        # descriptor under this same lock before closing, so a concurrent late
        # manager.cancel cannot write into a recycled unrelated FD.
        with self._fd_guard:
            fd, self._cancel = self._cancel, -1
            if fd < 0:
                return
            try: os.write(fd, b"x")
            except OSError: pass
            finally:
                try: os.close(fd)
                except OSError: pass

    def close_owner(self) -> None:
        with self._fd_guard:
            fd, self._liveness = self._liveness, -1
            if fd < 0:
                return
            try: os.close(fd)
            except OSError: pass

    def _take_descriptors(self) -> tuple[int, int, int]:
        with self._fd_guard:
            result = (self._liveness, self._cancel, self._result)
            self._liveness = self._cancel = self._result = -1
            return result

    def wait(self) -> dict:
        self.process.wait()
        chunks = []
        while True:
            try: data = os.read(self._result, 4096)
            except OSError: break
            if not data: break
            chunks.append(data)
            if sum(map(len, chunks)) > 4096: break
        try:
            return _worker_result(json.loads(b"".join(chunks)))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return _worker_protocol_error()
        finally:
            for fd in self._take_descriptors():
                try: os.close(fd)
                except OSError: pass


def _worker_protocol_error() -> dict:
    return {"state": "failed", "reason": "worker_protocol_error", "exit_code": None}


def _worker_result(value) -> dict:
    """Accept only the fixed, sanitized worker completion protocol."""
    if not isinstance(value, dict) or set(value) != {"state", "reason", "exit_code"}:
        raise ValueError
    state, reason, exit_code = value["state"], value["reason"], value["exit_code"]
    allowed_reasons = {
        "succeeded": {None},
        "failed": {"provenance_changed", "execution_failed", "worker_setup_failed"},
        "canceled": {"canceled"},
        "interrupted": {"owner_lost", "timeout", "supervisor_interrupted"},
    }
    if state not in allowed_reasons:
        raise ValueError
    if (reason is not None and type(reason) is not str) or type(exit_code) not in {int, type(None)}:
        raise ValueError
    if reason not in allowed_reasons[state]:
        raise ValueError
    if state == "succeeded" and (reason is not None or exit_code != 0):
        raise ValueError
    return {"state": state, "reason": reason, "exit_code": exit_code}


class Supervisor:
    def __init__(self, repository: Path, lock_fd: int) -> None:
        self.repository, self.lock_fd = repository, lock_fd

    def launch(self, spec_path: Path, environment: dict[str, str]) -> RunningJob:
        parent_r, parent_w = os.pipe(); cancel_r, cancel_w = os.pipe(); result_r, result_w = os.pipe()
        for fd in (parent_w, cancel_w, result_r): os.set_inheritable(fd, False)
        argv = [str(Path(sys.executable).absolute()), "-m", "qte.gui.job_worker", str(spec_path),
                str(parent_r), str(cancel_r), str(result_w)]
        try:
            process = subprocess.Popen(argv, cwd=self.repository, env=environment, shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, pass_fds=(parent_r, cancel_r, result_w, self.lock_fd))
        except Exception:
            for fd in (parent_r, parent_w, cancel_r, cancel_w, result_r, result_w):
                try: os.close(fd)
                except OSError: pass
            raise
        for fd in (parent_r, cancel_r, result_w): os.close(fd)
        return RunningJob(process, parent_w, cancel_w, result_r)


class JobManager:
    def __init__(self, repository: Path, catalog, *, input_authorizer=None, output_protector=None,
                 supervisor_factory=Supervisor, clock=time.time_ns) -> None:
        self.repository, self.catalog = Path(repository).absolute(), catalog
        self.database = self.repository / ".cache/qte-gui/catalog.sqlite3"
        self.lock_path = self.repository / ".cache/qte-gui/jobs.lock"
        self.input_authorizer = input_authorizer or getattr(catalog, "authorize_job_inputs", None)
        self.output_protector = output_protector or getattr(catalog, "protect_job_output", None)
        self.supervisor_factory, self.clock = supervisor_factory, clock
        self._guard = threading.RLock(); self._condition = threading.Condition(self._guard)
        self._opened = False; self._closing = False; self._lock_fd = None; self._thread = None
        self._active: tuple[str, RunningJob] | None = None
        self._prepared: dict[str, object] = {}; self._snapshots: dict[str, dict] = {}

    def _open(self) -> None:
        with self._guard:
            if self._closing: raise JobError("jobs_unavailable", 503, "Job manager is closed.")
            if self._opened: return
            self._ensure_storage()
            lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try: fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(lock_fd); raise JobError("jobs_owner_active", 409, "Another job manager owns this repository.") from exc
            try:
                self._lock_fd = lock_fd
                with self._connect() as db:
                    self._schema(db)
                    now = self.clock()
                    db.execute("UPDATE gui_jobs SET state='interrupted',phase='finished',reason='owner_restart',end_ns=? "
                               "WHERE state IN ('queued','running')", (now,))
                if self._closing:
                    raise JobError("jobs_unavailable", 503, "Job manager is closed.")
                self._opened = True
                self._thread = threading.Thread(target=self._dispatch, name="qte-job-dispatch", daemon=True)
                self._thread.start()
            except BaseException:
                self._opened = False
                self._thread = None
                self._lock_fd = None
                try: fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError: pass
                try: os.close(lock_fd)
                except OSError: pass
                raise

    def _ensure_storage(self) -> None:
        current = self.repository
        for part in Path(".cache/qte-gui").parts:
            current /= part
            if current.is_symlink(): raise JobError("jobs_forbidden", 403, "Job storage is unavailable.")
            current.mkdir(exist_ok=True)
        for path in (self.database, Path(f"{self.database}-wal"), Path(f"{self.database}-shm"),
                     Path(f"{self.database}-journal"), self.lock_path):
            try: info = path.lstat()
            except FileNotFoundError: continue
            if not stat.S_ISREG(info.st_mode):
                raise JobError("jobs_forbidden", 403, "Job storage is unavailable.")

    def _connect(self):
        self._ensure_storage()
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _schema(db) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS gui_jobs (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
          idempotency_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
          request_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, phase TEXT NOT NULL,
          reason TEXT, created_ns INTEGER NOT NULL, start_ns INTEGER, end_ns INTEGER,
          timeout_seconds INTEGER NOT NULL, source_identity TEXT NOT NULL, input_hashes TEXT NOT NULL,
          lineage TEXT NOT NULL, snapshot TEXT NOT NULL, launch_identity TEXT, supervisor_pid INTEGER,
          executable TEXT, cwd TEXT, exit_code INTEGER, output_path TEXT NOT NULL, log TEXT NOT NULL)""")

    def submit(self, request_id, kind, prepared, idempotency_key, timeout_seconds) -> dict:
        if (not isinstance(kind, str) or kind not in {"research_run", "strategy_lab"}
                or not isinstance(request_id, str) or not request_id):
            raise JobError("invalid_job_request", 400, "Invalid job request.")
        if (not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 128
                or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in idempotency_key)):
            raise JobError("invalid_idempotency_key", 400, "Invalid idempotency key.")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800:
            raise JobError("invalid_timeout", 400, "Invalid job timeout.")
        try:
            from .inline_inputs import PreparedInlineInputs
        except Exception:
            PreparedInlineInputs = ()  # type: ignore[assignment]
        if isinstance(prepared, PreparedInlineInputs):
            if prepared.kind != kind:
                raise JobError("invalid_job_request", 400, "Invalid job request.")
        elif kind == "strategy_lab":
            raise JobError("invalid_job_request", 400, "Invalid job request.")
        if kind == "strategy_lab" and any(value is not None for value in (
                prepared.adapter, prepared.original_data_path, prepared.data_bytes, prepared.data_sha256,
                prepared.original_config_path)):
            raise JobError("invalid_job_request", 400, "Invalid job request.")
        fingerprint = _fingerprint(kind, prepared, timeout_seconds)
        self._open()
        with self._condition:
            if self._closing:
                raise JobError("jobs_unavailable", 503, "Job manager is closed.")
            with self._connect() as db:
                row = db.execute("SELECT * FROM gui_jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if row is not None:
                    if row["fingerprint"] != fingerprint:
                        raise JobError("idempotency_conflict", 409, "Idempotency key has different inputs.")
                    return self._dto(row)
                queued = db.execute("SELECT count(*) FROM gui_jobs WHERE state='queued'").fetchone()[0]
                if queued >= 20: raise JobError("job_queue_full", 429, "Job queue is full.")
            if not callable(self.input_authorizer) or not callable(self.output_protector):
                raise JobError("job_authorization_unavailable", 503, "Job input authorization is unavailable.")
            try: lineage = self.input_authorizer(prepared)
            except Exception as exc:
                if all(hasattr(exc, name) for name in ("code", "status", "message")):
                    raise JobError(exc.code, exc.status, exc.message) from None
                raise JobError("job_authorization_unavailable", 503, "Job input authorization is unavailable.") from None
            if not isinstance(lineage, dict):
                raise JobError("job_authorization_unavailable", 503, "Job input authorization is unavailable.")
            job_id = uuid4().hex
            try:
                from .job_inputs import write_snapshot
                snapshot = write_snapshot(self.repository, job_id, prepared)
            except Exception as exc:
                if all(hasattr(exc, name) for name in ("code", "status", "message")):
                    raise JobError(exc.code, exc.status, exc.message) from None
                raise JobError("job_snapshot_failed", 409, "Job input snapshot failed.") from exc
            snapshot_value = _plain(snapshot)
            hashes = self._value(snapshot_value, "hashes", "input_hashes")
            snapshot_hashes = self._value(snapshot_value, "snapshot_hashes")
            try:
                from qte.provenance import source_identity as current_source_identity
                source_identity = current_source_identity()
            except Exception as exc:
                raise JobError("source_identity_unavailable", 503, "Source identity is unavailable.") from exc
            if not isinstance(source_identity, str) or not isinstance(hashes, dict) or not isinstance(snapshot_hashes, dict):
                raise JobError("job_snapshot_failed", 409, "Job input snapshot failed.")
            now = self.clock(); output = f"build/gui/jobs/{job_id}/artifacts"
            log = "accepted\n"
            with self._connect() as db:
                try:
                    db.execute("""INSERT INTO gui_jobs
                        (id,idempotency_key,fingerprint,request_id,kind,state,phase,reason,created_ns,start_ns,end_ns,
                         timeout_seconds,source_identity,input_hashes,lineage,snapshot,launch_identity,supervisor_pid,
                         executable,cwd,exit_code,output_path,log) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (job_id, idempotency_key, fingerprint, request_id, kind, "queued", "queued", None,
                         now, None, None, timeout_seconds, source_identity,
                         self._json(hashes), self._json(lineage), self._json(snapshot_value), None, None,
                         None, None, None, output, log))
                except sqlite3.IntegrityError as exc:
                    raise JobError("idempotency_conflict", 409, "Idempotency key has different inputs.") from exc
            self._prepared[job_id], self._snapshots[job_id] = prepared, snapshot_value
            self._condition.notify_all()
            return self.get(job_id)

    @staticmethod
    def _value(value: dict, *names):
        for name in names:
            if name in value: return value[name]
        return None

    @staticmethod
    def _json(value) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def _dispatch(self) -> None:
        while True:
            with self._condition:
                if self._closing: return
                with self._connect() as db:
                    row = db.execute("SELECT * FROM gui_jobs WHERE state='queued' ORDER BY sequence LIMIT 1").fetchone()
                if row is None:
                    self._condition.wait(0.5); continue
                job_id = row["id"]
                prepared = self._prepared.get(job_id); snapshot = self._snapshots.get(job_id)
                if prepared is None or snapshot is None:
                    self._finish(job_id, "interrupted", "accepted_process_lost", None); continue
                try:
                    from .job_inputs import verify_snapshot
                    verified = verify_snapshot(self.repository, job_id, snapshot["snapshot_hashes"])
                    if not self._snapshot_matches_kind(verified, row["kind"], snapshot):
                        raise JobError("job_snapshot_failed", 409, "Job snapshot invalid.")
                    current_lineage = self.input_authorizer(prepared)
                    if not isinstance(current_lineage, dict):
                        raise JobError("job_authorization_unavailable", 503, "Job input authorization is unavailable.")
                    with self._connect() as db:
                        db.execute("UPDATE gui_jobs SET lineage=? WHERE id=? AND state='queued'",
                                   (self._json(current_lineage), job_id))
                    row = dict(row); row["lineage"] = self._json(current_lineage)
                    running = self._launch(row, snapshot)
                except ArtifactError as exc:
                    self._finish(job_id, "failed", exc.code, None); continue
                except JobError as exc:
                    self._finish(job_id, "failed", exc.code, None); continue
                except Exception as exc:
                    reason = exc.code if all(hasattr(exc, name) for name in ("code", "status", "message")) else "launch_failed"
                    self._finish(job_id, "failed", reason, None); continue
                self._active = (job_id, running)
            try:
                result = _worker_result(running.wait())
            except (TypeError, ValueError):
                result = _worker_protocol_error()
            with self._condition:
                self._active = None
                state, reason, exit_code = result.get("state"), result.get("reason"), result.get("exit_code")
                if state == "succeeded":
                    try:
                        relative = row["output_path"]
                        self.output_protector(relative, json.loads(row["lineage"]))
                    except ArtifactError as exc:
                        state, reason = "failed", exc.code
                    except Exception:
                        state, reason = "failed", "output_protection_failed"
                self._finish(job_id, state, reason, exit_code)
                self._condition.notify_all()

    def _launch(self, row, snapshot) -> RunningJob:
        executable = Path(sys.executable).absolute()
        if executable.parent.name != "bin" or executable.parent.parent.name != ".venv":
            raise JobError("venv_required", 503, "Repository virtual environment is required.")
        job_root = self.repository / "build/gui/jobs" / row["id"]
        config = self._value(snapshot, "effective_config_path")
        hashes = self._value(snapshot, "snapshot_hashes")
        if config is None or not isinstance(hashes, dict): raise JobError("job_snapshot_failed", 409, "Job snapshot invalid.")
        config = self.repository / config
        environment = self._environment(job_root)
        spec = {"repository": self.repository.as_posix(), "executable": executable.as_posix(),
                "config": str(config), "output": (job_root / "artifacts").as_posix(),
                "job_id": row["id"], "kind": row["kind"], "snapshot_hashes": hashes,
                "source_identity": row["source_identity"], "timeout_seconds": row["timeout_seconds"],
                "environment": environment}
        spec_path = job_root / "worker.json"
        with spec_path.open("x", encoding="utf-8") as stream: json.dump(spec, stream, sort_keys=True); stream.write("\n")
        os.chmod(spec_path, 0o400)
        launch_id = uuid4().hex
        running = self.supervisor_factory(self.repository, self._lock_fd).launch(spec_path, environment)
        try:
            with self._connect() as db:
                db.execute("UPDATE gui_jobs SET state='running',phase='executing',start_ns=?,launch_identity=?,"
                           "supervisor_pid=?,executable=?,cwd=?,log=log||? WHERE id=? AND state='queued'",
                           (self.clock(), launch_id, running.process.pid, executable.as_posix(), self.repository.as_posix(),
                            "running\n", row["id"]))
        except Exception:
            running.cancel(); running.close_owner(); running.wait()
            raise
        return running

    @staticmethod
    def _snapshot_matches_kind(verified, kind: str, snapshot: dict) -> bool:
        """Require v2 verified kind/hash while retaining the v1 research shape."""
        try:
            from .inline_inputs import InlineSnapshotInputs
            from .job_inputs import SnapshotInputs
        except Exception:
            return False
        expected = snapshot.get("hashes", {}).get("canonical_config_sha256")
        if not isinstance(expected, str):
            return False
        if isinstance(verified, InlineSnapshotInputs):
            return (verified.kind == kind and verified.hashes.get("canonical_config_sha256") == expected)
        # v1 snapshots have no kind marker and are research-only.
        return (kind == "research_run" and isinstance(verified, SnapshotInputs)
                and verified.hashes.get("canonical_config_sha256") == expected)

    @staticmethod
    def _environment(job_root: Path) -> dict[str, str]:
        temp, cache = job_root / "tmp", job_root / "cache"
        temp.mkdir(exist_ok=True); cache.mkdir(exist_ok=True)
        return {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": temp.as_posix(), "XDG_CACHE_HOME": cache.as_posix()}

    def _finish(self, job_id, state, reason, exit_code) -> None:
        message = {"succeeded": "verified output complete", "canceled": "canceled"}.get(state, "job failed") + "\n"
        with self._connect() as db:
            db.execute("UPDATE gui_jobs SET state=?,phase='finished',reason=?,end_ns=?,exit_code=?,"
                       "log=substr(log||?,1,65536) WHERE id=? AND state IN ('queued','running')",
                       (state, reason, self.clock(), exit_code, message, job_id))
        self._prepared.pop(job_id, None); self._snapshots.pop(job_id, None)

    def get(self, job_id: str) -> dict:
        self._open()
        with self._connect() as db: row = db.execute("SELECT * FROM gui_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None: raise JobError("job_not_found", 404, "Job unavailable.")
        return self._dto(row)

    def list(self, offset=0, limit=25):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise JobError("invalid_query", 400, "Invalid job query.")
        self._open()
        with self._connect() as db:
            total = db.execute("SELECT count(*) FROM gui_jobs").fetchone()[0]
            rows = db.execute("SELECT * FROM gui_jobs ORDER BY sequence DESC LIMIT ? OFFSET ?",
                              (limit, offset)).fetchall()
        return [self._dto(row) for row in rows], total

    def log(self, job_id: str, offset=0, limit=65536) -> dict:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536:
            raise JobError("invalid_query", 400, "Invalid log query.")
        self._open()
        with self._connect() as db: row = db.execute("SELECT log FROM gui_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None: raise JobError("job_not_found", 404, "Job unavailable.")
        text = row["log"]; part = text[offset:offset + limit]
        return {"text": part, "offset": offset, "next_offset": offset + len(part), "total": len(text)}

    def cancel(self, job_id: str) -> dict:
        self._open()
        with self._condition:
            with self._connect() as db: row = db.execute("SELECT * FROM gui_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None: raise JobError("job_not_found", 404, "Job unavailable.")
            if row["state"] == "queued": self._finish(job_id, "canceled", "canceled", None)
            elif row["state"] == "running" and self._active and self._active[0] == job_id:
                self._active[1].cancel()
            return self.get(job_id)

    @staticmethod
    def _dto(row) -> dict:
        lineage = json.loads(row["lineage"])
        return {"id": row["id"], "kind": row["kind"], "state": row["state"], "phase": row["phase"],
            "reason": row["reason"], "created_ns": row["created_ns"], "start_ns": row["start_ns"],
            "end_ns": row["end_ns"], "timeout_seconds": row["timeout_seconds"],
            "source_identity": row["source_identity"], "input_hashes": json.loads(row["input_hashes"]),
            "input_lineage": lineage, "input_disclosure": lineage.get("input_disclosure", "unknown"),
            "request_id": row["request_id"], "worker_launch_identity": row["launch_identity"],
            "supervisor_pid": row["supervisor_pid"], "executable": row["executable"], "cwd": row["cwd"],
            "exit_code": row["exit_code"], "output_path": row["output_path"]}

    def close(self) -> None:
        with self._condition:
            if not self._opened: self._closing = True; return
            self._closing = True
            if self._active:
                self._active[1].cancel(); self._active[1].close_owner()
            self._condition.notify_all()
        if self._thread: self._thread.join(timeout=10)
        if self._lock_fd is not None and (self._thread is None or not self._thread.is_alive()):
            try: fcntl.flock(self._lock_fd, fcntl.LOCK_UN); os.close(self._lock_fd)
            except OSError: pass
            self._lock_fd = None
