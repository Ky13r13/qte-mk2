"""Private captured inputs for comparison jobs, never a browser DTO.

Validation IDs establish consistency and source correspondence, not historical
authenticity or an untouched holdout. No strategy executes at this boundary.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
import sqlite3

from qte.comparison_config import compile_comparison_configuration, prepare_comparison
from qte.provenance import source_identity

from .artifact_io import ArtifactIOError, FILE_LIMIT, read_stable
from .artifacts import ArtifactError, strict_json
from .job_inputs import CONFIG_LIMIT, DATA_LIMIT, JobInputError, _allowed, _read_regular, _safe_parts


TOTAL_DATA_LIMIT = 64 * 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_INTEGER_PARAMETERS = {"fast_period", "slow_period", "quantity", "breakout_period", "exit_period",
                       "atr_period", "contraction_window", "contraction_memory", "max_holding_bars"}
_INTEGER_EXPERIMENT = {"random_seed", "min_closed_trades", "min_validation_windows", "max_order_quantity"}


def _error() -> JobInputError:
    # Do not identify which source/role failed, or echo adapter/parser diagnostics.
    return JobInputError("comparison_input_unavailable", 409, "Comparison inputs are unavailable or inconsistent.")


def _canonical(value: object) -> bytes:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False).encode("ascii")
        if len(data) > CONFIG_LIMIT:
            raise ValueError
        return data
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise _error() from None


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise _error()
    return value


def _hex(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise _error()
    return value


@dataclass(frozen=True)
class ComparisonSource:
    validation_id: str
    validation_identity: str
    validation_path: str
    source_path: str
    adapter: str
    data_configuration_bytes: bytes
    data_bytes: bytes
    data_sha256: str
    identities: tuple[str, ...]


@dataclass(frozen=True)
class PreparedComparisonInputs:
    kind: str
    submitted_form_bytes: bytes
    configuration_bytes: bytes
    effective_config_bytes: bytes
    sources: tuple[ComparisonSource, ...]
    source_identity: str
    fingerprint: str


def _read_json(artifact, name):
    raw = read_stable(artifact.root, name, FILE_LIMIT, artifact.snapshots[name])
    if sha256(raw).hexdigest() != artifact.metadata["file_hashes"][name]:
        raise _error()
    return strict_json(raw)


def _resolve_validation(catalog, identifier: str) -> ComparisonSource:
    """Capture a verified validation's matching allowed source, without disclosure."""
    if type(identifier) is not str or _ID.fullmatch(identifier) is None:
        raise _error()
    original = catalog.inspect(identifier)
    if original.kind != "dataset_validation_v1" or original.metadata.get("integrity") != "verified":
        raise _error()
    relative = catalog._row(identifier)["path"]
    manifest = _read_json(original, "dataset_validation.json")
    configuration = _read_json(original, "configuration.json")
    data = copy.deepcopy(configuration["data"])
    job = re.fullmatch(r"build/gui/jobs/([0-9a-f]{32})/artifacts", relative)
    if job:
        from .inline_inputs import InlineSnapshotInputs
        from .job_inputs import verify_snapshot
        db = catalog._connect()
        if db is None:
            raise _error()
        try:
            row = db.execute("SELECT kind,state,snapshot FROM gui_jobs WHERE id=?", (job[1],)).fetchone()
        finally:
            db.close()
        if row is None or row["kind"] != "dataset_validate" or row["state"] != "succeeded":
            raise _error()
        recorded = strict_json(row["snapshot"].encode())
        snapshot = verify_snapshot(catalog.repository, job[1], recorded["snapshot_hashes"])
        if (not isinstance(snapshot, InlineSnapshotInputs) or snapshot.kind != "dataset_validate"
                or snapshot.data_sha256 != manifest["data_sha256"]):
            raise _error()
        raw = _read_regular(catalog.repository, snapshot.effective_config_path, CONFIG_LIMIT)
        if sha256(raw).hexdigest() != snapshot.hashes["effective_config_sha256"]:
            raise _error()
        expected = strict_json(raw)
        expected["data"]["path"] = data["path"]
        if _canonical(expected) != _canonical(configuration):
            raise _error()
        data["path"] = snapshot.original_data_path
    else:
        data["path"] = str(PurePosixPath(relative) / data["path"])
    _allowed(_safe_parts(data["path"]))
    if len(data["path"]) > 512:
        raise _error()
    raw = _read_regular(catalog.repository, data["path"], DATA_LIMIT)
    digest = sha256(raw).hexdigest()
    if digest != manifest["data_sha256"]:
        raise _error()
    data["expected_sha256"] = digest
    latest = catalog.inspect(identifier)
    if latest.snapshots != original.snapshots or latest.metadata != original.metadata:
        raise _error()
    # Caller options may be identical across unrelated windows. They are not
    # evidence that a window's raw data or derived summary has been observed.
    identities = tuple(sorted(f"file-sha256:{value}" for name, value in
                              latest.metadata["file_hashes"].items() if name != "configuration.json"))
    return ComparisonSource(identifier, latest.metadata["identity"], relative, data["path"], data["adapter"],
                            _canonical(data), raw, digest, identities)


def _wire(configuration):
    """Freeze and type only caller-controlled form fields; never accept data paths."""
    submitted = _canonical(configuration)
    value = strict_json(submitted)
    _object(value, {"schema_version", "workflow", "windows", "candidates", "experiment"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _error()
    if type(value["windows"]) is not list or not 2 <= len(value["windows"]) <= 24:
        raise _error()
    for window in value["windows"]:
        _object(window, {"name", "role", "regime", "validation_id", "evidence"})
        _object(window["evidence"], {"kind", "source", "corporate_action_policy"})
        if type(window["validation_id"]) is not str or _ID.fullmatch(window["validation_id"]) is None:
            raise _error()
    if type(value["candidates"]) is not list or not 1 <= len(value["candidates"]) <= 32:
        raise _error()
    def integers(mapping, keys):
        if type(mapping) is not dict:
            raise _error()
        for key in keys & mapping.keys():
            text = mapping[key]
            if type(text) is not str or len(text) > 20 or re.fullmatch(r"0|[1-9][0-9]*", text) is None:
                raise _error()
            mapping[key] = int(text)
    for candidate in value["candidates"]:
        if type(candidate) is not dict:
            raise _error()
        integers(candidate.get("parameters"), _INTEGER_PARAMETERS)
    integers(value["experiment"], _INTEGER_EXPERIMENT)
    return submitted, value


def _validated_sources(sources):
    if type(sources) is not tuple or not 2 <= len(sources) <= 24:
        raise _error()
    total = 0
    for item in sources:
        if not isinstance(item, ComparisonSource):
            raise _error()
        if type(item.validation_id) is not str or _ID.fullmatch(item.validation_id) is None:
            raise _error()
        if type(item.validation_identity) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", item.validation_identity) is None:
            raise _error()
        for path in (item.validation_path, item.source_path):
            if type(path) is not str or len(path) > 512 or "/".join(_safe_parts(path)) != path:
                raise _error()
        _allowed(_safe_parts(item.source_path))
        if (item.adapter not in {"csv", "alpaca"} or type(item.data_bytes) is not bytes
                or len(item.data_bytes) > DATA_LIMIT or type(item.data_configuration_bytes) is not bytes
                or len(item.data_configuration_bytes) > CONFIG_LIMIT):
            raise _error()
        if sha256(item.data_bytes).hexdigest() != _hex(item.data_sha256):
            raise _error()
        data = strict_json(item.data_configuration_bytes)
        if (_canonical(data) != item.data_configuration_bytes or data.get("path") != item.source_path
                or data.get("adapter") != item.adapter or data.get("expected_sha256") != item.data_sha256):
            raise _error()
        suffix = ".csv" if item.adapter == "csv" else ".json"
        if PurePosixPath(item.source_path).suffix.casefold() != suffix:
            raise _error()
        if (type(item.identities) is not tuple or not item.identities or
                any(type(identity) is not str or re.fullmatch(r"file-sha256:[0-9a-f]{64}", identity) is None
                    for identity in item.identities) or tuple(sorted(set(item.identities))) != item.identities
                or f"file-sha256:{item.data_sha256}" not in item.identities):
            raise _error()
        total += len(item.data_bytes)
    if total > TOTAL_DATA_LIMIT:
        raise _error()
    for field in ("validation_id", "validation_identity", "source_path", "data_sha256"):
        if len({getattr(item, field) for item in sources}) != len(sources):
            raise _error()


def input_name(index: int, source: ComparisonSource) -> str:
    return f"window-{index:03d}.{'csv' if source.adapter == 'csv' else 'json'}"


def _build_prepared(submitted: bytes, sources: tuple[ComparisonSource, ...], identity: str) -> PreparedComparisonInputs:
    """Reconstruct all identities from captured bytes; no source rereads or engine."""
    if type(submitted) is not bytes or len(submitted) > CONFIG_LIMIT:
        raise _error()
    _validated_sources(sources)
    wire, config = _wire(strict_json(submitted))
    if wire != submitted or len(sources) != len(config["windows"]):
        raise _error()
    if type(identity) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is None:
        raise _error()
    for window, source in zip(config["windows"], sources, strict=True):
        if window.pop("validation_id") != source.validation_id:
            raise _error()
        window["data"] = strict_json(source.data_configuration_bytes)
        window["evidence"].update(source_sha256=source.data_sha256, availability_policy="bar_end_ns")
    normalized = compile_comparison_configuration(config)
    effective = copy.deepcopy(normalized)
    for index, (window, source) in enumerate(zip(effective["windows"], sources, strict=True)):
        window["data"]["path"] = input_name(index, source)
    canonical, effective_bytes = _canonical(normalized), _canonical(effective)
    fingerprint = sha256(_canonical({"schema_version": 3, "kind": "comparison_run",
        "submitted_sha256": sha256(submitted).hexdigest(), "configuration_sha256": sha256(canonical).hexdigest(),
        "source_identity": identity, "sources": [_source_record(index, item) for index, item in enumerate(sources)]})).hexdigest()
    return PreparedComparisonInputs("comparison_run", submitted, canonical, effective_bytes, sources, identity, fingerprint)


def _source_record(index: int, source: ComparisonSource) -> dict:
    return {"validation_id": source.validation_id, "validation_identity": source.validation_identity,
            "validation_path": source.validation_path, "source_path": source.source_path, "adapter": source.adapter,
            "data_configuration": strict_json(source.data_configuration_bytes), "data_sha256": source.data_sha256,
            "identities": list(source.identities), "input_name": input_name(index, source)}


def validate_prepared(value: PreparedComparisonInputs) -> PreparedComparisonInputs:
    try:
        if not isinstance(value, PreparedComparisonInputs) or value.kind != "comparison_run":
            raise _error()
        checked = _build_prepared(value.submitted_form_bytes, value.sources, value.source_identity)
        if checked != value:
            raise _error()
        return checked
    except (ArtifactError, JobInputError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise _error() from None


def prepare_comparison_inputs(catalog, configuration) -> PreparedComparisonInputs:
    try:
        submitted, wire = _wire(configuration)
        sources = []
        total = 0
        for window in wire["windows"]:
            source = _resolve_validation(catalog, window["validation_id"])
            total += len(source.data_bytes)
            if total > TOTAL_DATA_LIMIT:
                raise _error()
            sources.append(source)
        prepared = _build_prepared(submitted, tuple(sources), source_identity())
        authorize_comparison_inputs(catalog, prepared)
        # Chronology/universe/currency/options are checked only after authorization.
        plan = prepare_comparison(strict_json(prepared.configuration_bytes), tuple(item.data_bytes for item in sources))
        if plan.source_identity != prepared.source_identity:
            raise _error()
        authorize_comparison_inputs(catalog, prepared)
        return prepared
    except (ArtifactError, ArtifactIOError, JobInputError, OSError, sqlite3.Error,
            ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise _error() from None


def authorize_comparison_inputs(catalog, prepared: PreparedComparisonInputs, *, reserve_test: bool = False) -> dict:
    """Private role-aware authorization; reservation protects bytes, never reveals.

    Call again at job acceptance/dispatch/finalization. This is deliberately not
    the ordinary job policy, which permits revealed protected input. An absent
    disclosure record is unknown history, not proof of an untouched holdout.
    """
    try:
        value = validate_prepared(prepared)
        if type(reserve_test) is not bool:
            raise _error()
        catalog._validate_job_disclosure_storage()
        db = catalog._connect()
        if db is not None:
            try:
                rows = db.execute("SELECT id,path FROM artifacts").fetchall()
            finally:
                db.close()
            for row in rows:
                if any(source.source_path.startswith(row["path"].rstrip("/") + "/") for source in value.sources):
                    catalog.inspect(row["id"])
        test_identities, safe_identities = set(), set()
        config = strict_json(value.configuration_bytes)
        for window, source in zip(config["windows"], value.sources, strict=True):
            latest = catalog.inspect(source.validation_id)
            identities = tuple(sorted(f"file-sha256:{digest}" for name, digest in
                                      latest.metadata.get("file_hashes", {}).items() if name != "configuration.json"))
            if (latest.kind != "dataset_validation_v1" or latest.metadata.get("integrity") != "verified"
                    or latest.metadata["identity"] != source.validation_identity or identities != source.identities
                    or catalog._row(source.validation_id)["path"] != source.validation_path):
                raise _error()
            catalog._validate_job_disclosure_storage()
            for identity in source.identities:
                protected = catalog._job_input_protected(identity)
                revealed = catalog.disclosures.revealed([identity])
                record = catalog.disclosures.root / f"revealed_outside_protocol-{catalog.disclosures.key(identity)}.json"
                if not revealed and (record.exists() or record.is_symlink()):
                    raise _error()
                if (window["role"] != "test" and (protected or revealed)) or (window["role"] == "test" and revealed):
                    raise _error()
            (test_identities if window["role"] == "test" else safe_identities).update(source.identities)
        if test_identities & safe_identities:
            raise _error()
        if reserve_test:
            catalog.disclosures.mark_protected(sorted(test_identities))
            if not all(catalog._job_input_protected(identity) for identity in test_identities):
                raise _error()
            # Detect disclosure/protection changes during the append-only write.
            return authorize_comparison_inputs(catalog, value)
        return {"kind": "comparison_run", "safe_identities": sorted(safe_identities),
                "test_identities": sorted(test_identities), "holdout_history": "unknown"}
    except (ArtifactError, ArtifactIOError, JobInputError, OSError, sqlite3.Error,
            ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise _error() from None
