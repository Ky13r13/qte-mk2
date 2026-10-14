"""Authorized views of explicitly registered local validation artifacts."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import PurePosixPath
import re

from .artifacts import ArtifactError, strict_json
from .form_inputs import capture_form_inputs
from .job_inputs import CONFIG_LIMIT, JobInputError, _read_regular, verify_snapshot
from .research_forms import config_origin


def dataset_view(catalog, artifact_id: str) -> dict:
    artifact = catalog.inspect(artifact_id)
    if artifact.kind != "dataset_validation_v1" or artifact.metadata.get("integrity") != "verified":
        raise ArtifactError("dataset_unavailable", 422, "A verified dataset validation is required.")
    # Authorize the entire projection before exposing any data-derived fields.
    if any(not row["available"] for row in catalog._files(artifact)):
        raise ArtifactError("holdout_protected", 403, "Dataset disclosure is required.")

    def read(name):
        raw, _ = catalog.read_file(artifact_id, sha256(name.encode()).hexdigest())
        return strict_json(raw)

    summary, manifest, configuration = read("summary.json"), read("dataset_validation.json"), read("configuration.json")
    registered = catalog.get(artifact_id)
    source = {"state": "unavailable", "reason": "The matching original source is unavailable.", "data": None}
    data = dict(configuration["data"])
    relative = registered["relative_path"]
    match = re.fullmatch(r"build/gui/jobs/([0-9a-f]{32})/artifacts", relative)
    try:
        if match:
            db = catalog._connect()
            if db is None:
                raise ValueError("missing job")
            try:
                row = db.execute("SELECT kind,state,snapshot FROM gui_jobs WHERE id=?", (match[1],)).fetchone()
            finally:
                db.close()
            if row is None or row["kind"] != "dataset_validate" or row["state"] != "succeeded":
                raise ValueError("invalid job")
            recorded = strict_json(row["snapshot"].encode())
            snapshot = verify_snapshot(catalog.repository, match[1], recorded["snapshot_hashes"])
            if snapshot.kind != "dataset_validate" or snapshot.hashes["data_sha256"] != manifest["data_sha256"]:
                raise ValueError("invalid source")
            effective_raw = _read_regular(catalog.repository, snapshot.effective_config_path, CONFIG_LIMIT)
            if sha256(effective_raw).hexdigest() != snapshot.hashes["effective_config_sha256"]:
                raise ValueError("changed source snapshot")
            expected = strict_json(effective_raw)
            expected["data"]["path"] = configuration["data"]["path"]
            if json.dumps(expected, sort_keys=True) != json.dumps(configuration, sort_keys=True):
                raise ValueError("changed validation configuration")
            data["path"] = snapshot.original_data_path
        else:
            # Terminal-created exports may live in ordinary allowed input roots.
            # The same normal capture policy applies; reserved roots stay denied.
            data["path"] = str(PurePosixPath(relative) / data["path"])
        data["expected_sha256"] = manifest["data_sha256"]
        wire = dict(data)
        wire["interval_ns"] = str(wire["interval_ns"])
        captured = capture_form_inputs(catalog.repository, "dataset_validate", config_origin(), {"data": wire})
        catalog.authorize_job_inputs(captured)
        if captured.data_sha256 == manifest["data_sha256"]:
            source = {"state": "available", "reason": "Source bytes match validation; preview and launch recheck the checksum.", "data": wire}
    except (JobInputError, ValueError, KeyError, TypeError, AttributeError):
        pass
    # Detect mutation between metadata reads and projection.
    latest = catalog.inspect(artifact_id)
    if latest.snapshots != artifact.snapshots or latest.metadata != artifact.metadata:
        raise ArtifactError("artifact_changed", 409, "Dataset changed while the view was being read.")
    if any(not row["available"] for row in catalog._files(latest)):
        raise ArtifactError("holdout_protected", 403, "Dataset disclosure is required.")
    return {"id": artifact_id, "name": registered["name"], "kind": artifact.kind,
            "summary": summary, "manifest": manifest, "source": source,
            "status": registered["status"]}
