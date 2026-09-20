from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


TREND_COPY = {
    "half_hour": ("Activity by half hour", "Distance traveled per 30-minute interval"),
    "hour": ("Hourly activity", "Distance traveled per hour"),
    "day": ("Daily activity", "Distance traveled per day"),
    "week": ("Weekly activity", "Distance traveled per week"),
}


def range_from_dates(start_date: date, end_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    return (
        datetime.combine(start_date, time.min, tzinfo=zone),
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone),
    )


def serialize_run(run: dict, label: str, timezone_name: str) -> dict:
    timestamp = run["timestamp"].astimezone(ZoneInfo(timezone_name))
    return {
        **run,
        "timestamp": timestamp.isoformat(),
        "short_date": timestamp.strftime("%b %-d"),
        "short_time": timestamp.strftime("%-I:%M %p"),
        "label": label,
    }


def _bucket_start(timestamp: datetime, bucket: str, zone: ZoneInfo) -> datetime:
    local = timestamp.astimezone(zone)
    if bucket == "half_hour":
        return local.replace(minute=local.minute - (local.minute % 30), second=0, microsecond=0)
    if bucket == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        return datetime.combine(local.date(), time.min, tzinfo=zone)
    if bucket == "week":
        return datetime.combine(local.date() - timedelta(days=local.weekday()), time.min, tzinfo=zone)
    raise ValueError(f"Unsupported trend bucket: {bucket}")


def _next_bucket(timestamp: datetime, bucket: str) -> datetime:
    if bucket == "half_hour":
        return timestamp + timedelta(minutes=30)
    if bucket == "hour":
        return timestamp + timedelta(hours=1)
    if bucket == "day":
        return timestamp + timedelta(days=1)
    if bucket == "week":
        return timestamp + timedelta(days=7)
    raise ValueError(f"Unsupported trend bucket: {bucket}")


def _bucket_label(timestamp: datetime, bucket: str) -> str:
    if bucket == "half_hour":
        return timestamp.strftime("%-I:%M %p")
    if bucket == "hour":
        return timestamp.strftime("%-I %p")
    if bucket == "day":
        return timestamp.strftime("%b %-d")
    return f"Week of {timestamp.strftime('%b %-d')}"


def activity_trend(runs: list[dict], start: datetime, stop: datetime, bucket: str, zone: ZoneInfo) -> list[dict]:
    """Aggregate completed runs into a complete, local-time graph axis."""
    values = defaultdict(lambda: {"distance_ft": 0.0, "run_count": 0, "active_seconds": 0.0})
    for run in runs:
        timestamp = datetime.fromisoformat(run["timestamp"])
        if not start <= timestamp < stop:
            continue
        key = _bucket_start(timestamp, bucket, zone).isoformat()
        values[key]["distance_ft"] += run["distance_travelled_ft"]
        values[key]["run_count"] += 1
        values[key]["active_seconds"] += run["run_duration_seconds"]

    trend = []
    cursor = _bucket_start(start, bucket, zone)
    while cursor < stop:
        value = values[cursor.isoformat()]
        trend.append(
            {
                "timestamp": cursor.isoformat(),
                "label": _bucket_label(cursor, bucket),
                "distance_ft": round(value["distance_ft"], 1),
                "run_count": value["run_count"],
                "active_seconds": round(value["active_seconds"], 1),
            }
        )
        cursor = _next_bucket(cursor, bucket)
    return trend


def dashboard_payload_for_window(
    repository, store, start: datetime, stop: datetime, cat_id: str | None, timezone_name: str, trend_bucket: str = "day"
) -> dict:
    """Build dashboard data for an exact start/stop window."""
    runs = repository.completed_runs(start, stop)
    labels = store.labels_for_runs([run["run_id"] for run in runs])
    enriched = [serialize_run(run, labels.get(run["run_id"], "unknown"), timezone_name) for run in runs]
    if cat_id:
        enriched = [run for run in enriched if run["label"] == cat_id]
    # Keep the recent-run list deterministic even if an adapter returns
    # separate Influx result tables in an arbitrary order.
    enriched.sort(key=lambda run: run["timestamp"], reverse=True)
    daily = defaultdict(lambda: {"distance_ft": 0.0, "run_count": 0, "active_seconds": 0.0})
    weekly = defaultdict(lambda: {"distance_ft": 0.0, "run_count": 0, "active_seconds": 0.0})
    zone = ZoneInfo(timezone_name)
    for run in enriched:
        day = datetime.fromisoformat(run["timestamp"]).astimezone(zone).date()
        week = day - timedelta(days=day.weekday())
        for bucket in (daily[day.isoformat()], weekly[week.isoformat()]):
            bucket["distance_ft"] += run["distance_travelled_ft"]
            bucket["run_count"] += 1
            bucket["active_seconds"] += run["run_duration_seconds"]
    summary = {
        "run_count": len(enriched),
        "total_distance_ft": round(sum(run["distance_travelled_ft"] for run in enriched), 1),
        "active_seconds": round(sum(run["run_duration_seconds"] for run in enriched), 1),
        "fastest_speed_mph": round(max((run["max_speed_mph"] for run in enriched), default=0), 2),
    }
    trend_title, trend_description = TREND_COPY[trend_bucket]
    return {
        "summary": summary,
        "runs": enriched,
        "daily": [{"date": key, **value} for key, value in sorted(daily.items())],
        "weekly": [{"date": key, **value} for key, value in sorted(weekly.items())],
        "trend": activity_trend(enriched, start, stop, trend_bucket, zone),
        "trend_bucket": trend_bucket,
        "trend_title": trend_title,
        "trend_description": trend_description,
        "start_date": start.astimezone(zone).date().isoformat(),
        "end_date": (stop - timedelta(microseconds=1)).astimezone(zone).date().isoformat(),
        "cat_id": cat_id or "",
    }


def dashboard_payload(
    repository, store, start_date: date, end_date: date, cat_id: str | None, timezone_name: str, trend_bucket: str = "day"
) -> dict:
    start, stop = range_from_dates(start_date, end_date, timezone_name)
    return dashboard_payload_for_window(repository, store, start, stop, cat_id, timezone_name, trend_bucket)
