"""Build and durably schedule Telegram summaries of completed wheel runs."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
import logging
from typing import Any
from zoneinfo import ZoneInfo

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


SUMMARY_HOUR = 0
SUMMARY_MINUTE = 5


def summary_window(summary_date: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(summary_date, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def build_daily_summary(runs: list[dict[str, Any]], summary_date: date, zone: ZoneInfo) -> dict[str, Any]:
    histogram = [0] * 24
    for run in runs:
        histogram[run["timestamp"].astimezone(zone).hour] += 1
    return {
        "topic": "catwheel/v1/daily-summary",
        "summary_date": summary_date.isoformat(),
        "run_count": len(runs),
        "total_distance_ft": sum(run["distance_travelled_ft"] for run in runs),
        "max_speed_mph": max((run["max_speed_mph"] for run in runs), default=0.0),
        "hourly_run_counts": histogram,
    }


def daily_summary_caption(payload: dict[str, Any]) -> str:
    return "\n".join(
        (
            f"** Daily summary — {payload['summary_date']} **",
            f"Runs: {int(payload['run_count'])}",
            f"Total distance: {float(payload['total_distance_ft']):.0f} ft",
            f"Max speed: {float(payload['max_speed_mph']):.1f} mph",
        )
    )


def render_daily_histogram(payload: dict[str, Any]) -> bytes:
    counts = [int(value) for value in payload["hourly_run_counts"]]
    figure = Figure(figsize=(12, 6), dpi=100, facecolor="#20242b")
    axis = figure.add_subplot(1, 1, 1)
    axis.set_facecolor("#282d35")
    axis.bar(range(24), counts, color="#ffb86c", width=0.8)
    axis.set_title("Wheel activity by time of day", color="#f8f8f2", pad=14, fontsize=16)
    axis.set_xlabel("Local hour", color="#f8f8f2")
    axis.set_ylabel("Completed runs", color="#f8f8f2")
    axis.set_xticks(range(0, 24, 2), [f"{hour:02d}:00" for hour in range(0, 24, 2)])
    axis.set_ylim(bottom=0)
    axis.tick_params(colors="#d7d7d7")
    for spine in axis.spines.values():
        spine.set_color("#697386")
    axis.grid(axis="y", color="#697386", alpha=0.35, linewidth=0.8)
    figure.tight_layout()
    output = BytesIO()
    FigureCanvasAgg(figure).print_png(output)
    return output.getvalue()


class DailySummaryScheduler:
    def __init__(self, store, repository, timezone_name: str, logger: logging.Logger | None = None):
        self.store = store
        self.repository = repository
        self.zone = ZoneInfo(timezone_name)
        self.log = logger or logging.getLogger(__name__)

    def process_due_summary(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(self.zone)
        local_now = now.astimezone(self.zone)
        trigger = datetime.combine(local_now.date(), time(SUMMARY_HOUR, SUMMARY_MINUTE), tzinfo=self.zone)
        if local_now >= trigger:
            self.store.ensure_daily_summary_job((local_now.date() - timedelta(days=1)).isoformat())
        job = self.store.claim_due_daily_summary_job()
        if job is None:
            return False
        try:
            summary_date = date.fromisoformat(job["summary_date"])
            start, stop = summary_window(summary_date, self.zone)
            payload = build_daily_summary(self.repository.completed_runs(start, stop), summary_date, self.zone)
            self.store.enqueue_telegram_payload(f"daily-summary:{job['summary_date']}", payload)
            self.store.complete_daily_summary_job(job["summary_date"])
        except Exception as exc:
            delay = min(5 * (2 ** job["attempt_count"]), 3600)
            self.store.retry_daily_summary_job(
                job["summary_date"], datetime.now(timezone.utc) + timedelta(seconds=delay), str(exc)
            )
            self.log.warning("Daily summary for %s failed; retrying in %.0f seconds: %s", job["summary_date"], delay, exc)
        return True
