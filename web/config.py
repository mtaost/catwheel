from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class WebConfig:
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    state_db: Path
    timezone: str = "America/New_York"

    @classmethod
    def from_environment(cls) -> "WebConfig":
        required = {
            "INFLUXDB_READ_TOKEN": os.environ.get("INFLUXDB_READ_TOKEN"),
            "BUCKET": os.environ.get("BUCKET"),
            "ORG": os.environ.get("ORG"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise RuntimeError("Missing required web configuration: " + ", ".join(missing))
        return cls(
            influx_url=required["DATABASE_URL"],
            influx_token=required["INFLUXDB_READ_TOKEN"],
            influx_org=required["ORG"],
            influx_bucket=required["BUCKET"],
            state_db=Path(os.environ.get("CATWHEEL_STATE_DB", "/var/lib/catwheel/state.db")),
            timezone=os.environ.get("DASHBOARD_TIME_ZONE", "America/New_York"),
        )
