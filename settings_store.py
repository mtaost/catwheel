"""Shared, durable settings and annotation storage for Catwheel.

InfluxDB remains the source of run telemetry.  This SQLite database contains
small pieces of application state that are edited by people or the web app.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping


CAT_IDS = ("unknown", "Lumi", "Miso")


class SettingsConflictError(RuntimeError):
    """Raised when an admin tries to overwrite a newer settings revision."""


@dataclass(frozen=True)
class DetectionSettings:
    """The adjustable run-detection settings, expressed in display units."""

    revision: int = 1
    idle_timeout_seconds: float = 45.0
    min_duration_seconds: float = 5.0
    min_peak_speed_mph: float = 1.4763804
    min_distance_ft: float = 10.3018376
    max_valid_speed_mph: float = 16.0
    drop_correction_mph: float = 4.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "DetectionSettings":
        allowed = {field for field in cls.__dataclass_fields__}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown detection settings: {', '.join(sorted(unknown))}")
        converted = dict(values)
        for name in allowed - {"revision"}:
            if name in converted:
                converted[name] = float(converted[name])
        if "revision" in converted:
            converted["revision"] = int(converted["revision"])
        settings = cls(**converted)
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.revision < 1:
            raise ValueError("revision must be positive")
        positive = (
            "idle_timeout_seconds",
            "min_duration_seconds",
            "min_peak_speed_mph",
            "min_distance_ft",
            "max_valid_speed_mph",
        )
        for name in positive:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite value greater than zero")
        if not math.isfinite(self.drop_correction_mph) or self.drop_correction_mph < 0:
            raise ValueError("drop_correction_mph must be finite and non-negative")
        if self.min_peak_speed_mph > self.max_valid_speed_mph:
            raise ValueError("min_peak_speed_mph cannot exceed max_valid_speed_mph")

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


class SettingsStore:
    """A short-lived-connection SQLite repository safe for two local services."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._set_shared_permissions()

    def _set_shared_permissions(self) -> None:
        """Keep SQLite files writable by the logger and web-service group."""
        for candidate in (self.path, self.path.with_name(self.path.name + "-wal"), self.path.with_name(self.path.name + "-shm")):
            if candidate.exists():
                try:
                    candidate.chmod(0o660)
                except PermissionError:
                    # The other service may own an already group-writable file.
                    # It still has the required read/write access through the
                    # shared catwheel group, so changing its mode is unnecessary.
                    continue

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        self._set_shared_permissions()
        return connection

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS detection_settings (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    revision INTEGER NOT NULL,
                    idle_timeout_seconds REAL NOT NULL,
                    min_duration_seconds REAL NOT NULL,
                    min_peak_speed_mph REAL NOT NULL,
                    min_distance_ft REAL NOT NULL,
                    max_valid_speed_mph REAL NOT NULL,
                    drop_correction_mph REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_labels (
                    run_id TEXT PRIMARY KEY,
                    cat_id TEXT NOT NULL CHECK (cat_id IN ('unknown', 'Lumi', 'Miso')),
                    source TEXT NOT NULL DEFAULT 'manual',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_predictions (
                    run_id TEXT NOT NULL,
                    cat_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    model_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, model_version)
                );
                CREATE TABLE IF NOT EXISTS telegram_notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    lease_until TEXT,
                    sent_at TEXT,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS telegram_notification_outbox_due
                    ON telegram_notification_outbox (sent_at, next_attempt_at);
                CREATE TABLE IF NOT EXISTS run_records (
                    metric TEXT PRIMARY KEY CHECK (metric IN ('max_speed', 'run_duration')),
                    value REAL NOT NULL,
                    run_id TEXT NOT NULL,
                    achieved_at TEXT NOT NULL
                );
                """
            )
            row = connection.execute("SELECT singleton FROM detection_settings WHERE singleton = 1").fetchone()
            if row is None:
                self._insert_settings(connection, DetectionSettings())

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _insert_settings(self, connection: sqlite3.Connection, settings: DetectionSettings) -> None:
        connection.execute(
            """
            INSERT INTO detection_settings (
                singleton, revision, idle_timeout_seconds, min_duration_seconds,
                min_peak_speed_mph, min_distance_ft, max_valid_speed_mph,
                drop_correction_mph, updated_at
            ) VALUES (1, :revision, :idle_timeout_seconds, :min_duration_seconds,
                :min_peak_speed_mph, :min_distance_ft, :max_valid_speed_mph,
                :drop_correction_mph, :updated_at)
            """,
            {**settings.to_dict(), "updated_at": self._now()},
        )

    @staticmethod
    def _row_to_settings(row: sqlite3.Row) -> DetectionSettings:
        fields = DetectionSettings.__dataclass_fields__
        return DetectionSettings.from_mapping({name: row[name] for name in fields})

    def get_settings(self) -> DetectionSettings:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM detection_settings WHERE singleton = 1").fetchone()
        assert row is not None
        return self._row_to_settings(row)

    def update_settings(
        self, changes: Mapping[str, Any], expected_revision: int
    ) -> DetectionSettings:
        editable = set(DetectionSettings.__dataclass_fields__) - {"revision"}
        unknown = set(changes) - editable
        if unknown:
            raise ValueError(f"Unknown detection settings: {', '.join(sorted(unknown))}")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM detection_settings WHERE singleton = 1").fetchone()
            assert row is not None
            current = self._row_to_settings(row)
            if current.revision != expected_revision:
                raise SettingsConflictError(
                    f"Settings changed from revision {expected_revision} to {current.revision}"
                )
            candidate = DetectionSettings.from_mapping(
                {**current.to_dict(), **dict(changes), "revision": current.revision + 1}
            )
            connection.execute(
                """
                UPDATE detection_settings SET
                    revision = :revision,
                    idle_timeout_seconds = :idle_timeout_seconds,
                    min_duration_seconds = :min_duration_seconds,
                    min_peak_speed_mph = :min_peak_speed_mph,
                    min_distance_ft = :min_distance_ft,
                    max_valid_speed_mph = :max_valid_speed_mph,
                    drop_correction_mph = :drop_correction_mph,
                    updated_at = :updated_at
                WHERE singleton = 1
                """,
                {**candidate.to_dict(), "updated_at": self._now()},
            )
        return candidate

    def set_label(self, run_id: str, cat_id: str) -> None:
        if cat_id not in CAT_IDS:
            raise ValueError(f"cat_id must be one of: {', '.join(CAT_IDS)}")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO run_labels (run_id, cat_id, source, updated_at)
                VALUES (?, ?, 'manual', ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    cat_id = excluded.cat_id,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (run_id, cat_id, self._now()),
            )

    def get_label(self, run_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT cat_id FROM run_labels WHERE run_id = ?", (run_id,)
            ).fetchone()
        return row["cat_id"] if row else "unknown"

    def labels_for_runs(self, run_ids: list[str]) -> dict[str, str]:
        if not run_ids:
            return {}
        placeholders = ", ".join("?" for _ in run_ids)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT run_id, cat_id FROM run_labels WHERE run_id IN ({placeholders})", run_ids
            ).fetchall()
        return {row["run_id"]: row["cat_id"] for row in rows}

    def enqueue_telegram_notification(self, run_id: str, payload: Mapping[str, Any]) -> None:
        """Durably enqueue one completion notification per run.

        The payload is retained instead of rendered image data so a retry can
        generate a fresh PNG without using a shared temporary directory.
        """
        now = self._now()
        notification_payload = dict(payload)
        with self._connection() as connection:
            record_breakers = []
            for metric in ("max_speed", "run_duration"):
                value = float(notification_payload[metric])
                current = connection.execute(
                    "SELECT value FROM run_records WHERE metric = ?", (metric,)
                ).fetchone()
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO run_records (metric, value, run_id, achieved_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (metric, value, run_id, now),
                    )
                elif value > float(current["value"]):
                    connection.execute(
                        """
                        UPDATE run_records
                        SET value = ?, run_id = ?, achieved_at = ?
                        WHERE metric = ?
                        """,
                        (value, run_id, now, metric),
                    )
                    record_breakers.append(metric)
            notification_payload["record_breakers"] = record_breakers
            connection.execute(
                """
                INSERT INTO telegram_notification_outbox (
                    run_id, payload_json, next_attempt_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (run_id, json.dumps(notification_payload, separators=(",", ":")), now),
            )

    def claim_due_telegram_notification(
        self, lease_seconds: float = 60.0, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """Atomically lease one due notification for a single send attempt."""
        now = now or datetime.now(timezone.utc)
        now_text = now.astimezone(timezone.utc).isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).astimezone(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM telegram_notification_outbox
                WHERE sent_at IS NULL
                  AND next_attempt_at <= ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY id
                LIMIT 1
                """,
                (now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE telegram_notification_outbox SET lease_until = ? WHERE id = ?",
                (lease_until, row["id"]),
            )
        notification = dict(row)
        notification["payload"] = json.loads(notification.pop("payload_json"))
        return notification

    def mark_telegram_notification_sent(self, notification_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE telegram_notification_outbox
                SET sent_at = ?, lease_until = NULL, last_error = NULL
                WHERE id = ?
                """,
                (self._now(), notification_id),
            )

    def retry_telegram_notification(
        self, notification_id: int, next_attempt_at: datetime, error: str
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE telegram_notification_outbox
                SET attempt_count = attempt_count + 1,
                    next_attempt_at = ?,
                    lease_until = NULL,
                    last_error = ?
                WHERE id = ?
                """,
                (next_attempt_at.astimezone(timezone.utc).isoformat(), error[:1000], notification_id),
            )
