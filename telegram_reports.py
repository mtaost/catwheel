"""Pure formatting and chart helpers for Telegram's historical reports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from io import BytesIO
import re
from zoneinfo import ZoneInfo

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


@dataclass(frozen=True)
class ReportRequest:
    kind: str
    count: int
    unit: str


class ReportCommandError(ValueError):
    """The command is valid Telegram text but not a supported report request."""


HELP_TEXT = "\n".join(
    (
        "Catwheel reports",
        "/weekly — last 7 days of run counts",
        "/hist <n><h|d|mo> — run-count histogram (for example: /hist 24h)",
        "/heatmap <n><d|mo> — activity by date and hour (for example: /heatmap 30d)",
    )
)


def parse_report_command(text: str) -> ReportRequest | None:
    """Parse one supported bot command, ignoring a Telegram @bot suffix."""
    tokens = text.strip().split()
    if not tokens:
        return None
    command = tokens[0].split("@", 1)[0].lower()
    if command in {"/start", "/help"}:
        if len(tokens) != 1:
            raise ReportCommandError("Use /help without additional arguments.")
        return ReportRequest("help", 0, "")
    if command == "/weekly":
        if len(tokens) != 1:
            raise ReportCommandError("Use /weekly without additional arguments.")
        return ReportRequest("hist", 7, "day")
    if command not in {"/hist", "/heatmap"}:
        return None
    if len(tokens) == 2:
        match = re.fullmatch(r"(\d+)(h|d|mo)", tokens[1].lower())
        if not match:
            raise ReportCommandError(f"Usage: {command} <n><h|d|mo>")
        count = int(match.group(1))
        unit = {"h": "hr", "d": "day", "mo": "mo"}[match.group(2)]
    elif len(tokens) == 3:
        try:
            count = int(tokens[1])
        except ValueError as exc:
            raise ReportCommandError("n must be a whole number.") from exc
        unit = {"hr": "hr", "day": "day", "mo": "mo"}.get(tokens[2].lower())
        if unit is None:
            raise ReportCommandError("Unit must be hr, day, or mo.")
    else:
        raise ReportCommandError(f"Usage: {command} <n><h|d|mo>")
    limits = {"hr": 168, "day": 365, "mo": 24}
    if not 1 <= count <= limits[unit]:
        raise ReportCommandError(f"n must be between 1 and {limits[unit]} for {unit}.")
    if command == "/heatmap" and unit == "hr":
        raise ReportCommandError("Heat maps use day or mo so the vertical axis can be hour of day.")
    return ReportRequest(command.removeprefix("/"), count, unit)


def report_window(request: ReportRequest, now: datetime, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Return a local-time window aligned to the selected reporting buckets."""
    local_now = now.astimezone(zone)
    if request.unit == "hr":
        stop = local_now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return stop - timedelta(hours=request.count), stop
    if request.unit == "day":
        stop = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=zone)
        return stop - timedelta(days=request.count), stop
    stop = datetime(local_now.year, local_now.month, 1, tzinfo=zone)
    if local_now.day != 1 or local_now.time() != time.min:
        stop = _add_months(stop, 1)
    return _add_months(stop, -request.count), stop


def _add_months(value: datetime, count: int) -> datetime:
    month = value.month - 1 + count
    return datetime(value.year + month // 12, month % 12 + 1, 1, tzinfo=value.tzinfo)


def bucket_start(timestamp: datetime, unit: str, zone: ZoneInfo) -> datetime:
    local = timestamp.astimezone(zone)
    if unit == "hr":
        return local.replace(minute=0, second=0, microsecond=0)
    if unit == "day":
        return datetime.combine(local.date(), time.min, tzinfo=zone)
    return datetime(local.year, local.month, 1, tzinfo=zone)


def next_bucket(value: datetime, unit: str) -> datetime:
    if unit == "hr":
        return value + timedelta(hours=1)
    if unit == "day":
        return value + timedelta(days=1)
    return _add_months(value, 1)


def bucket_label(value: datetime, unit: str) -> str:
    if unit == "hr":
        return value.strftime("%-I %p")
    if unit == "day":
        return value.strftime("%b %-d")
    return value.strftime("%b\n%Y")


def histogram_counts(runs: list[dict], start: datetime, stop: datetime, unit: str, zone: ZoneInfo) -> list[tuple[datetime, int]]:
    counts: dict[datetime, int] = defaultdict(int)
    for run in runs:
        timestamp = run["timestamp"]
        if start <= timestamp < stop:
            counts[bucket_start(timestamp, unit, zone)] += 1
    result = []
    cursor = start
    while cursor < stop:
        result.append((cursor, counts[cursor]))
        cursor = next_bucket(cursor, unit)
    return result


def report_caption(title: str, runs: list[dict], start: datetime, stop: datetime, zone: ZoneInfo) -> str:
    local_start = start.astimezone(zone).strftime("%b %-d")
    local_end = (stop - timedelta(microseconds=1)).astimezone(zone).strftime("%b %-d")
    total_distance = sum(float(run.get("distance_travelled_ft", 0)) for run in runs)
    active_seconds = sum(float(run.get("run_duration_seconds", 0)) for run in runs)
    return "\n".join(
        (
            title,
            f"{local_start}–{local_end}",
            f"Runs: {len(runs)}  •  Distance: {total_distance:,.0f} ft  •  Active: {active_seconds / 60:.1f} min",
        )
    )


def _dark_figure(width: float, height: float) -> tuple[Figure, object]:
    figure = Figure(figsize=(width, height), dpi=100, facecolor="#20242b")
    axis = figure.add_subplot(1, 1, 1)
    axis.set_facecolor("#282d35")
    axis.tick_params(colors="#d7d7d7")
    for spine in axis.spines.values():
        spine.set_color("#697386")
    return figure, axis


def _png(figure: Figure) -> bytes:
    figure.tight_layout()
    output = BytesIO()
    FigureCanvasAgg(figure).print_png(output)
    return output.getvalue()


def render_histogram(counts: list[tuple[datetime, int]], unit: str, title: str) -> bytes:
    """Render a compact run-count histogram without writing a temporary file."""
    figure, axis = _dark_figure(max(8, min(14, len(counts) * 0.45)), 5)
    labels = [bucket_label(timestamp, unit) for timestamp, _count in counts]
    values = [count for _timestamp, count in counts]
    positions = list(range(len(counts)))
    axis.bar(positions, values, color="#ffb86c", width=0.78)
    axis.set_title(title, color="#f8f8f2", pad=14, fontsize=16)
    axis.set_ylabel("Completed runs", color="#f8f8f2")
    axis.set_ylim(bottom=0)
    axis.grid(axis="y", color="#697386", alpha=0.35, linewidth=0.8)
    axis.set_axisbelow(True)
    stride = max(1, len(labels) // 12)
    axis.set_xticks(positions[::stride], labels[::stride], rotation=45, ha="right")
    for position, value in zip(positions, values):
        if value:
            axis.text(position, value, str(value), color="#f8f8f2", ha="center", va="bottom", fontsize=9)
    return _png(figure)


def render_activity_heatmap(runs: list[dict], start: datetime, stop: datetime, zone: ZoneInfo) -> bytes:
    """Render date × local-hour run counts; each square represents one bucket."""
    dates = []
    cursor = start
    while cursor < stop:
        dates.append(cursor.date())
        cursor += timedelta(days=1)
    counts: dict[tuple[object, int], int] = defaultdict(int)
    for run in runs:
        local = run["timestamp"].astimezone(zone)
        if start <= local < stop:
            counts[(local.date(), local.hour)] += 1
    values = [[counts[(day, hour)] for day in dates] for hour in range(24)]
    figure, axis = _dark_figure(max(8, min(16, len(dates) * 0.33)), 7)
    image = axis.imshow(values, aspect="auto", interpolation="nearest", cmap="YlOrRd", origin="upper")
    axis.set_title("Catwheel activity by local hour", color="#f8f8f2", pad=14, fontsize=16)
    axis.set_xlabel("Date", color="#f8f8f2")
    axis.set_ylabel("Hour of day", color="#f8f8f2")
    axis.set_yticks(range(0, 24, 2), [f"{hour:02}:00" for hour in range(0, 24, 2)])
    stride = max(1, len(dates) // 10)
    axis.set_xticks(range(0, len(dates), stride), [day.strftime("%b %-d") for day in dates[::stride]], rotation=45, ha="right")
    colorbar = figure.colorbar(image, ax=axis, pad=0.02)
    colorbar.set_label("Completed runs", color="#f8f8f2")
    colorbar.ax.tick_params(colors="#d7d7d7")
    return _png(figure)
