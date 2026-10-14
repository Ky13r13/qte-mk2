"""Authorized projections of explicitly registered, verified download caches."""
from hashlib import sha256

from qte.download_artifact import verify_download_files
from .artifacts import ArtifactError


def download_view(catalog, artifact_id: str) -> dict:
    artifact = catalog.inspect(artifact_id)
    if artifact.kind != "alpaca_download_v1" or artifact.metadata.get("integrity") != "verified":
        raise ArtifactError("download_unavailable", 422, "A verified historical download is required.")
    if any(not row["available"] for row in catalog._files(artifact)):
        raise ArtifactError("holdout_protected", 403, "Dataset disclosure is required.")
    files = {name: catalog.read_file(artifact_id, sha256(name.encode()).hexdigest())[0]
             for name in artifact.snapshots}
    try:
        checked = verify_download_files(files)
    except (ValueError, TypeError, KeyError):
        raise ArtifactError("artifact_invalid", 422, "Historical download is invalid.") from None
    latest = catalog.inspect(artifact_id)
    if latest.snapshots != artifact.snapshots or latest.metadata != artifact.metadata:
        raise ArtifactError("artifact_changed", 409, "Download changed while the view was being read.")
    if any(not row["available"] for row in catalog._files(latest)):
        raise ArtifactError("holdout_protected", 403, "Dataset disclosure is required.")
    registered = catalog.get(artifact_id)
    return {"id": artifact_id, "name": registered["name"], "kind": artifact.kind,
            "summary": checked["summary"], "manifest": checked["manifest"],
            "configuration": checked["configuration"], "status": registered["status"]}
