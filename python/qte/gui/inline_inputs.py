"""Immutable version-2 snapshots for already-authorized inline research forms."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .artifacts import strict_json
from .form_inputs import CapturedFormInputs
from .job_inputs import (
    CONFIG_LIMIT,
    DATA_LIMIT,
    JobInputError,
    _JOB_ID,
    _allowed,
    _error,
    _job_inputs_fd,
    _read_regular,
    _relative,
    _safe_parts,
    _write_exclusive,
)
from .research_forms import INLINE_CONFIG_ORIGIN, compile_configuration


_HEX = re.compile(r"[0-9a-f]{64}")
_INLINE_NAMES = {"submitted-form.json", "effective-config.json", "input-manifest.json"}


@dataclass(frozen=True)
class PreparedInlineInputs:
    """Validated inline identities ready for a no-reread snapshot write."""

    kind: str
    config_origin: dict[str, object]
    submitted_form_bytes: bytes
    configuration_bytes: bytes
    config_sha256: str
    original_config_path: None
    original_data_path: str | None
    adapter: str | None
    data_bytes: bytes | None
    data_sha256: str | None
    canonical_config_sha256: str
    effective_config_bytes: bytes
    preview_fingerprint: str

    @property
    def config_bytes(self) -> bytes:
        return self.configuration_bytes

    @property
    def fingerprint(self) -> str:
        return self.preview_fingerprint


@dataclass(frozen=True)
class InlineSnapshotInputs:
    """Verified version-2 inline snapshot inventory."""

    job_id: str
    kind: str
    input_root: str
    effective_config: str
    original_config_path: None
    original_data_path: str | None
    adapter: str | None
    effective_config_path: str
    data_path: str | None
    data_sha256: str | None
    hashes: dict[str, str | None]
    snapshot_hashes: dict[str, str]
    preview_fingerprint: str

    @property
    def fingerprint(self) -> str:
        return self.preview_fingerprint


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise _error("invalid") from None


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _origin(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(INLINE_CONFIG_ORIGIN):
        raise _error("invalid")
    for name, expected in INLINE_CONFIG_ORIGIN.items():
        actual = value[name]
        if type(actual) is not type(expected) or actual != expected:
            raise _error("invalid")
    return dict(INLINE_CONFIG_ORIGIN)


def _hex(value: object) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise _error("invalid")
    return value


def _fingerprint(kind: str, canonical_config_sha256: str, data_sha256: str | None) -> str:
    return _digest(_canonical({
        "schema_version": 1,
        "kind": kind,
        "canonical_config_sha256": canonical_config_sha256,
        "data_sha256": data_sha256,
    }))


def _configuration(value: bytes) -> dict[str, Any]:
    if type(value) is not bytes:
        raise _error("invalid")
    try:
        parsed = strict_json(value)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise _error("invalid") from None
    if not isinstance(parsed, dict) or _canonical(parsed) != value:
        raise _error("invalid")
    return parsed


def _research_identity(adapter: object, original_data_path: object, data_bytes: object,
                       data_sha256: object) -> tuple[str, str, bytes, str]:
    if (not isinstance(adapter, str) or adapter not in {"csv", "alpaca"}
            or not isinstance(original_data_path, str) or type(data_bytes) is not bytes
            or len(data_bytes) > DATA_LIMIT):
        raise _error("invalid")
    if not 1 <= len(original_data_path) <= 512:
        raise _error("invalid")
    parts = _safe_parts(original_data_path)
    _allowed(parts)
    relative = _relative(parts)
    suffix = PurePosixPath(relative).suffix.casefold()
    if relative != original_data_path or (adapter == "csv" and suffix != ".csv") or (
            adapter == "alpaca" and suffix != ".json"):
        raise _error("invalid")
    digest = _hex(data_sha256)
    if _digest(data_bytes) != digest:
        raise _error("invalid")
    return adapter, relative, data_bytes, digest


def _normalized_data_path(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise _error("invalid")
    parts = _safe_parts(value)
    _allowed(parts)
    return _relative(parts)


def _build_prepared(captured: CapturedFormInputs, preview: object) -> PreparedInlineInputs:
    if not isinstance(captured, CapturedFormInputs) or not isinstance(preview, dict):
        raise _error("invalid")
    if (not isinstance(captured.kind, str) or captured.kind not in {"research_run", "dataset_validate", "strategy_lab", "download_alpaca"}
            or captured.original_config_path is not None):
        raise _error("invalid")
    if type(captured.configuration_bytes) is not bytes or len(captured.configuration_bytes) > CONFIG_LIMIT:
        raise _error("invalid")
    origin = _origin(captured.config_origin)
    wire = _configuration(captured.configuration_bytes)
    if _digest(captured.configuration_bytes) != _hex(captured.config_sha256):
        raise _error("invalid")
    if preview.get("kind") != captured.kind or _origin(preview.get("config_origin")) != origin:
        raise _error("invalid")
    try:
        typed = compile_configuration(captured.kind, wire)
    except Exception:
        raise _error("invalid") from None
    typed_bytes = _canonical(typed)
    if len(typed_bytes) > CONFIG_LIMIT:
        raise _error("invalid")
    canonical_hash = _digest(typed_bytes)
    if (not isinstance(preview.get("effective_configuration"), dict)
            or _canonical(preview["effective_configuration"]) != typed_bytes
            or _hex(preview.get("canonical_config_sha256")) != canonical_hash):
        raise _error("invalid")

    if captured.kind in {"research_run", "dataset_validate"}:
        adapter, path, data, data_hash = _research_identity(
            captured.adapter, captured.original_data_path, captured.data_bytes, captured.data_sha256,
        )
        if typed.get("data", {}).get("adapter") != adapter:
            raise _error("invalid")
        if _normalized_data_path(typed.get("data", {}).get("path")) != path:
            raise _error("invalid")
        effective = copy.deepcopy(typed)
        effective["data"]["path"] = "input.csv" if adapter == "csv" else "input.json"
    else:
        if any(value is not None for value in (
                captured.adapter, captured.original_data_path, captured.data_bytes, captured.data_sha256)):
            raise _error("invalid")
        adapter = path = data = data_hash = None
        effective = typed
    fingerprint = _fingerprint(captured.kind, canonical_hash, data_hash)
    if _hex(preview.get("preview_fingerprint")) != fingerprint:
        raise _error("invalid")
    envelope = {
        "schema_version": 1,
        "kind": captured.kind,
        "config_origin": origin,
        "configuration": wire,
    }
    submitted_form_bytes = _canonical(envelope)
    effective_config_bytes = _canonical(effective)
    if len(submitted_form_bytes) > CONFIG_LIMIT + 1024 or len(effective_config_bytes) > CONFIG_LIMIT:
        raise _error("invalid")
    return PreparedInlineInputs(
        captured.kind, origin, submitted_form_bytes, captured.configuration_bytes, captured.config_sha256,
        None, path, adapter, data, data_hash, canonical_hash, effective_config_bytes, fingerprint,
    )


def prepare_inline_inputs(captured: CapturedFormInputs, preview: object) -> PreparedInlineInputs:
    """Recheck the reviewed preview against immutable captured bytes only."""
    return _build_prepared(captured, preview)


def _validated_prepared(prepared: PreparedInlineInputs) -> PreparedInlineInputs:
    """Validate every identity before a writer creates its job directory."""
    if not isinstance(prepared, PreparedInlineInputs):
        raise _error("invalid")
    if (type(prepared.configuration_bytes) is not bytes or type(prepared.submitted_form_bytes) is not bytes
            or type(prepared.effective_config_bytes) is not bytes):
        raise _error("invalid")
    if len(prepared.configuration_bytes) > CONFIG_LIMIT or len(prepared.submitted_form_bytes) > CONFIG_LIMIT + 1024:
        raise _error("invalid")
    wire = _configuration(prepared.configuration_bytes)
    origin = _origin(prepared.config_origin)
    if (not isinstance(prepared.kind, str) or prepared.kind not in {"research_run", "dataset_validate", "strategy_lab", "download_alpaca"}
            or prepared.original_config_path is not None):
        raise _error("invalid")
    if _digest(prepared.configuration_bytes) != _hex(prepared.config_sha256):
        raise _error("invalid")
    try:
        typed = compile_configuration(prepared.kind, wire)
    except Exception:
        raise _error("invalid") from None
    typed_bytes = _canonical(typed)
    canonical_hash = _digest(typed_bytes)
    if canonical_hash != _hex(prepared.canonical_config_sha256):
        raise _error("invalid")
    if prepared.kind in {"research_run", "dataset_validate"}:
        adapter, path, data, data_hash = _research_identity(
            prepared.adapter, prepared.original_data_path, prepared.data_bytes, prepared.data_sha256,
        )
        if typed.get("data", {}).get("adapter") != adapter:
            raise _error("invalid")
        if _normalized_data_path(typed.get("data", {}).get("path")) != path:
            raise _error("invalid")
        effective = copy.deepcopy(typed)
        effective["data"]["path"] = "input.csv" if adapter == "csv" else "input.json"
    else:
        if any(value is not None for value in (
                prepared.adapter, prepared.original_data_path, prepared.data_bytes, prepared.data_sha256)):
            raise _error("invalid")
        adapter = path = data = data_hash = None
        effective = typed
    fingerprint = _fingerprint(prepared.kind, canonical_hash, data_hash)
    if fingerprint != _hex(prepared.preview_fingerprint):
        raise _error("invalid")
    envelope = _canonical({
        "schema_version": 1, "kind": prepared.kind, "config_origin": origin, "configuration": wire,
    })
    effective_bytes = _canonical(effective)
    if (envelope != prepared.submitted_form_bytes or effective_bytes != prepared.effective_config_bytes
            or len(effective_bytes) > CONFIG_LIMIT):
        raise _error("invalid")
    return PreparedInlineInputs(
        prepared.kind, origin, envelope, prepared.configuration_bytes, prepared.config_sha256, None,
        path, adapter, data, data_hash, canonical_hash, effective_bytes, fingerprint,
    )


def _manifest(prepared: PreparedInlineInputs, job_id: str, hashes: dict[str, str]) -> bytes:
    return _canonical({
        "schema_version": 2,
        "job_id": job_id,
        "kind": prepared.kind,
        "config_origin": prepared.config_origin,
        "adapter": prepared.adapter,
        "original_data_path": prepared.original_data_path,
        "submitted_configuration_sha256": prepared.config_sha256,
        "canonical_config_sha256": prepared.canonical_config_sha256,
        "preview_fingerprint": prepared.preview_fingerprint,
        "hashes": hashes,
    }) + b"\n"


def _job_id(value: object) -> str:
    if not isinstance(value, str) or _JOB_ID.fullmatch(value) is None:
        raise _error("invalid")
    return value


def _inventory(prepared: PreparedInlineInputs) -> tuple[set[str], str | None]:
    if prepared.kind in {"strategy_lab", "download_alpaca"}:
        return set(_INLINE_NAMES), None
    name = "input.csv" if prepared.adapter == "csv" else "input.json"
    return set(_INLINE_NAMES) | {name}, name


def _snapshot_result(repository: Path, job_id: str, prepared: PreparedInlineInputs,
                     snapshot_hashes: dict[str, str]) -> InlineSnapshotInputs:
    root = f"build/gui/jobs/{job_id}/inputs"
    hashes: dict[str, str | None] = {
        "submitted_configuration_sha256": prepared.config_sha256,
        "canonical_config_sha256": prepared.canonical_config_sha256,
        "effective_config_sha256": _digest(prepared.effective_config_bytes),
        "input_manifest_sha256": snapshot_hashes["input-manifest.json"],
        "data_sha256": prepared.data_sha256,
    }
    data_name = None
    if prepared.kind in {"research_run", "dataset_validate"}:
        data_name = "input.csv" if prepared.adapter == "csv" else "input.json"
    return InlineSnapshotInputs(
        job_id, prepared.kind, root, f"{root}/effective-config.json", None, prepared.original_data_path,
        prepared.adapter, f"{root}/effective-config.json", None if data_name is None else f"{root}/{data_name}",
        prepared.data_sha256, hashes, snapshot_hashes, prepared.preview_fingerprint,
    )


def write_inline_snapshot(repository: Path, job_id: str, prepared: PreparedInlineInputs) -> InlineSnapshotInputs:
    """Persist a verified v2 inventory with no path reread or fake source name."""
    checked = _validated_prepared(prepared)
    _job_id(job_id)
    names, data_name = _inventory(checked)
    contents = {
        "submitted-form.json": checked.submitted_form_bytes,
        "effective-config.json": checked.effective_config_bytes,
    }
    if data_name is not None:
        contents[data_name] = checked.data_bytes  # type: ignore[assignment]
    hashes = {name: _digest(value) for name, value in contents.items()}
    manifest = _manifest(checked, job_id, hashes)

    descriptors, inputs_fd = _job_inputs_fd(Path(repository), job_id, create=True)
    try:
        for name in sorted(contents):
            _write_exclusive(inputs_fd, name, contents[name])
        _write_exclusive(inputs_fd, "input-manifest.json", manifest)
        snapshot_hashes = {**hashes, "input-manifest.json": _digest(manifest)}
        if set(snapshot_hashes) != names:
            raise _error("invalid")
        os.fsync(inputs_fd)
        os.fchmod(inputs_fd, 0o500)
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return _snapshot_result(Path(repository), job_id, checked, snapshot_hashes)


def _expected_hashes(value: object) -> dict[str, str]:
    if (not isinstance(value, dict) or not value or any(
            not isinstance(name, str) or not isinstance(digest, str) or _HEX.fullmatch(digest) is None
            for name, digest in value.items())):
        raise _error("snapshot")
    return dict(value)


def _snapshot_error() -> None:
    raise _error("snapshot")


def verify_inline_snapshot(repository: Path, job_id: str,
                           expected_snapshot_hashes: dict[str, str]) -> InlineSnapshotInputs:
    """Verify v2 external hashes and recompute all form-derived identities."""
    expected = _expected_hashes(expected_snapshot_hashes)
    if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
        _snapshot_error()
    descriptors, inputs_fd = _job_inputs_fd(Path(repository), job_id, create=False)
    try:
        try:
            inventory = set(os.listdir(inputs_fd))
        except OSError:
            _snapshot_error()
        if inventory != set(expected) or not _INLINE_NAMES <= inventory:
            _snapshot_error()
        data_names = inventory & {"input.csv", "input.json"}
        if len(data_names) > 1 or inventory - _INLINE_NAMES - data_names:
            _snapshot_error()
        values = {name: _read_regular(Path(repository), f"build/gui/jobs/{job_id}/inputs/{name}", DATA_LIMIT)
                  for name in inventory}
        if (len(values["submitted-form.json"]) > CONFIG_LIMIT + 1024
                or len(values["effective-config.json"]) > CONFIG_LIMIT
                or len(values["input-manifest.json"]) > CONFIG_LIMIT):
            _snapshot_error()
        actual = {name: _digest(value) for name, value in values.items()}
        if actual != expected:
            _snapshot_error()
        try:
            manifest = strict_json(values["input-manifest.json"])
        except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
            _snapshot_error()
        expected_keys = {
            "schema_version", "job_id", "kind", "config_origin", "adapter", "original_data_path",
            "submitted_configuration_sha256", "canonical_config_sha256", "preview_fingerprint", "hashes",
        }
        if not isinstance(manifest, dict) or set(manifest) != expected_keys:
            _snapshot_error()
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 2 or manifest.get("job_id") != job_id:
            _snapshot_error()
        kind = manifest.get("kind")
        if not isinstance(kind, str) or kind not in {"research_run", "dataset_validate", "strategy_lab", "download_alpaca"}:
            _snapshot_error()
        try:
            origin = _origin(manifest.get("config_origin"))
        except JobInputError:
            _snapshot_error()
        hashes = manifest.get("hashes")
        content_hashes = {name: actual[name] for name in inventory - {"input-manifest.json"}}
        try:
            manifest_hashes = _expected_hashes(hashes)
        except JobInputError:
            _snapshot_error()
        if manifest_hashes != content_hashes:
            _snapshot_error()
        for name in ("submitted_configuration_sha256", "canonical_config_sha256", "preview_fingerprint"):
            try:
                _hex(manifest.get(name))
            except JobInputError:
                _snapshot_error()
        try:
            envelope = strict_json(values["submitted-form.json"])
        except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
            _snapshot_error()
        if (not isinstance(envelope, dict) or set(envelope) != {"schema_version", "kind", "config_origin", "configuration"}
                or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1
                or envelope.get("kind") != kind or _origin(envelope.get("config_origin")) != origin
                or not isinstance(envelope.get("configuration"), dict)
                or _canonical(envelope) != values["submitted-form.json"]):
            _snapshot_error()
        configuration_bytes = _canonical(envelope["configuration"])
        if (len(configuration_bytes) > CONFIG_LIMIT
                or _digest(configuration_bytes) != manifest["submitted_configuration_sha256"]):
            _snapshot_error()
        try:
            typed = compile_configuration(kind, envelope["configuration"])
        except Exception:
            _snapshot_error()
        typed_bytes = _canonical(typed)
        canonical_hash = _digest(typed_bytes)
        if canonical_hash != manifest["canonical_config_sha256"]:
            _snapshot_error()
        if kind in {"research_run", "dataset_validate"}:
            adapter = manifest.get("adapter")
            original_data_path = manifest.get("original_data_path")
            if len(data_names) != 1:
                _snapshot_error()
            try:
                adapter, original_data_path, data_bytes, data_hash = _research_identity(
                    adapter, original_data_path, values[next(iter(data_names))], actual[next(iter(data_names))],
                )
            except JobInputError:
                _snapshot_error()
            data_name = next(iter(data_names))
            if (typed.get("data", {}).get("adapter") != adapter
                    or _normalized_data_path(typed.get("data", {}).get("path")) != original_data_path
                    or data_name != ("input.csv" if adapter == "csv" else "input.json")):
                _snapshot_error()
            effective = copy.deepcopy(typed)
            effective["data"]["path"] = data_name
        else:
            if data_names or manifest.get("adapter") is not None or manifest.get("original_data_path") is not None:
                _snapshot_error()
            adapter = original_data_path = data_bytes = data_hash = data_name = None
            effective = typed
        effective_bytes = _canonical(effective)
        if values["effective-config.json"] != effective_bytes:
            _snapshot_error()
        fingerprint = _fingerprint(kind, canonical_hash, data_hash)
        if fingerprint != manifest["preview_fingerprint"]:
            _snapshot_error()
        prepared = PreparedInlineInputs(
            kind, origin, values["submitted-form.json"], configuration_bytes,
            manifest["submitted_configuration_sha256"], None, original_data_path, adapter, data_bytes, data_hash,
            canonical_hash, effective_bytes, fingerprint,
        )
        return _snapshot_result(Path(repository), job_id, _validated_prepared(prepared), actual)
    except JobInputError:
        raise _error("snapshot") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
