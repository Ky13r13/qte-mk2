"""Explicit persistent artifact registry; never scans for candidates."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from pathlib import Path
import re
from uuid import uuid4

from .artifact_io import ArtifactIOError, inventory, read_stable, validate_root
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
