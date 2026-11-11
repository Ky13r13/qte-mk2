from __future__ import annotations

import sqlite3

import pytest

from qte.gui.catalog import Catalog
from qte.gui.artifacts import ArtifactError
from qte.gui.jobs import JobManager
from qte.gui import sqlite as owned_sqlite


def test_readonly_uri_escapes_reserved_unicode_and_opens_real_database(tmp_path):
    path = tmp_path / "catalog #?% ü.sqlite3"
    with owned_sqlite.owned_connection(path) as db:
        db.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
        db.execute("INSERT INTO values_table VALUES ('committed')")
    uri = owned_sqlite.readonly_uri(path)
    assert uri.endswith("?mode=ro")
    assert "%20" in uri and "%23" in uri and "%3F" in uri and "%25" in uri
    assert "%C3%BC" in uri
    with owned_sqlite.owned_connection(path, readonly=True) as db:
        assert db.execute("SELECT value FROM values_table").fetchone()[0] == "committed"


def test_missing_readonly_database_fails_without_creation(tmp_path):
    missing = tmp_path / "missing #?.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        with owned_sqlite.owned_connection(missing, readonly=True):
            pass
    assert not missing.exists()


def test_owned_connection_commits_and_rolls_back(tmp_path):
    path = tmp_path / "transactions.sqlite3"
    with owned_sqlite.owned_connection(path) as db:
        db.execute("CREATE TABLE events (value TEXT NOT NULL)")
        db.execute("INSERT INTO events VALUES ('kept')")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")
    rolled_back = None
    with pytest.raises(RuntimeError, match="rollback"):
        with owned_sqlite.owned_connection(path) as rolled_back:
            rolled_back.execute("INSERT INTO events VALUES ('discarded')")
            raise RuntimeError("rollback")
    assert rolled_back is not None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        rolled_back.execute("SELECT 1")
    with owned_sqlite.owned_connection(path, readonly=True) as db:
        assert [row[0] for row in db.execute("SELECT value FROM events")] == ["kept"]


def test_deferred_foreign_key_commit_failure_closes_and_persists_nothing(tmp_path):
    path = tmp_path / "deferred.sqlite3"
    with owned_sqlite.owned_connection(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) "
                   "DEFERRABLE INITIALLY DEFERRED)")
    failed = None
    with pytest.raises(sqlite3.IntegrityError):
        with owned_sqlite.owned_connection(path) as failed:
            failed.execute("PRAGMA foreign_keys=ON")
            failed.execute("INSERT INTO child VALUES (7)")
    assert failed is not None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        failed.execute("SELECT 1")
    with owned_sqlite.owned_connection(path, readonly=True) as db:
        assert db.execute("SELECT count(*) FROM child").fetchone()[0] == 0


class _FakeConnection:
    def __init__(self, *, setup_error=False, commit_error=False):
        self.setup_error = setup_error
        self.commit_error = commit_error
        self.closed = False
        self.exited_with = None
        self._row_factory = None

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value):
        if self.setup_error:
            raise RuntimeError("row factory setup failed")
        self._row_factory = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exited_with = exc_type
        if exc_type is None and self.commit_error:
            raise RuntimeError("commit failed")
        return False

    def close(self):
        self.closed = True


@pytest.mark.parametrize("failure", [None, "setup", "body", "commit"])
def test_owned_connection_closes_after_success_and_every_post_open_error(monkeypatch, failure):
    connection = _FakeConnection(
        setup_error=failure == "setup", commit_error=failure == "commit")
    monkeypatch.setattr(owned_sqlite.sqlite3, "connect", lambda *args, **kwargs: connection)
    if failure is None:
        with owned_sqlite.owned_connection("unused") as db:
            assert db is connection
    elif failure == "body":
        with pytest.raises(RuntimeError, match="body failed"):
            with owned_sqlite.owned_connection("unused"):
                raise RuntimeError("body failed")
        assert connection.exited_with is RuntimeError
    else:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            with owned_sqlite.owned_connection("unused"):
                pass
    assert connection.closed


def test_catalog_and_jobs_share_owned_database_and_keep_idempotency_unique(tmp_path):
    root = tmp_path / "build" / "manual"
    root.mkdir(parents=True)
    (root / "attachment.bin").write_bytes(b"protected")
    catalog = Catalog(tmp_path)
    artifact = catalog.register("build/manual")
    assert catalog.list()[1] == 1

    manager = JobManager(tmp_path, catalog)
    values = (
        "a" * 32, "same-idempotency-key", "fingerprint", "request", "benchmark",
        "queued", "queued", None, 1, None, None, 30, "source", "{}", "{}", "{}",
        None, None, None, None, None, f"build/gui/jobs/{'a' * 32}/artifacts", "accepted\n",
    )
    statement = """INSERT INTO gui_jobs
      (id,idempotency_key,fingerprint,request_id,kind,state,phase,reason,created_ns,start_ns,end_ns,
       timeout_seconds,source_identity,input_hashes,lineage,snapshot,launch_identity,supervisor_pid,
       executable,cwd,exit_code,output_path,log) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    with manager._connect() as db:
        manager._schema(db)
        db.execute(statement, values)
    duplicate = list(values)
    duplicate[0] = "b" * 32
    with pytest.raises(sqlite3.IntegrityError):
        with manager._connect() as db:
            db.execute(statement, duplicate)
    with manager._connect() as jobs_db:
        assert jobs_db.execute("SELECT count(*) FROM gui_jobs").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        jobs_db.execute("SELECT 1")
    with catalog._connect() as catalog_db:
        assert catalog_db is not None
        assert catalog_db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        catalog_db.execute("SELECT 1")
    assert catalog.get(artifact["id"])["id"] == artifact["id"]


def test_catalog_readonly_uri_handles_escaped_checkout_and_rejects_writes(tmp_path):
    repository = tmp_path / "checkout #?% ü"
    repository.mkdir()
    catalog = Catalog(repository)
    with catalog._connect(True) as db:
        db.execute("INSERT INTO artifacts VALUES (?,?,?)", ("a" * 32, "build/item", "now"))
    assert catalog.database.is_file()
    with catalog._connect() as db:
        assert db is not None
        assert db.execute("SELECT path FROM artifacts").fetchone()[0] == "build/item"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("INSERT INTO artifacts VALUES (?,?,?)", ("b" * 32, "build/other", "now"))
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")
    assert list(repository.rglob("*.sqlite3")) == [catalog.database]


@pytest.mark.parametrize("kind", [None, "research_run", "test_run", "benchmark"])
def test_finalized_job_lookup_uses_escaped_uri_and_preserves_kind_gate(tmp_path, kind):
    repository = tmp_path / "checkout #?% ü"
    repository.mkdir()
    catalog = Catalog(repository)
    catalog.database.parent.mkdir(parents=True)
    job_id = "a" * 32
    output = f"build/gui/jobs/{job_id}/artifacts"
    # Some historical catalogs have no artifacts table or job-kind column.
    with owned_sqlite.owned_connection(catalog.database) as db:
        db.execute("CREATE TABLE gui_jobs (id TEXT, state TEXT, output_path TEXT)")
        db.execute("INSERT INTO gui_jobs VALUES (?,?,?)", (job_id, "succeeded", output))
        if kind is not None:
            db.execute("ALTER TABLE gui_jobs ADD COLUMN kind TEXT")
            db.execute("UPDATE gui_jobs SET kind=?", (kind,))
    if kind in {"test_run", "benchmark"}:
        with pytest.raises(ArtifactError) as error:
            catalog._require_finalized_job(output)
        assert (error.value.code, error.value.status) == ("job_output_unavailable", 409)
    else:
        catalog._require_finalized_job(output)
