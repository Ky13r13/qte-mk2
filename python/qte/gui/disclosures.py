"""Durable append-only holdout disclosure records."""

from __future__ import annotations

from contextlib import suppress

from qte._cleanup import close_fd

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from uuid import uuid4

from .artifact_io import ArtifactIOError, Snapshot, read_stable
from .artifacts import strict_json


_RECORD_LIMIT = 4096


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
        self._require_write_platform()
        identities = [identity for identity in identities if not self.protected(identity)]
        if not identities:
            return
        self._write(identities, "protected_content")

    def reveal(self, identities: list[str]) -> None:
        self._write(identities, "revealed_outside_protocol")

    def _write(self, identities: list[str], event: str) -> None:
        self._require_write_platform()
        prepared = [self._prepared(identity, event) for identity in identities]
        if not prepared:
            return
        self._ensure_root()
        directory = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        primary = None
        try:
            for identity, filename, payload in prepared:
                self._publish(directory, identity, event, filename, payload)
            os.fsync(directory)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                os.close(directory)
            except OSError:
                if primary is None:
                    raise

    def _prepared(self, identity: str, event: str) -> tuple[str, str, str]:
        if type(identity) is not str or type(event) is not str:
            raise ValueError("disclosure identity and event must be strings")
        record = {"schema_version": 1, "event": event, "identity": identity,
                  "recorded_at": datetime.now(timezone.utc).isoformat()}
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        if len(payload.encode("utf-8")) > _RECORD_LIMIT:
            raise OSError("disclosure record exceeds the limit")
        return identity, f"{event}-{self.key(identity)}.json", payload

    def _publish(self, directory: int, identity: str, event: str, filename: str, payload: str) -> None:
        temporary = f".{uuid4().hex}.tmp"
        fd = None
        created = False
        primary = None
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                         0o600, dir_fd=directory)
            created = True
            try:
                stream = os.fdopen(fd, "w", encoding="utf-8")
            except BaseException as error:
                close_fd(fd)
                fd = None
                raise error
            fd = None
            with stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
            except FileExistsError:
                if not self._valid_record_at(directory, filename, identity, event):
                    raise OSError("disclosure record collision is invalid") from None
        except BaseException as error:
            primary = error
            raise
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    if primary is None:
                        raise
            if created:
                try:
                    with suppress(FileNotFoundError):
                        os.unlink(temporary, dir_fd=directory)
                except OSError:
                    if primary is None:
                        raise

    def _ensure_root(self) -> None:
        current = self.root.parents[2]
        for part in self.root.relative_to(current).parts:
            current = current / part
            if current.is_symlink():
                raise OSError("symlinked disclosure directory")
            current.mkdir(exist_ok=True)

    def _valid_record(self, identity: str, event: str) -> bool:
        if sys.platform.startswith("linux"):
            return self._valid_record_posix(identity, event)
        if sys.platform == "win32":
            return self._valid_record_windows(identity, event)
        return False

    def _valid_record_windows(self, identity: str, event: str) -> bool:
        filename = f"{event}-{self.key(identity)}.json"
        try:
            from ._artifact_io_windows import read_stable as windows_read_stable
            value = strict_json(windows_read_stable(self.root, filename, _RECORD_LIMIT, None))
        except (ArtifactIOError, ImportError, OSError, ValueError, UnicodeDecodeError):
            return False
        return self._matches(value, identity, event)

    def _valid_record_posix(self, identity: str, event: str) -> bool:
        current = self.root.parents[2]
        for part in self.root.relative_to(current).parts:
            current = current / part
            if current.is_symlink() or not current.is_dir(): return False
        path = self.root / f"{event}-{self.key(identity)}.json"
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > _RECORD_LIMIT: return False
            snapshot = Snapshot(path.name, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino)
            value = strict_json(read_stable(self.root, path.name, _RECORD_LIMIT, snapshot))
        except (ArtifactIOError, OSError, ValueError, UnicodeDecodeError):
            return False
        return self._matches(value, identity, event)

    @staticmethod
    def _require_write_platform() -> None:
        if not sys.platform.startswith("linux"):
            raise OSError("disclosure writes are unsupported on this platform")

    def _valid_record_at(self, directory: int, filename: str, identity: str, event: str) -> bool:
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                 getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
        fd = None
        try:
            fd = os.open(filename, flags, dir_fd=directory)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _RECORD_LIMIT:
                return False
            chunks, remaining = [], _RECORD_LIMIT + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk); remaining -= len(chunk)
            after = os.fstat(fd)
            payload = b"".join(chunks)
            if len(payload) > _RECORD_LIMIT or len(payload) != after.st_size or (info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns):
                return False
            value = strict_json(payload)
        except (OSError, ValueError, UnicodeDecodeError):
            return False
        finally:
            if fd is not None:
                close_fd(fd)
        return self._matches(value, identity, event)

    @staticmethod
    def _matches(value: object, identity: str, event: str) -> bool:
        return (isinstance(value, dict) and type(value.get("schema_version")) is int and
                value.get("schema_version") == 1 and value.get("event") == event and
                value.get("identity") == identity and isinstance(value.get("recorded_at"), str) and
                set(value) == {"schema_version", "event", "identity", "recorded_at"})
