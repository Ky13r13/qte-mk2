"""Versioned canonical market observations for offline charting, not replay logic."""
from __future__ import annotations

import math

import qte

MARKET_FILE = "market_data.json"
BAR_FIELDS = {"symbol", "start_ns", "end_ns", "available_ns", "open", "high", "low", "close", "volume"}


def market_recording(loaded) -> dict:
    if not loaded.bars:
        raise ValueError("captured canonical bars are required")
    timed = loaded.dataset.timing_policy == "explicit_availability_v1"
    times = loaded.publication_times_ns if timed else (bar.end_ns for bar in loaded.bars)
    rows = sorted(zip(loaded.bars, times, strict=True), key=lambda row: (row[0].start_ns, row[0].symbol))
    return {"schema_version": 2 if timed else 1,
            "timing_policy": "explicit_availability_v1" if timed else "completed_bar_at_end",
            "dataset_hash": loaded.dataset.hash,
            "source_sha256": loaded.provenance.source_sha256,
            "candles": [{"symbol": bar.symbol, "start_ns": bar.start_ns,
                         "end_ns": bar.end_ns, "available_ns": publication,
                         **{field: getattr(bar, field) for field in ("open", "high", "low", "close", "volume")}}
                        for bar, publication in rows]}


def validate_market_recording(value: object, manifest: dict) -> list[dict]:
    """Validate exact fields and reconstruct the canonical dataset identity.

    This verifies recorded consistency, not provider authenticity. No source
    paths are read, strategies run, or inferred regimes added.
    """
    def invalid():
        raise ValueError("canonical market recording is inconsistent")

    data = manifest["configuration"]["data"]
    timed = data.get("adapter") == "session_bundle"
    if (type(value) is not dict or set(value) != {"schema_version", "timing_policy", "dataset_hash", "source_sha256", "candles"}
            or type(value["schema_version"]) is not int or value["schema_version"] != (2 if timed else 1)
            or value["timing_policy"] != ("explicit_availability_v1" if timed else "completed_bar_at_end")
            or value["dataset_hash"] != manifest["dataset_hash"]
            or value["source_sha256"] != manifest["source"]["source_sha256"]
            or type(value["candles"]) is not list or not value["candles"]):
        invalid()
    bars, previous = [], None
    for row in value["candles"]:
        if type(row) is not dict or set(row) != BAR_FIELDS or type(row["symbol"]) is not str:
            invalid()
        for field in ("start_ns", "end_ns", "available_ns"):
            if type(row[field]) is not int or not -(2**63) <= row[field] < 2**63:
                invalid()
        if row["available_ns"] < row["end_ns"] or (not timed and row["available_ns"] != row["end_ns"]):
            invalid()
        for field in ("open", "high", "low", "close", "volume"):
            if type(row[field]) not in (int, float) or not math.isfinite(row[field]):
                invalid()
        key = (row["start_ns"], row["symbol"])
        if previous is not None and key <= previous:
            invalid()
        previous = key
        bars.append(qte.Bar(row["symbol"], row["start_ns"], row["end_ns"],
                            row["open"], row["high"], row["low"], row["close"], row["volume"]))
    kwargs = dict(source_id=manifest["source_id"], tick_size=data.get("tick_size", 0.01),
                  currency=data.get("currency", "USD"), volume_unit="shares")
    if timed:
        timing = manifest["timing"]
        observations = [qte.BarObservation(bar, row["available_ns"])
                        for bar, row in zip(bars, value["candles"], strict=True)]
        dataset = qte.Dataset.from_observations(observations, timing_source_id=timing["timing_source_id"], **kwargs)
        if (dataset.timing_policy != timing["timing_policy"] or dataset.observation_end_ns != timing["observation_end_ns"]
                or dataset.end_ns != timing["replay_end_ns"]):
            invalid()
    else:
        dataset = qte.Dataset.from_bars(bars, interval_ns=data["interval_ns"], **kwargs)
    if (dataset.hash != manifest["dataset_hash"] or dataset.start_ns != manifest["dataset_start_ns"]
            or dataset.end_ns != manifest["dataset_end_ns"]):
        invalid()
    return value["candles"]
