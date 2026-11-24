"""Check recorded output digests of offline research studies without rerunning them."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat

from qte.json_input import strict_json


def _output_path(directory: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if (not parts or name != "/".join(parts) or PureWindowsPath(name).anchor
            or "\\" in name or any(part.startswith(".") for part in parts)):
        raise ValueError(f"invalid relative output name: {name!r}")
    path = directory
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlink in recorded output path: {name}")
    return path


def _digest(path: Path) -> str:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"expected a regular output file: {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        opened = os.fstat(stream.fileno())
    after = path.stat()
    if any(len({getattr(value, field) for value in (before, opened, after)}) != 1
           for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")):
        raise ValueError(f"output changed during audit: {path}")
    return digest


def audit_outputs(manifest_path: Path) -> dict:
    """Verify a declared SHA-256 output table. Research input/code hashes have separate meanings."""
    manifest = strict_json(manifest_path.read_bytes())
    if not isinstance(manifest, dict):
        raise ValueError("expected a JSON object")
    keys = [key for key in ("artifacts", "outputs", "files", "sha256", "artifact_sha256") if key in manifest]
    if len(keys) != 1 or not isinstance(manifest[keys[0]], dict) or not manifest[keys[0]]:
        raise ValueError("expected one nonempty recorded output checksum table")
    mismatches = []
    table = manifest[keys[0]]
    for name, expected in sorted(table.items()):
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"invalid output checksum for {name!r}")
        path = _output_path(manifest_path.parent, name)
        try:
            actual = _digest(path)
        except FileNotFoundError:
            mismatches.append({"file": name, "status": "missing"})
            continue
        if actual != expected:
            mismatches.append({"file": name, "status": "changed", "expected": expected, "actual": actual})
    return {"manifest": str(manifest_path), "listed": len(table), "matched": len(table) - len(mismatches),
            "differences": mismatches}


def audit_command(paths: list[Path]) -> int:
    import json
    reports = []
    for path in paths:
        try:
            reports.append(audit_outputs(path))
        except (OSError, ValueError) as exc:
            reports.append({"manifest": str(path), "error": str(exc)})
    print(json.dumps(reports, indent=2, allow_nan=False))
    return int(any("error" in report or report["differences"] for report in reports))
