"""Inert, disclosure-bound views of accepted test jobs, not arbitrary imports."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import time

from .artifact_io import FILE_LIMIT, METADATA_LIMIT, inventory, read_stable, validate_root
from .disclosures import DisclosureJournal
from .dto import artifact_value
from .jobs import JobError
from .test_evidence import FILES, verify_test_evidence_files
from .test_identity import compare_identity
from .test_preflight import prepare_test_run
from .test_snapshots import load_test_snapshot


GROUPS = ("source", "tests", "binaries", "build")
TERMINAL = {"succeeded", "failed", "canceled", "interrupted"}


def _unavailable():
    return JobError("test_evidence_unavailable", 409, "Verified test evidence is unavailable.")


def _record(manager, job_id):
    if not isinstance(job_id, str) or re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
        raise JobError("job_not_found", 404, "Test job unavailable.")
    manager._open()
    with manager._connect() as db:
        row = db.execute("SELECT * FROM gui_jobs WHERE id=? AND kind='test_run'", (job_id,)).fetchone()
    if row is None:
        raise JobError("job_not_found", 404, "Test job unavailable.")
    return dict(row)


def list_tests(manager, offset, limit):
    manager._open()
    with manager._connect() as db:
        total = db.execute("SELECT count(*) FROM gui_jobs WHERE kind='test_run'").fetchone()[0]
        rows = db.execute("SELECT * FROM gui_jobs WHERE kind='test_run' ORDER BY sequence DESC LIMIT ? OFFSET ?",
                          (limit, offset)).fetchall()
    return {"items": [artifact_value(manager._dto(row)) for row in rows],
            "total": total, "offset": offset, "limit": limit}


@dataclass(frozen=True)
class BoundEvidence:
    evidence: object
    identity: str


def _load(manager, row):
    """Bind exact bytes to a durable accepted request; never execute discovery."""
    if row["state"] not in TERMINAL:
        raise _unavailable()
    try:
        relative = f'build/gui/jobs/{row["id"]}/artifacts'
        if row["output_path"] != relative:
            raise ValueError
        hashes = json.loads(row["snapshot"])["snapshot_hashes"]
        snapshot, prepared = load_test_snapshot(manager.repository, row["id"], hashes)
        if row["source_identity"] != "sha256:" + snapshot.request_sha256:
            raise ValueError
        root = validate_root(manager.repository, relative)
        before = inventory(root)
        if set(before) != FILES:
            raise ValueError
        files = {name: read_stable(root, name, METADATA_LIMIT if name in {"manifest.json", "complete.json"}
                                  else FILE_LIMIT, before[name]) for name in sorted(FILES)}
        verified = verify_test_evidence_files(files)
        manifest, accepted = verified.manifest, prepared.value
        if (manifest["job_id"] != row["id"] or manifest["repository_path"] != str(manager.repository)
                or manifest["runner"] != accepted["runner"]
                or manifest["versions"] != accepted["versions"]
                or manifest["start_identity"] != accepted["identity"]):
            raise ValueError
        # Failed/skipped/interrupted records and changed end identities are valid
        # history. Do not apply the supervisor's success-only acceptance gate.
        if (inventory(validate_root(manager.repository, relative)) != before
                or load_test_snapshot(manager.repository, row["id"], hashes) != (snapshot, prepared)):
            raise ValueError
        binding = {"job_id": row["id"], "request": snapshot.snapshot_hashes,
                   "files": {name: sha256(data).hexdigest() for name, data in files.items()}}
        identity = "test-evidence-v1:" + sha256(json.dumps(binding, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
        return BoundEvidence(verified, identity)
    except Exception:
        raise _unavailable() from None


def _authorized(manager, bound):
    if not DisclosureJournal(manager.repository).revealed([bound.identity]):
        raise JobError("test_evidence_protected", 403, "Reveal test evidence explicitly before viewing it.")


def _stable(manager, row, bound):
    if _record(manager, row["id"]) != row or _load(manager, row) != bound:
        raise _unavailable()


def _case_page(cases, offset, limit):
    # The parser's per-event bounds are insufficient for a whole HTTP response.
    # Bound aggregate detail text and events too, preserving every case row.
    remaining, event_budget = 65536, 200
    truncated, result = False, []

    def text(value):
        nonlocal remaining, truncated
        if value is None:
            return None
        kept = value[:min(4096, remaining)]
        remaining -= len(kept)
        truncated |= len(kept) != len(value)
        return kept

    for case in cases[offset:offset + limit]:
        duration = case.duration
        if duration is not None and len(duration) > 128:
            duration = None  # Do not turn a truncated numeric string into a new value.
            truncated = True
        item = {"ordinal": case.ordinal, "name": text(case.name), "classname": text(case.classname),
                "outcome": case.status, "duration": duration, "issues": []}
        for issue in case.events:
            if event_budget == 0:
                truncated = True
                break
            event_budget -= 1
            item["issues"].append({"kind": issue.kind, "message": text(issue.message), "text": text(issue.text)})
        result.append(item)
    return result, truncated


def test_detail(manager, job_id, offset=0, limit=25):
    row = _record(manager, job_id)
    output = {"job": artifact_value(manager._dto(row)), "evidence": {"state": "unavailable"}}
    try:
        bound = _load(manager, row)
    except JobError:
        return output
    journal = DisclosureJournal(manager.repository)
    if not journal.revealed([bound.identity]):
        output["evidence"] = {"state": "protected"}
        return output
    verified = bound.evidence
    manifest, report = verified.manifest, verified.report
    cases, truncated = _case_page(report.cases, offset, limit)
    value = {"state": "verified", "disclosure": "revealed_outside_protocol",
             "runner": manifest["runner"], "outcome": verified.outcome,
             "counts": {key: getattr(report, key) for key in ("tests", "passed", "failed", "errors", "skipped", "xfailed")},
             "identity_stability": dict(verified.identity_stability),
             "current_match": {group: "unknown" for group in GROUPS},
             "source_to_binary": "unknown", "versions": manifest["versions"],
             "termination": manifest["termination"], "exit_code": manifest["exit_code"],
             "log_truncated": manifest["log_truncated"], "details_truncated": truncated,
             "cases": cases, "total": len(report.cases), "offset": offset, "limit": limit}
    _stable(manager, row, bound)
    _authorized(manager, bound)
    output["evidence"] = value
    return output


test_detail.__test__ = False


def reveal_test(manager, job_id):
    row = _record(manager, job_id)
    bound = _load(manager, row)
    journal = DisclosureJournal(manager.repository)
    try:
        journal.mark_protected([bound.identity])
        journal.reveal([bound.identity])
        _stable(manager, row, bound)
        _authorized(manager, bound)
    except Exception:
        raise JobError("test_disclosure_unavailable", 409, "Test disclosure could not be recorded.") from None
    return {"disclosure": "revealed_outside_protocol"}


def check_current(manager, job_id):
    row = _record(manager, job_id)
    bound = _load(manager, row)
    _authorized(manager, bound)
    result = {group: "unknown" for group in GROUPS}
    versions_match = "unknown"
    try:
        current = prepare_test_run(manager.repository, bound.evidence.manifest["runner"]).value
        result = compare_identity(bound.evidence.manifest["end_identity"], current["identity"])
        versions_match = "same" if current["versions"] == bound.evidence.manifest["versions"] else "different"
    except Exception:
        pass  # Unknown is not same, and preflight error text remains private.
    _stable(manager, row, bound)
    _authorized(manager, bound)
    return {"current_match": result, "versions_match": versions_match,
            "checked_ns": str(time.time_ns()), "source_to_binary": "unknown"}
