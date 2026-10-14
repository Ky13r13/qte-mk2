"""Explicit persistent artifact registry; never scans for candidates."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from pathlib import Path
import re
from uuid import uuid4

from .artifact_io import ArtifactIOError, checked_relative, inventory, read_stable, validate_root
from .artifacts import Artifact, ArtifactError, inspect_artifact, invalid_artifact
from .disclosures import DisclosureJournal


# Schema-v2 records the same engine facts in its legacy compatibility files and
# richer owned-result tables.  Protection applies to the fact, not whichever
# serialization a caller happens to request.
V2_PROJECTION_GROUPS = (
    ("equity.csv", "equity_events.csv"),
    ("sampled_equity.csv", "sampled_equity_events.csv"),
    ("fills.csv", "fill_events.csv"),
    ("orders.csv", "order_snapshots.csv"),
)


class Catalog:
    def __init__(self, repository: Path) -> None:
        self.repository = Path(repository)
        self.database = self.repository / ".cache/qte-gui/catalog.sqlite3"
        self.disclosures = DisclosureJournal(self.repository)
        self._verification_cache: dict[str, tuple[tuple, Artifact]] = {}
        try:
            from qte.provenance import source_identity
            self._source_identity = source_identity()
        except Exception:
            self._source_identity = None

    def _connect(self, create: bool = False):
        self._validate_catalog_storage()
        if not self.database.exists() and not create: return None
        if create: self._ensure_cache()
        db = sqlite3.connect(self.database if create else f"file:{self.database}?mode=ro", uri=not create)
        db.row_factory = sqlite3.Row
        if create:
            db.execute("CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, registered_at TEXT NOT NULL)")
        elif db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone() is None:
            # The shared UI database may have been initialized by jobs first.
            db.close()
            return None
        return db

    def _validate_catalog_storage(self) -> None:
        for path in (self.database.parent.parent, self.database.parent, self.database,
                     Path(f"{self.database}-wal"), Path(f"{self.database}-shm"), Path(f"{self.database}-journal")):
            if path.is_symlink(): raise ArtifactError("catalog_forbidden", 403, "Catalog storage is unavailable.")

    def _ensure_cache(self) -> None:
        current = self.repository
        for part in self.database.parent.relative_to(self.repository).parts:
            current = current / part
            if current.is_symlink():
                raise ArtifactError("catalog_forbidden", 403, "Catalog storage is unavailable.")
            current.mkdir(exist_ok=True)

    def register(self, relative_path: str) -> dict:
        relative_path = self._canonical_relative(relative_path)
        self._require_finalized_job(relative_path)
        try: root = validate_root(self.repository, relative_path)
        except ArtifactIOError as exc: raise ArtifactError("artifact_forbidden", 403, "Artifact root is unavailable.") from exc
        try:
            artifact = inspect_artifact(root)
        except ArtifactError as exc:
            if exc.code not in {"artifact_invalid", "artifact_unsupported"}: raise
            artifact = invalid_artifact(root, exc.code)
        self._record_protection(artifact)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect(True) as db:
            row = db.execute("SELECT id,registered_at FROM artifacts WHERE path=?", (relative_path,)).fetchone()
            if row is None:
                artifact_id = uuid4().hex
                db.execute("INSERT INTO artifacts VALUES (?,?,?)", (artifact_id, relative_path, now))
            else: artifact_id, now = row["id"], row["registered_at"]
        return self._dto(artifact_id, relative_path, now, artifact)

    def _row(self, artifact_id: str):
        db = self._connect()
        if db is None: raise ArtifactError("artifact_not_found", 404, "Artifact unavailable.")
        try: row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        finally: db.close()
        if row is None: raise ArtifactError("artifact_not_found", 404, "Artifact unavailable.")
        return row

    def inspect(self, artifact_id: str) -> Artifact:
        row = self._row(artifact_id)
        self._require_finalized_job(row["path"])
        try: root = validate_root(self.repository, row["path"])
        except ArtifactIOError as exc: raise ArtifactError("artifact_unavailable", 404, "Artifact unavailable.") from exc
        try:
            snaps = inventory(root)
            fingerprint = tuple((name, value.size, value.mtime_ns, value.ctime_ns, value.device, value.inode)
                                for name, value in snaps.items())
            cached = self._verification_cache.get(artifact_id)
            if cached and cached[0] == fingerprint:
                self._record_protection(cached[1])
                return cached[1]
            artifact = inspect_artifact(root, snaps)
            self._verification_cache[artifact_id] = (fingerprint, artifact)
            self._record_protection(artifact)
            return artifact
        except ArtifactIOError as exc:
            raise ArtifactError("artifact_unavailable", 404, "Artifact unavailable.") from exc
        except ArtifactError as exc:
            if exc.code not in {"artifact_invalid", "artifact_unsupported"}: raise
            artifact = invalid_artifact(root, exc.code)
            self._record_protection(artifact)
            return artifact

    def get(self, artifact_id: str) -> dict:
        row = self._row(artifact_id)
        try: artifact = self.inspect(artifact_id)
        except ArtifactError as exc:
            if exc.code != "artifact_unavailable": raise
            return self._unavailable(row)
        return self._dto(row["id"], row["path"], row["registered_at"], artifact)

    def list(self, offset: int = 0, limit: int = 25) -> tuple[list[dict], int]:
        if offset < 0 or not 1 <= limit <= 200: raise ArtifactError("invalid_query", 400, "Invalid catalog query.")
        db = self._connect()
        if db is None: return [], 0
        try:
            total = db.execute("SELECT count(*) FROM artifacts").fetchone()[0]
            rows = db.execute("SELECT * FROM artifacts ORDER BY registered_at,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        finally: db.close()
        values = []
        for row in rows:
            try: values.append(self._dto(row["id"], row["path"], row["registered_at"], self.inspect(row["id"]), False))
            except ArtifactError as exc:
                if exc.code != "artifact_unavailable": raise
                values.append(self._unavailable(row))
        return values, total

    def reveal(self, artifact_id: str, confirmation: str) -> dict:
        if confirmation != "reveal outside protocol":
            raise ArtifactError("confirmation_required", 400, "Explicit holdout confirmation is required.")
        artifact = self.inspect(artifact_id)
        identities = self._effective_protected(artifact) + artifact.context_identities()
        if not identities:
            raise ArtifactError("holdout_not_present", 400, "Artifact has no protected holdout contents.")
        try: self.disclosures.reveal(identities)
        except OSError as exc: raise ArtifactError("disclosure_unavailable", 503, "Disclosure storage is unavailable.") from exc
        return self.get(artifact_id)

    def read_file(self, artifact_id: str, file_id: str) -> tuple[bytes, dict]:
        artifact = self.inspect(artifact_id)
        name, snap = artifact.path_for_id(file_id)
        meta = next(x for x in self._files(artifact) if x["id"] == file_id)
        if not meta["available"]:
            raise ArtifactError("holdout_protected", 403, "Holdout disclosure is required.")
        try: data = read_stable(artifact.root, name, expected=snap)
        except ArtifactIOError as exc: raise ArtifactError("artifact_changed", 409, "Artifact changed while being read.") from exc
        expected_hash = artifact.metadata.get("file_hashes", {}).get(name)
        from hashlib import sha256
        if expected_hash is None or sha256(data).hexdigest() != expected_hash:
            raise ArtifactError("artifact_changed", 409, "Artifact changed while being read.")
        return data, meta

    def experiment_view(self, artifact_id: str, timeframe: str | None = None,
                        table: str | None = None, window: str | None = None,
                        offset: int = 0, limit: int = 25) -> dict:
        """Fixed safe-role projection; never a raw protection override.

        The projector internally verifies and filters mixed lab metadata. It
        cannot accept paths, arbitrary field lists or executable callbacks.
        Raw downloads still require read_file authorization and disclosure.
        """
        from .lab_views import project_lab
        row = self._row(artifact_id)
        original = self.inspect(artifact_id)
        projected = project_lab(original, timeframe, table, window, offset, limit)
        latest = self.inspect(artifact_id)
        if latest.snapshots != original.snapshots or latest.metadata != original.metadata:
            raise ArtifactError("artifact_changed", 409, "Artifact changed while the view was being read.")
        if table is not None:
            return {**projected, "artifact_id": artifact_id}
        metadata = self._dto(artifact_id, row["path"], row["registered_at"], latest, False)
        status = {key: metadata["status"][key] for key in
                  ("export", "integrity", "evidence", "selection", "holdout_evaluation", "disclosure", "code_match")}
        status["combined_status"] = metadata["status"]["combined_status"]
        return {**projected, "id": artifact_id, "name": metadata["name"],
                "kind": latest.kind, "status": status}

    def authorize_job_inputs(self, prepared) -> dict:
        """Check known protection without revealing input bytes or importing roots.

        Refresh registered ancestors so a newly changed file cannot retain a
        stale classification. Copied bytes also inherit durable hash protection.
        Unknown local inputs are not proof of untouched research data.
        """
        self._validate_job_disclosure_storage()
        paths, hashes, synthetic = self._job_input_sources(prepared)
        db = self._connect()
        if db is not None:
            try: rows = db.execute("SELECT id,path FROM artifacts").fetchall()
            finally: db.close()
            for row in rows:
                prefix = row["path"].rstrip("/") + "/"
                if any(path.startswith(prefix) for path in paths):
                    self.inspect(row["id"])
        identities = sorted({f"file-sha256:{value}" for value in hashes})
        protected = [value for value in identities if self._job_input_protected(value)]
        if protected and not self.disclosures.revealed(protected):
            raise ArtifactError("holdout_protected", 403, "Input disclosure is required before execution.")
        result = {"input_identities": identities, "protected_input_identities": protected,
                  "input_disclosure": "revealed_outside_protocol" if protected else
                                      "not_required" if synthetic else "unknown"}
        if synthetic:
            result["evidence"] = "synthetic_only"
        return result

    def _job_input_sources(self, prepared) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
        """Null sources are allowed only for the explicit inline-form contract.

        In particular, a synthetic lab has a real configuration identity to
        authorize, but no invented data path, empty-file hash or source filename.
        Keep the legacy path-job contract strict and unchanged.
        """
        def invalid():
            raise ArtifactError("artifact_invalid", 422, "Input identities are invalid.")

        config_path = getattr(prepared, "original_config_path", None)
        data_path = getattr(prepared, "original_data_path", None)
        config_hash = getattr(prepared, "config_sha256", None)
        data_hash = getattr(prepared, "data_sha256", None)
        synthetic = False
        if hasattr(prepared, "config_origin") or hasattr(prepared, "kind"):
            origin = getattr(prepared, "config_origin", None)
            kind = getattr(prepared, "kind", None)
            if (not isinstance(origin, dict)
                    or set(origin) != {"kind", "path_base", "form_schema_version"}
                    or origin.get("kind") != "inline_form"
                    or origin.get("path_base") != "repository_root"
                    or type(origin.get("form_schema_version")) is not int
                    or origin["form_schema_version"] != 1
                    or not isinstance(kind, str) or kind not in {"research_run", "strategy_lab", "dataset_validate", "download_alpaca"}
                    or config_path is not None):
                invalid()
            synthetic = kind == "strategy_lab"
            if kind in {"strategy_lab", "download_alpaca"}:
                if any(getattr(prepared, field, False) is not None for field in
                       ("original_data_path", "adapter", "data_bytes", "data_sha256")):
                    invalid()
                paths, hashes = (), (config_hash,)
            else:
                adapter = getattr(prepared, "adapter", None)
                if (not isinstance(adapter, str) or adapter not in {"csv", "alpaca"}
                        or not isinstance(getattr(prepared, "data_bytes", None), bytes)):
                    invalid()
                paths, hashes = (data_path,), (config_hash, data_hash)
        else:
            paths, hashes = (config_path, data_path), (config_hash, data_hash)
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in hashes):
            invalid()
        if any(not isinstance(path, str) or not path for path in paths):
            invalid()
        return tuple(self._canonical_relative(path) for path in paths), hashes, synthetic

    def _validate_job_disclosure_storage(self) -> None:
        current = self.repository
        for part in ("build", "gui", "disclosures"):
            current = current / part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise ArtifactError("disclosure_unavailable", 503, "Input disclosure history is unavailable.")

    def _job_input_protected(self, identity: str) -> bool:
        protected = self.disclosures.protected(identity)
        record = self.disclosures.root / f"protected_content-{self.disclosures.key(identity)}.json"
        if not protected and (record.exists() or record.is_symlink()):
            # Corrupt history is not evidence that copied contents are safe.
            raise ArtifactError("disclosure_unavailable", 503, "Input disclosure history is unavailable.")
        return protected

    def protect_job_output(self, relative_path: str, lineage: dict) -> None:
        """Persist derived-content protection; never create an implicit reveal."""
        if not re.fullmatch(r"build/gui/jobs/[0-9a-f]{32}/artifacts", relative_path):
            raise ArtifactError("artifact_forbidden", 403, "Job output is unavailable.")
        self._validate_job_disclosure_storage()
        identities = lineage.get("input_identities", [])
        protected = lineage.get("protected_input_identities", [])
        if (not isinstance(identities, list) or not isinstance(protected, list)
                or any(not isinstance(value, str) or not re.fullmatch(r"file-sha256:[0-9a-f]{64}", value)
                       for value in identities + protected) or not set(protected) <= set(identities)):
            raise ArtifactError("artifact_invalid", 422, "Job lineage is invalid.")
        # Include protections learned while the job was running. Partial/invalid
        # exports already fail closed in the ordinary catalog verification path.
        if not protected and not any(self._job_input_protected(value) for value in identities):
            return
        try: root = validate_root(self.repository, relative_path)
        except ArtifactIOError as exc:
            raise ArtifactError("artifact_forbidden", 403, "Job output is unavailable.") from exc
        artifact = inspect_artifact(root)
        output_identities = [f"file-sha256:{value}" for value in artifact.metadata.get("file_hashes", {}).values()]
        for value in output_identities:
            self._job_input_protected(value)
        try:
            self.disclosures.mark_protected(output_identities)
        except OSError as exc:
            raise ArtifactError("disclosure_unavailable", 503, "Output protection could not be recorded.") from exc
        if not output_identities or not all(self.disclosures.protected(value) for value in output_identities):
            raise ArtifactError("disclosure_unavailable", 503, "Output protection could not be verified.")

    def _require_finalized_job(self, relative_path: str) -> None:
        """Do not publish a CLI completion marker before lineage finalization."""
        relative_path = self._canonical_relative(relative_path)
        if not relative_path.startswith("build/gui/jobs/"):
            return
        match = re.fullmatch(r"build/gui/jobs/([0-9a-f]{32})/artifacts(?:/.*)?", relative_path)
        if match is None:
            raise ArtifactError("job_output_unavailable", 409, "Job output is not finalized.")
        self._validate_catalog_storage()
        if not self.database.is_file():
            raise ArtifactError("job_output_unavailable", 409, "Job output is not finalized.")
        try:
            db = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
            try:
                row = db.execute("SELECT state,output_path FROM gui_jobs WHERE id=?", (match[1],)).fetchone()
            finally: db.close()
        except sqlite3.Error as exc:
            raise ArtifactError("job_output_unavailable", 409, "Job output is not finalized.") from exc
        if row is None or row != ("succeeded", f"build/gui/jobs/{match[1]}/artifacts"):
            raise ArtifactError("job_output_unavailable", 409, "Job output is not finalized.")

    @staticmethod
    def _canonical_relative(relative_path: str) -> str:
        try:
            if not isinstance(relative_path, str): raise ArtifactIOError("invalid path")
            relative_path.encode("utf-8")
            return checked_relative(relative_path).as_posix()
        except (ArtifactIOError, UnicodeError, ValueError) as exc:
            raise ArtifactError("artifact_forbidden", 403, "Artifact root is unavailable.") from exc

    def _dto(self, artifact_id: str, relative: str, registered: str, artifact: Artifact,
             include_files: bool = True) -> dict:
        protected = self._effective_protected(artifact)
        disclosure_ids = protected + artifact.context_identities()
        status = {k: v for k, v in artifact.metadata.items()
                  if k not in ("identity", "protocol_ids", "source_identities", "errors", "file_hashes")}
        status["disclosure"] = "revealed_outside_protocol" if self.disclosures.any_revealed(disclosure_ids) else "unknown"
        source_ids = artifact.metadata.get("source_identities", [])
        status["code_match"] = ("same" if self._source_identity and source_ids and all(x == self._source_identity for x in source_ids)
                                else "different" if self._source_identity and source_ids else "unknown")
        status["combined_status"] = f"{status['export']}; {status['integrity']}; {status['evidence']}"
        match = re.search(r"(20\d{6})", Path(relative).name)
        date = f"{match[1][:4]}-{match[1][4:6]}-{match[1][6:]}" if match else None
        result = {"id": artifact_id, "name": Path(relative).name, "kind": artifact.kind, "artifact_date": date,
                "artifact_date_source": "path_hint" if date else "unknown",
                "relative_path": relative, "registered_at": registered, "file_count": len(artifact.snapshots),
                "identity": artifact.metadata["identity"], "has_protected": bool(protected),
                "status": status, "errors": artifact.metadata.get("errors", [])}
        file_rows = self._files(artifact)
        blocked = sum(1 for row in file_rows if row["protection"] != "unprotected" and not row["available"])
        result["blocked_protected_count"] = blocked
        result["needs_disclosure"] = blocked > 0
        if include_files: result["files"] = file_rows
        return result

    def _effective_protected(self, artifact: Artifact) -> list[str]:
        identities = set(artifact.protected_identities())
        identities.update(identity for identity in
            (f"file-sha256:{value}" for value in artifact.metadata.get("file_hashes", {}).values())
            if self.disclosures.protected(identity))
        identities.update(self._projection_protection(artifact, identities))
        return sorted(identities)

    def _files(self, artifact: Artifact) -> list[dict]:
        effective = set(self._effective_protected(artifact))
        hashes = artifact.metadata.get("file_hashes", {})
        rows = artifact.files(False)
        for row in rows:
            identity = f"file-sha256:{hashes.get(row['name'], '')}"
            if identity in effective:
                if row["protection"] == "unprotected": row["protection"] = "protected"
                row["available"] = self.disclosures.revealed([identity])
        return rows

    def _record_protection(self, artifact: Artifact) -> None:
        try:
            identities = set(artifact.protected_identities())
            identities.update(self._projection_protection(artifact, identities))
            self.disclosures.mark_protected(sorted(identities))
        except OSError as exc:
            raise ArtifactError("disclosure_unavailable", 503, "Disclosure storage is unavailable.") from exc

    def _projection_protection(self, artifact: Artifact, protected: set[str]) -> set[str]:
        if artifact.kind == "comparison_export_v1" and artifact.metadata.get("integrity") == "verified":
            hashes = artifact.metadata.get("file_hashes", {})
            # A CLI export can reuse previously protected data outside the GUI.
            # Its derived runs must not become a disclosure bypass. Intrinsic
            # mixed/test files do not trigger this: only nominally safe inputs.
            safe_inputs = {name for name in artifact.known - artifact.protected - artifact.mixed
                           if name.startswith("inputs/")}
            if any(f"file-sha256:{hashes[name]}" in protected or
                   self.disclosures.protected(f"file-sha256:{hashes[name]}") for name in safe_inputs):
                return {f"file-sha256:{value}" for value in hashes.values()}
            return set()
        if artifact.kind in {"dataset_validation_v1", "alpaca_download_v1"} and artifact.metadata.get("integrity") == "verified":
            identities = {f"file-sha256:{value}" for value in artifact.metadata.get("file_hashes", {}).values()}
            # Counts, symbols, timestamps and mappings are derived from raw data.
            # Copying a protected input into a validation export cannot reveal them.
            if any(value in protected or self.disclosures.protected(value) for value in identities):
                return identities
            return set()
        if artifact.kind != "research_export_v2" or artifact.metadata.get("integrity") != "verified":
            return set()
        hashes = artifact.metadata.get("file_hashes", {})
        expanded: set[str] = set()
        for names in V2_PROJECTION_GROUPS:
            identities = {f"file-sha256:{hashes[name]}" for name in names if name in hashes}
            if len(identities) != len(names):
                continue
            if any(identity in protected or self.disclosures.protected(identity) for identity in identities):
                expanded.update(identities)
        return expanded

    @staticmethod
    def _unavailable(row) -> dict:
        return {"id": row["id"], "name": Path(row["path"]).name, "kind": "unavailable",
                "artifact_date": None, "artifact_date_source": "unknown", "relative_path": row["path"],
                "registered_at": row["registered_at"], "file_count": 0, "identity": "unknown",
                "has_protected": False, "needs_disclosure": False, "blocked_protected_count": 0,
                "status": {"export": "incomplete", "integrity": "unchecked",
                "evidence": "unknown", "scenario_outcome": "unknown", "selection": "unknown",
                "holdout_evaluation": "unknown", "disclosure": "unknown", "code_match": "unknown",
                "combined_status": "unavailable"}, "errors": ["artifact_unavailable"], "files": []}
