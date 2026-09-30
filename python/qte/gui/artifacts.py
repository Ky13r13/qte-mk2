"""Strict readers for QTE's legacy and additive owned-result artifact layouts."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
import io
import json
import math
import mimetypes
from pathlib import Path
import re

from .artifact_io import ArtifactIOError, FILE_LIMIT, METADATA_LIMIT, Snapshot, inventory, read_stable


class ArtifactError(Exception):
    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message); self.code = code; self.status = status; self.message = message


def strict_json(data: bytes):
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out: raise ValueError("duplicate key")
            out[key] = value
        return out
    def finite(value: str) -> float:
        result = float(value)
        if not math.isfinite(result): raise ValueError("nonfinite")
        return result
    return json.loads(data, object_pairs_hook=pairs, parse_float=finite,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))


@dataclass
class Artifact:
    root: Path
    kind: str
    snapshots: dict[str, Snapshot]
    metadata: dict
    protected: set[str]
    mixed: set[str]
    known: set[str]

    def protected_identities(self) -> list[str]:
        names = self.protected | self.mixed
        if self.kind == "unsupported" or self.metadata.get("integrity") != "verified":
            names = set(self.snapshots)
        else:
            names |= set(self.snapshots) - self.known
        hashes = self.metadata.get("file_hashes", {})
        return sorted(f"file-sha256:{hashes[name]}" for name in names if name in hashes)

    def context_identities(self) -> list[str]:
        return sorted(f"protocol:{value}" for value in self.metadata.get("protocol_ids", []))

    def files(self, revealed: bool) -> list[dict]:
        values = []
        for name, snap in self.snapshots.items():
            protection = "protected" if name in self.protected else "mixed" if name in self.mixed else (
                "unclassified" if name not in self.known or self.metadata.get("integrity") != "verified" else "unprotected")
            file_id = sha256(name.encode()).hexdigest()
            values.append({"id": file_id, "name": name, "size": snap.size,
                           "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                           "protection": protection,
                           "available": protection == "unprotected" or revealed})
        return values

    def path_for_id(self, file_id: str) -> tuple[str, Snapshot]:
        for name, snap in self.snapshots.items():
            if sha256(name.encode()).hexdigest() == file_id:
                return name, snap
        raise ArtifactError("artifact_file_not_found", 404, "Artifact file unavailable.")


def _load_json(root: Path, snaps: dict[str, Snapshot], name: str):
    try:
        return strict_json(read_stable(root, name, METADATA_LIMIT, snaps[name]))
    except (KeyError, ValueError, UnicodeDecodeError, ArtifactIOError, json.JSONDecodeError) as exc:
        raise ArtifactError("artifact_invalid", 422, "Artifact metadata is invalid.") from exc


def _validate_csv(data: bytes, name: str) -> None:
    try: text = data.decode("utf-8")
    except UnicodeDecodeError as exc: raise ArtifactError("artifact_invalid", 422, "Artifact CSV is invalid.") from exc
    rows = csv.reader(io.StringIO(text), strict=True)
    try: header = next(rows)
    except (StopIteration, csv.Error) as exc: raise ArtifactError("artifact_invalid", 422, "Artifact CSV is invalid.") from exc
    if not header or len(header) != len(set(header)) or any(not x or "\x00" in x for x in header):
        raise ArtifactError("artifact_invalid", 422, "Artifact CSV header is invalid.")
    base = name.rsplit("/", 1)[-1]
    expected = {
        "equity.csv": ["timestamp_ns", "equity", "gross_exposure"],
        "sampled_equity.csv": ["timestamp_ns", "equity", "gross_exposure"],
        "fills.csv": (["fill_id", "order_id", "symbol", "side", "quantity", "timestamp_ns", "price", "commission"]
                      if "/runs/" not in f"/{name}" else
                      ["id", "order_id", "symbol", "side", "quantity", "timestamp_ns", "price", "commission"]),
        "orders.csv": (["order_id", "symbol", "status", "detail"] if "/runs/" not in f"/{name}" else
                       ["id", "symbol", "status", "detail"]),
        "comparison.csv": ["candidate", "window", "role", "regime", "cost_scenario", "status", "total_return",
                           "maximum_drawdown", "trade_count", "open_trade_count", "fill_count", "rejected_order_count", "error"],
        "regime-decisions.csv": ["timestamp_ns", "macro_regime", "volatility_regime", "permitted_families"],
    }.get(base)
    if "/data/" in f"/{name}":
        expected = ["symbol", "start_ns", "end_ns", "open", "high", "low", "close", "volume"]
    if expected is None or header != expected:
        raise ArtifactError("artifact_invalid", 422, "Artifact CSV header is unsupported.")
    numeric = {i for i, value in enumerate(header) if value.endswith("_ns") or value in {
        "fill_id", "order_id", "id", "quantity", "seed", "sequence", "price", "commission",
        "open", "high", "low", "close", "volume", "equity", "gross_exposure"}}
    try:
        for row in rows:
            if len(row) != len(header): raise ValueError
            for i in numeric:
                if not row[i] or not math.isfinite(float(row[i])): raise ValueError
                field = header[i]
                if field.endswith("_ns"):
                    value = int(row[i]);
                    if not -(2**63) <= value < 2**63: raise ValueError
                elif field in {"fill_id", "order_id", "id"}:
                    value = int(row[i]);
                    if not 0 <= value < 2**64: raise ValueError
                elif field == "quantity":
                    value = int(row[i]);
                    if value <= 0 or value >= 2**63: raise ValueError
    except (csv.Error, ValueError, OverflowError) as exc:
        raise ArtifactError("artifact_invalid", 422, "Artifact CSV values are invalid.") from exc


def _csv_data_row_count(data: bytes) -> int:
    try:
        rows = csv.reader(io.StringIO(data.decode("utf-8")), strict=True)
        next(rows)
        return sum(1 for _ in rows)
    except (UnicodeDecodeError, StopIteration, csv.Error) as exc:
        raise ArtifactError("artifact_invalid", 422, "Artifact CSV is invalid.") from exc


def inspect_artifact(root: Path, snapshots: dict[str, Snapshot] | None = None) -> Artifact:
    try: snaps = snapshots if snapshots is not None else inventory(root)
    except ArtifactIOError as exc: raise ArtifactError("artifact_forbidden", 403, "Artifact is unavailable.") from exc
    kind = _kind(snaps)
    single = kind in {"single_run_v1", "research_export_v2"}
    base = {"export": "unsupported_format", "integrity": "unchecked", "evidence": "unknown",
            "scenario_outcome": "unknown", "selection": "unknown", "holdout_evaluation": "unknown"}
    if kind == "unsupported":
        identity = _content_identity(root, snaps)
        hashes = _file_hashes(root, snaps)
        _unchanged(root, snaps)
        return Artifact(root, kind, snaps, {**base, "identity": identity, "protocol_ids": [], "file_hashes": hashes},
                        set(), set(), set())
    mandatory = {"manifest.json", "report.json", "equity.csv", "fills.csv", "orders.csv"} if single else {"configuration.json", "summary.json"}
    if "complete.json" not in snaps:
        hashes = _file_hashes(root, snaps)
        result = Artifact(root, kind, snaps, {**base, "export": "incomplete", "identity": _content_identity(root, snaps),
                                            "protocol_ids": [], "file_hashes": hashes}, set(), set(), set())
        _unchanged(root, snaps)
        return result
    complete = _load_json(root, snaps, "complete.json")
    checksums = complete.get("sha256") if (isinstance(complete, dict) and
        type(complete.get("schema_version")) is int and complete.get("schema_version") == 1) else None
    expected_names = set(snaps) - {"complete.json"}
    if isinstance(complete, dict) and "schema_version" in complete and (type(complete["schema_version"]) is not int or complete["schema_version"] != 1):
        raise ArtifactError("artifact_unsupported", 422, "Unsupported artifact version.")
    if not isinstance(checksums, dict) or set(checksums) != expected_names or not mandatory <= expected_names:
        raise ArtifactError("artifact_invalid", 422, "Artifact inventory is invalid.")
    for name, expected_hash in checksums.items():
        data = read_stable(root, name, FILE_LIMIT, snaps[name])
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or sha256(data).hexdigest() != expected_hash:
            raise ArtifactError("artifact_invalid", 422, "Artifact checksum verification failed.")
    protected, mixed, protocols, source_identities = set(), set(), [], []
    known = {"complete.json"}
    if single:
        manifest = _load_json(root, snaps, "manifest.json")
        report = _load_json(root, snaps, "report.json")
        if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or
                manifest.get("schema_version") != 1 or not isinstance(report, dict)):
            raise ArtifactError("artifact_unsupported", 422, "Unsupported artifact version.")
        required_manifest = {"configuration", "dataset_end_ns", "dataset_hash", "dataset_hash_algorithm",
                             "dataset_start_ns", "execution_model", "normalized_engine_config", "research_label",
                             "sampling", "schema_version", "source", "source_id", "source_identity", "strategy"}
        if not required_manifest <= set(manifest) or not isinstance(manifest["configuration"], dict) or \
                not isinstance(manifest["source"], dict) or not isinstance(manifest["strategy"], dict):
            raise ArtifactError("artifact_invalid", 422, "Artifact manifest shape is invalid.")
        for field in ("dataset_start_ns", "dataset_end_ns"):
            if type(manifest[field]) is not int or not -(2**63) <= manifest[field] < 2**63:
                raise ArtifactError("artifact_invalid", 422, "Artifact manifest timestamp is invalid.")
        if not isinstance(report.get("metrics"), dict) or not isinstance(report.get("returns"), list):
            raise ArtifactError("artifact_invalid", 422, "Artifact report shape is invalid.")
        for field in ("fill_count", "open_trade_count", "order_count", "trade_count"):
            if type(report.get(field)) is not int or report[field] < 0:
                raise ArtifactError("artifact_invalid", 422, "Artifact report count is invalid.")
        for metric in report["metrics"].values():
            if (not isinstance(metric, dict) or "value" not in metric or
                    not isinstance(metric.get("undefined_reason"), str) or
                    (metric.get("value") is not None and
                     (type(metric["value"]) not in (int, float) or not math.isfinite(metric["value"])))):
                raise ArtifactError("artifact_invalid", 422, "Artifact metric is invalid.")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in report["returns"]):
            raise ArtifactError("artifact_invalid", 422, "Artifact returns are invalid.")
        for name in mandatory - {"manifest.json", "report.json"}: _validate_csv(read_stable(root, name, FILE_LIMIT, snaps[name]), name)
        if "sampled_equity.csv" in snaps:
            _validate_csv(read_stable(root, "sampled_equity.csv", FILE_LIMIT, snaps["sampled_equity.csv"]),
                          "sampled_equity.csv")
            known.add("sampled_equity.csv")
        elif manifest.get("sampling") is not None:
            raise ArtifactError("artifact_invalid", 422, "Sampled equity export is missing.")
        evidence = "historical_declared" if manifest.get("evidence") == "historical_declared" else "unknown"
        status = {**base, "export": "complete", "integrity": "verified", "evidence": evidence,
                  "scenario_outcome": "succeeded", "selection": "no_selection", "holdout_evaluation": "not_evaluated"}
        known |= mandatory
        if kind == "research_export_v2":
            from .export_v2 import validate_v2
            known |= validate_v2(root, snaps, manifest, report)
        if isinstance(manifest.get("source_identity"), str): source_identities.append(manifest["source_identity"])
    else:
        config, summary = _load_json(root, snaps, "configuration.json"), _load_json(root, snaps, "summary.json")
        if (not isinstance(config, dict) or not isinstance(summary, dict) or
                type(config.get("schema_version")) is not int or config.get("schema_version") != 1 or
                not isinstance(summary.get("experiments"), list)):
            raise ArtifactError("artifact_unsupported", 422, "Unsupported artifact version.")
        timeframes = config.get("timeframes")
        if not isinstance(timeframes, list) or not timeframes: raise ArtifactError("artifact_invalid", 422, "Artifact configuration is invalid.")
        for timeframe in timeframes:
            required = {f"{timeframe}/{x}" for x in ("report.json","slate.json","generator.json","macro.json","comparison.csv","regime-decisions.csv")}
            if not required <= expected_names: raise ArtifactError("artifact_invalid", 422, "Artifact files are incomplete.")
            report = _load_json(root, snaps, f"{timeframe}/report.json")
            slate = _load_json(root, snaps, f"{timeframe}/slate.json")
            if not isinstance(report, dict) or not isinstance(slate, dict):
                raise ArtifactError("artifact_invalid", 422, "Artifact report or slate shape is invalid.")
            if not isinstance(slate.get("candidates"), list) or not slate["candidates"]:
                raise ArtifactError("artifact_invalid", 422, "Artifact slate is invalid.")
            summaries = [item for item in summary["experiments"] if isinstance(item, dict) and item.get("timeframe") == timeframe]
            if len(summaries) != 1 or type(summaries[0].get("candidate_count")) is not int or \
                    summaries[0]["candidate_count"] != len(slate["candidates"]):
                raise ArtifactError("artifact_invalid", 422, "Artifact candidate count is inconsistent.")
            comparison = read_stable(root, f"{timeframe}/comparison.csv", FILE_LIMIT,
                                     snaps[f"{timeframe}/comparison.csv"])
            scenario_rows = _csv_data_row_count(comparison)
            if type(summaries[0].get("scenario_count")) is not int or summaries[0]["scenario_count"] != scenario_rows:
                raise ArtifactError("artifact_invalid", 422, "Artifact scenario count is inconsistent.")
            if isinstance(report.get("protocol_id"), str): protocols.append(report["protocol_id"])
            if isinstance(report.get("source_identity"), str): source_identities.append(report["source_identity"])
            known |= required
            known |= {name for name in snaps if name.startswith(f"{timeframe}/data/") and name.endswith(".csv")}
            known |= {name for name in snaps if name.startswith(f"{timeframe}/runs/") and name.endswith(("/equity.csv", "/fills.csv", "/orders.csv"))}
        if any(not isinstance(x, dict) for x in summary["experiments"]):
            raise ArtifactError("artifact_invalid", 422, "Artifact experiment summary is invalid.")
        for experiment in summary["experiments"]:
            if type(experiment.get("scenario_failures")) is not int or experiment["scenario_failures"] < 0:
                raise ArtifactError("artifact_invalid", 422, "Artifact scenario failure count is invalid.")
        selected = [x.get("selected_candidate") for x in summary["experiments"] if x.get("selected_candidate")]
        evaluated = any(x.get("holdout_status") not in (None, "not_run_no_selection") for x in summary["experiments"])
        status = {**base, "export": "complete", "integrity": "verified",
                  "evidence": "synthetic" if summary.get("evidence") == "synthetic_only" else "unknown",
                  # The mixed summary may include test failures. Inventory
                  # metadata must not disclose their result before a reveal.
                  "scenario_outcome": "unknown",
                  "selection": "selected" if selected else "no_selection",
                  "holdout_evaluation": "evaluated" if evaluated else "not_evaluated"}
        protected = {n for n in snaps if "/test-" in f"/{n}"}
        known |= mandatory
        mixed = known - {"complete.json"}
        for name in known:
            if name.endswith(".json"):
                if not isinstance(_load_json(root, snaps, name), dict):
                    raise ArtifactError("artifact_invalid", 422, "Artifact JSON shape is invalid.")
            elif name.endswith(".csv"): _validate_csv(read_stable(root, name, FILE_LIMIT, snaps[name]), name)
    identity = "sha256:" + sha256("\n".join(f"{k}:{checksums[k]}" for k in sorted(checksums)).encode()).hexdigest()
    file_hashes = dict(checksums)
    file_hashes["complete.json"] = sha256(read_stable(root, "complete.json", METADATA_LIMIT, snaps["complete.json"])).hexdigest()
    result = Artifact(root, kind, snaps, {**status, "identity": identity, "protocol_ids": protocols,
                                        "source_identities": source_identities, "file_hashes": file_hashes},
                      protected, mixed, known)
    _unchanged(root, snaps)
    return result


def _unchanged(root: Path, expected: dict[str, Snapshot]) -> None:
    if inventory(root) != expected:
        raise ArtifactError("artifact_changed", 409, "Artifact changed during verification.")


def _content_identity(root: Path, snaps: dict[str, Snapshot]) -> str:
    digest = sha256()
    for name, snap in snaps.items():
        digest.update(name.encode()); digest.update(b"\0")
        digest.update(sha256(read_stable(root, name, FILE_LIMIT, snap)).digest())
    return "sha256:" + digest.hexdigest()


def _file_hashes(root: Path, snaps: dict[str, Snapshot]) -> dict[str, str]:
    return {name: sha256(read_stable(root, name, FILE_LIMIT, snap)).hexdigest() for name, snap in snaps.items()}


def invalid_artifact(root: Path, reason: str) -> Artifact:
    try: snaps = inventory(root)
    except ArtifactIOError as exc: raise ArtifactError("artifact_forbidden", 403, "Artifact is unavailable.") from exc
    kind = _kind(snaps)
    unsupported = reason == "artifact_unsupported"
    export = "unsupported_format" if unsupported else "complete" if "complete.json" in snaps else "incomplete"
    metadata = {"export": export, "integrity": "unchecked" if unsupported else "invalid",
                "evidence": "unknown", "scenario_outcome": "unknown",
                "selection": "unknown", "holdout_evaluation": "unknown", "identity": _content_identity(root, snaps),
                "protocol_ids": [], "errors": [reason], "file_hashes": _file_hashes(root, snaps)}
    return Artifact(root, kind, snaps, metadata, set(), set(), set())


def _kind(snapshots: dict) -> str:
    # An invalid/unknown v2 wrapper must not silently downgrade to v1.
    if "research_export.json" in snapshots: return "research_export_v2"
    if "manifest.json" in snapshots: return "single_run_v1"
    return "strategy_lab_v1" if "configuration.json" in snapshots else "unsupported"
