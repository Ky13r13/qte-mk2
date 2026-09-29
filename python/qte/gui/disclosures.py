"""Durable append-only holdout disclosure records."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from .artifact_io import ArtifactIOError, Snapshot, read_stable
from .artifacts import strict_json


class DisclosureJournal:
    def __init__(self, repository: Path) -> None:
        self.root = Path(repository) / "build/gui/disclosures"

    @staticmethod
    def key(identity: str) -> str:
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def revealed(self, identities: list[str]) -> bool:
        return bool(identities) and all(self._valid_record(identity, "revealed_outside_protocol")
                                        for identity in identities)

    def any_revealed(self, identities: list[str]) -> bool:
        return any(self.revealed([identity]) for identity in identities)

    def protected(self, identity: str) -> bool:
        return self._valid_record(identity, "protected_content")

    def mark_protected(self, identities: list[str]) -> None:
        identities = [identity for identity in identities if not self.protected(identity)]
        if not identities:
            return
        self._write(identities, "protected_content")

    def reveal(self, identities: list[str]) -> None:
        self._write(identities, "revealed_outside_protocol")

    def _write(self, identities: list[str], event: str) -> None:
        self._ensure_root()
        directory = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            for identity in identities:
                filename = f"{event}-{self.key(identity)}.json"
                record = {"schema_version": 1, "event": event, "identity": identity,
                          "recorded_at": datetime.now(timezone.utc).isoformat()}
                temporary = f".{uuid4().hex}.tmp"
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                             0o600, dir_fd=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(record, stream, sort_keys=True, separators=(",", ":")); stream.write("\n")
                    stream.flush(); os.fsync(stream.fileno())
                try:
                    os.link(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                except FileExistsError:
                    pass
                finally:
                    os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        finally: os.close(directory)

    def _ensure_root(self) -> None:
        current = self.root.parents[2]
        for part in self.root.relative_to(current).parts:
            current = current / part
            if current.is_symlink():
                raise OSError("symlinked disclosure directory")
            current.mkdir(exist_ok=True)

    def _valid_record(self, identity: str, event: str) -> bool:
        current = self.root.parents[2]
        for part in self.root.relative_to(current).parts:
            current = current / part
            if current.is_symlink() or not current.is_dir(): return False
        path = self.root / f"{event}-{self.key(identity)}.json"
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096: return False
            snapshot = Snapshot(path.name, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino)
            value = strict_json(read_stable(self.root, path.name, 4096, snapshot))
        except (ArtifactIOError, OSError, ValueError, UnicodeDecodeError):
            return False
        return (isinstance(value, dict) and type(value.get("schema_version")) is int and
                value.get("schema_version") == 1 and value.get("event") == event and
                value.get("identity") == identity and isinstance(value.get("recorded_at"), str) and
                set(value) == {"schema_version", "event", "identity", "recorded_at"})
