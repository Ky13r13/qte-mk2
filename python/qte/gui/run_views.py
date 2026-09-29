"""Read-only G3a views over verified single-run v1 exports."""

from __future__ import annotations

import csv
import io
import math

from .artifacts import ArtifactError, strict_json
from .catalog import Catalog
from .charts import reduce_points

TABLES = {"orders": "orders.csv", "fills": "fills.csv", "equity": "equity.csv",
          "sampled_equity": "sampled_equity.csv"}
METRIC_UNITS = {
    "average_losing_trade": "currency", "average_winning_trade": "currency", "expectancy": "currency",
    "annualized_return": "ratio", "annualized_volatility": "ratio", "average_gross_exposure": "ratio",
    "calmar_ratio": "ratio", "maximum_drawdown": "ratio", "profit_factor": "ratio",
    "sharpe_ratio": "ratio", "sortino_ratio": "ratio", "total_return": "ratio", "turnover": "ratio",
    "win_rate": "ratio",
}


class RunViews:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def detail(self, artifact_id: str) -> dict:
        artifact = self._single(artifact_id)
        catalog_dto = self.catalog.get(artifact_id)
        manifest = self._json(artifact_id, "manifest.json")
        report = self._json(artifact_id, "report.json")
        data, sampling = self._data_contract(manifest)
        metrics = {}
        for name, recorded in report["metrics"].items():
            metrics[name] = {"value": recorded["value"], "undefined_reason": recorded["undefined_reason"],
                             "unit": METRIC_UNITS.get(name, "unknown"),
                             "recorded_sampling_policy": sampling}
        source = manifest.get("source", {})
        self._unchanged(artifact_id, artifact)
        return {
            "id": artifact_id, "name": catalog_dto["name"], "kind": artifact.kind,
            "status": catalog_dto["status"], "metrics": metrics,
            "counts": {"fills": report["fill_count"], "orders": report["order_count"],
                       "trades": report["trade_count"], "open_trades": report["open_trade_count"]},
            "recordings": {"event_equity": "recorded", "sampled_equity":
                           "recorded" if "sampled_equity.csv" in artifact.snapshots else "not_recorded"},
            "provenance": {
                "source_id": manifest.get("source_id"), "source_identity": manifest.get("source_identity"),
                "provider": source.get("provider"), "adapter_version": source.get("adapter_version"),
                "schema_id": source.get("schema_id"), "source_sha256": source.get("source_sha256"),
                "assumptions": source.get("assumptions", []), "execution_model": manifest.get("execution_model"),
                "research_label": manifest.get("research_label"), "dataset_hash": manifest.get("dataset_hash"),
                "dataset_hash_algorithm": manifest.get("dataset_hash_algorithm"),
                "dataset_start_ns": manifest.get("dataset_start_ns"), "dataset_end_ns": manifest.get("dataset_end_ns"),
                "interval_ns": data.get("interval_ns"), "sampling": sampling,
            },
            "capabilities": {name: "not_recorded" for name in
                             ("positions", "closed_trades", "open_trades", "order_events", "event_sequence")},
            "warnings": ["Legacy v1 exports do not record event sequence or owned trade/order-event detail."],
        }

    def series(self, artifact_id: str, sampling: str = "event", max_points: int = 2000,
               start_ns: int | None = None, end_ns: int | None = None) -> dict:
        artifact = self._single(artifact_id)
        if sampling not in {"event", "sampled"} or type(max_points) is not int or not 2 <= max_points <= 2000:
            raise ArtifactError("invalid_chart_query", 400, "Invalid chart query.")
        for value in (start_ns, end_ns):
            if value is not None and (type(value) is not int or not -(2**63) <= value < 2**63):
                raise ArtifactError("invalid_chart_query", 400, "Invalid chart range.")
        if start_ns is not None and end_ns is not None and start_ns > end_ns:
            raise ArtifactError("invalid_chart_query", 400, "Invalid chart range.")
        filename = "equity.csv" if sampling == "event" else "sampled_equity.csv"
        rows = self._csv(artifact_id, filename)
        manifest = self._json(artifact_id, "manifest.json")
        data, declared_sampling = self._data_contract(manifest)
        interval = (data.get("interval_ns") if sampling == "event"
                    else declared_sampling.get("interval_ns") if isinstance(declared_sampling, dict) else None)
        interval = interval if type(interval) is int and interval > 0 else None
        points = []
        previous = None
        previous_source_time = None
        for ordinal, row in enumerate(rows):
            timestamp = int(row["timestamp_ns"])
            if previous_source_time is not None and timestamp < previous_source_time:
                raise ArtifactError("artifact_invalid", 422, "Recorded equity timestamps are not chronological.")
            previous_source_time = timestamp
            if start_ns is not None and timestamp < start_ns or end_ns is not None and timestamp > end_ns:
                continue
            equity, exposure = float(row["equity"]), float(row["gross_exposure"])
            if not math.isfinite(equity) or not math.isfinite(exposure):
                raise ArtifactError("artifact_invalid", 422, "Recorded equity is invalid.")
            point = {"timestamp_ns": timestamp, "row_ordinal": ordinal, "sequence": None,
                     "equity": equity, "gross_exposure": exposure,
                     "gap_before": previous is not None and interval is not None and timestamp - previous > interval}
            points.append(point); previous = timestamp
        display = reduce_points(points, max_points)
        self._unchanged(artifact_id, artifact)
        warnings = ["Legacy v1 equity rows do not record event sequence."]
        if interval is None: warnings.append("No fixed interval was recorded; gaps are not inferred.")
        return {"artifact_id": artifact_id, "sampling": sampling, "raw_count": len(points),
                "display_count": len(display), "points": display, "warnings": warnings,
                "gap_policy": "recorded_fixed_interval" if interval is not None else "not_declared",
                "sequence_status": "not_recorded"}

    def table(self, artifact_id: str, table: str = "orders", offset: int = 0, limit: int = 25) -> dict:
        artifact = self._single(artifact_id)
        if table not in TABLES or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ArtifactError("invalid_table_query", 400, "Invalid table query.")
        rows, columns = self._csv(artifact_id, TABLES[table], columns=True)
        self._unchanged(artifact_id, artifact)
        return {"artifact_id": artifact_id, "table": table, "columns": columns,
                "rows": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit,
                "warnings": ["Values are recorded source strings; missing legacy fields are not reconstructed."]}

    def _single(self, artifact_id: str):
        artifact = self.catalog.inspect(artifact_id)
        if artifact.kind != "single_run_v1" or artifact.metadata.get("integrity") != "verified":
            raise ArtifactError("run_view_unavailable", 409, "Verified single-run view unavailable.")
        return artifact

    def _unchanged(self, artifact_id: str, original) -> None:
        # A view combines independently authorized files. They must still belong
        # to the same verified generation, even if an external process replaced
        # both the payloads and their valid completion marker during this read.
        latest = self.catalog.inspect(artifact_id)
        if latest.snapshots != original.snapshots or latest.metadata != original.metadata:
            raise ArtifactError("artifact_changed", 409, "Artifact changed while the view was being read.")

    @staticmethod
    def _data_contract(manifest: dict) -> tuple[dict, dict | None]:
        data = manifest.get("configuration", {}).get("data", {})
        sampling = manifest.get("sampling")
        if not isinstance(data, dict) or (sampling is not None and not isinstance(sampling, dict)):
            raise ArtifactError("artifact_invalid", 422, "Recorded data or sampling metadata is invalid.")
        return data, sampling

    def _file(self, artifact_id: str, name: str) -> bytes:
        detail = self.catalog.get(artifact_id)
        match = next((item for item in detail.get("files", []) if item["name"] == name), None)
        if match is None:
            raise ArtifactError("artifact_file_not_found", 404, "Artifact file unavailable.")
        return self.catalog.read_file(artifact_id, match["id"])[0]

    def _json(self, artifact_id: str, name: str) -> dict:
        try: value = strict_json(self._file(artifact_id, name))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ArtifactError("artifact_invalid", 422, "Artifact metadata is invalid.") from exc
        if not isinstance(value, dict): raise ArtifactError("artifact_invalid", 422, "Artifact metadata is invalid.")
        return value

    def _csv(self, artifact_id: str, name: str, columns: bool = False):
        try:
            reader = csv.DictReader(io.StringIO(self._file(artifact_id, name).decode("utf-8")), strict=True)
            if reader.fieldnames is None: raise ValueError
            rows = [dict(row) for row in reader]
        except (UnicodeDecodeError, csv.Error, ValueError) as exc:
            raise ArtifactError("artifact_invalid", 422, "Artifact CSV is invalid.") from exc
        return (rows, list(reader.fieldnames)) if columns else rows
