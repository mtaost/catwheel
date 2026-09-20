from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import influxdb_client


def _flux_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class InfluxRunRepository:
    """Read-only InfluxDB access used only by the server-side dashboard."""

    def __init__(self, url: str, token: str, org: str, bucket: str):
        self.client = influxdb_client.InfluxDBClient(url=url, token=token, org=org)
        self.org = org
        self.bucket = bucket
        self.query_api = self.client.query_api()

    def close(self) -> None:
        self.client.close()

    def completed_runs(self, start: datetime, stop: datetime) -> list[dict[str, Any]]:
        query = f'''from(bucket: "{self.bucket}")
  |> range(start: {_flux_time(start)}, stop: {_flux_time(stop)})
  |> filter(fn: (r) => r._measurement == "catwheel_run_metadata")
  |> pivot(rowKey: ["_time", "run_id"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)'''
        rows = []
        for table in self.query_api.query(query=query, org=self.org):
            for record in table.records:
                value = record.values
                rows.append(
                    {
                        "run_id": value.get("run_id"),
                        "timestamp": value.get("_time"),
                        "max_speed_mph": float(value.get("max_speed_mph", 0)),
                        "avg_speed_mph": float(value.get("avg_speed_mph", 0)),
                        "distance_travelled_ft": float(value.get("distance_travelled_ft", 0)),
                        "run_duration_seconds": float(value.get("run_duration_seconds", 0)),
                        "settings_revision": int(value.get("settings_revision", 1)),
                        "rejected_sample_count": int(value.get("rejected_sample_count", 0)),
                    }
                )
        # Flux sorts within result tables; runs can still arrive table-by-table
        # when tag groups differ. Normalize the complete result for consumers.
        return sorted((row for row in rows if row["run_id"]), key=lambda row: row["timestamp"], reverse=True)

    def speed_samples(self, run_id: str, start: datetime, stop: datetime) -> list[dict[str, Any]]:
        query = f'''from(bucket: "{self.bucket}")
  |> range(start: {_flux_time(start)}, stop: {_flux_time(stop)})
  |> filter(fn: (r) => r._measurement == "catwheel_speed" and r.run_id == "{run_id}")
  |> filter(fn: (r) => r._field == "speed")
  |> keep(columns: ["_time", "_value"])
  |> sort(columns: ["_time"])'''
        samples = []
        for table in self.query_api.query(query=query, org=self.org):
            for record in table.records:
                samples.append({"timestamp": record.values.get("_time"), "speed_mph": float(record.values["_value"])})
        return samples
