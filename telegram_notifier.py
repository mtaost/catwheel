"""Durable, opt-in Telegram notifications for completed catwheel runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import logging
import os
import threading
from typing import Any, Mapping

import httpx

# Matplotlib otherwise tries to create a cache under the service account's
# unwritable home directory. PrivateTmp makes this cache service-local.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/catwheel-matplotlib")
import matplotlib

# The logger runs without a display server on the Raspberry Pi.
matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from events import NullEventPublisher


TELEGRAM_API_URL = "https://api.telegram.org"
POLL_INTERVAL_SECONDS = 30.0
INITIAL_RETRY_SECONDS = 5.0
MAX_RETRY_SECONDS = 60.0 * 60.0
SPEED_REMARKS = {
    "low": (
        None,
        4.0,
        (
            "A walk in the park",
            "Taking the scenic route",
        ),
    ),
    "high": (
        11.0,
        None,
        (
            "He's got the zoomies!",
            "I AM SPEED",
            "Gotta go fast",
            "That was fast! Definitely not Miso",
            "Nyooooom"
        ),
    ),
}
DURATION_REMARKS = {
    "low": (
        None,
        10.0,
        (
            "A quick wheel break!",
            "Keeping things light today",
        ),
    ),
    "high": (
        60.0,
        None,
        (
            "He's going the distance",
            "A marathon session on the wheel!",
            "Endurance champion!",
            "Ready for the Boston Marathon!",
        ),
    ),
}
def banded_remark(
    payload: Mapping[str, Any],
    value: float,
    bands: Mapping[str, tuple[float | None, float | None, tuple[str, ...]]],
    label: str,
) -> str | None:
    """Choose a stable message from a matching band, or silence in a dead band."""
    for band, (lower_bound, upper_bound, messages) in bands.items():
        if (lower_bound is None or value >= lower_bound) and (
            upper_bound is None or value <= upper_bound
        ):
            run_id = str(payload.get("run_id", ""))
            choice = hashlib.sha256(f"{label}:{band}:{run_id}".encode()).digest()[0] % len(messages)
            return messages[choice]
    return None


def performance_remarks(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Add one speed and duration band message plus optional record flavor."""
    max_speed = float(payload["max_speed"])
    duration = float(payload["run_duration"])
    band_remarks = (
        banded_remark(payload, max_speed, SPEED_REMARKS, "speed"),
        banded_remark(payload, duration, DURATION_REMARKS, "duration"),
    )
    remarks = [remark for remark in band_remarks if remark]
    record_breakers = set(payload.get("record_breakers", []))
    if "max_speed" in record_breakers:
        remarks.append("🏆 New speed record!")
    if "run_duration" in record_breakers:
        remarks.append("⏱️ New duration record!")
    return tuple(remarks)


def caption_for_run(payload: Mapping[str, Any]) -> str:
    """Build the concise caption sent alongside a completed-run speed graph."""
    return "\n".join(
        (
            "** Run complete **",
            f"Max speed: {float(payload['max_speed']):.1f} mph",
            f"Avg speed: {float(payload['avg_speed']):.1f} mph",
            f"Distance:  {float(payload['distance_travelled']):.0f} ft",
            f"Duration:  {float(payload['run_duration']):.0f} s",
            *performance_remarks(payload),
        )
    )


def render_speed_graph(payload: Mapping[str, Any]) -> bytes:
    """Return a 1200px-wide PNG speed trace without creating a temporary file."""
    samples = list(payload.get("speed_samples", []))
    figure = Figure(figsize=(12, 6), dpi=100, facecolor="#20242b")
    axis = figure.add_subplot(1, 1, 1)
    axis.set_facecolor("#282d35")
    axis.set_title("Catwheel speed", color="#f8f8f2", pad=14, fontsize=16)
    axis.set_xlabel("Elapsed time (seconds)", color="#f8f8f2")
    axis.set_ylabel("Speed (mph)", color="#f8f8f2")
    axis.tick_params(colors="#d7d7d7")
    for spine in axis.spines.values():
        spine.set_color("#697386")
    axis.grid(color="#697386", alpha=0.35, linewidth=0.8)

    valid_samples = []
    for sample in samples:
        try:
            valid_samples.append((float(sample["timestamp"]), float(sample["speed_mph"])))
        except (KeyError, TypeError, ValueError):
            continue
    if valid_samples:
        started_at = valid_samples[0][0]
        elapsed = [timestamp - started_at for timestamp, _speed in valid_samples]
        speeds = [speed for _timestamp, speed in valid_samples]
        axis.plot(
            elapsed,
            speeds,
            color="#ffb86c",
            linewidth=2.5,
            marker="o",
            markersize=4,
        )
        axis.set_ylim(bottom=0)
    else:
        axis.text(
            0.5,
            0.5,
            "No accepted speed samples",
            color="#f8f8f2",
            horizontalalignment="center",
            verticalalignment="center",
            transform=axis.transAxes,
        )
        axis.set_ylim(0, 1)
        axis.set_xlim(0, 1)

    figure.tight_layout()
    output = BytesIO()
    FigureCanvasAgg(figure).print_png(output)
    return output.getvalue()


class TelegramEventPublisher:
    """Queue and asynchronously deliver completed-run photos to Telegram.

    The SQLite outbox is written on the logger thread, while graph rendering
    and network I/O run in a worker. A send is acknowledged only after a
    successful Bot API response, making retries intentionally at-least-once.
    """

    def __init__(
        self,
        store,
        bot_token: str,
        chat_id: str,
        logger: logging.Logger | None = None,
        *,
        http_client: httpx.Client | None = None,
        start_worker: bool = True,
    ):
        if not bot_token or not chat_id:
            raise ValueError("Telegram bot token and chat ID are both required")
        self.store = store
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.log = logger or logging.getLogger(__name__)
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(15.0))
        self._owns_http_client = http_client is None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._run_worker,
                name="catwheel-telegram-notifier",
                daemon=True,
            )
            self._worker.start()

    def live_speed(self, payload: dict) -> None:
        """Telegram only publishes final, qualifying runs in version one."""
        return None

    def run_completed(self, payload: dict) -> None:
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Completed-run event is missing run_id")
        self.store.enqueue_telegram_notification(run_id, payload)
        self._wake.set()

    def close(self) -> None:
        """Stop the worker without removing queued notifications."""
        self._stop.set()
        self._wake.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=20.0)
        if self._owns_http_client:
            self._http.close()

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            if self.process_next_notification():
                continue
            self._wake.wait(POLL_INTERVAL_SECONDS)
            self._wake.clear()

    @staticmethod
    def _retry_delay(attempt_count: int, response_data: Any | None = None) -> float:
        parameters = response_data.get("parameters", {}) if isinstance(response_data, dict) else {}
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            return float(retry_after)
        return min(INITIAL_RETRY_SECONDS * (2 ** attempt_count), MAX_RETRY_SECONDS)

    @staticmethod
    def _server_retry_after(response_data: Any | None) -> float | None:
        parameters = response_data.get("parameters", {}) if isinstance(response_data, dict) else {}
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        return float(retry_after) if isinstance(retry_after, (int, float)) and retry_after > 0 else None

    def _send_photo(self, payload: Mapping[str, Any]) -> None:
        response = self._http.post(
            f"{TELEGRAM_API_URL}/bot{self.bot_token}/sendPhoto",
            data={"chat_id": self.chat_id, "caption": caption_for_run(payload)},
            files={"photo": ("catwheel-speed.png", render_speed_graph(payload), "image/png")},
        )
        response_data: Any | None = None
        try:
            response_data = response.json()
        except ValueError:
            pass
        if response.is_success and isinstance(response_data, dict) and response_data.get("ok") is True:
            return
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TelegramDeliveryError(
                f"Telegram responded with HTTP {response.status_code}",
                retry_after=self._server_retry_after(response_data),
            ) from exc
        description = response_data.get("description", "Telegram rejected the notification") if isinstance(response_data, dict) else "Telegram returned an invalid response"
        raise TelegramDeliveryError(
            str(description), retry_after=self._server_retry_after(response_data)
        )

    def process_next_notification(self) -> bool:
        """Attempt one due notification; exposed for deterministic tests."""
        notification = self.store.claim_due_telegram_notification()
        if notification is None:
            return False
        try:
            self._send_photo(notification["payload"])
        except Exception as exc:
            response_delay = exc.retry_after if isinstance(exc, TelegramDeliveryError) else None
            delay = response_delay or self._retry_delay(notification["attempt_count"])
            safe_error = str(exc).replace(self.bot_token, "[redacted]")
            self.store.retry_telegram_notification(
                notification["id"],
                datetime.now(timezone.utc) + timedelta(seconds=delay),
                safe_error,
            )
            self.log.warning(
                "Telegram notification for run %s failed; retrying in %.0f seconds: %s",
                notification["run_id"],
                delay,
                safe_error,
            )
        else:
            self.store.mark_telegram_notification_sent(notification["id"])
            self.log.info("Sent Telegram completion notification for run %s.", notification["run_id"])
        return True


class TelegramDeliveryError(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def publisher_from_environment(store, logger: logging.Logger | None = None):
    """Return the opt-in publisher or a no-op publisher when Telegram is unset."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token and not chat_id:
        return NullEventPublisher()
    if not bot_token or not chat_id:
        missing = "TELEGRAM_BOT_TOKEN" if not bot_token else "TELEGRAM_CHAT_ID"
        raise RuntimeError(f"Telegram notifications require {missing} when enabled")
    return TelegramEventPublisher(store, bot_token, chat_id, logger)
