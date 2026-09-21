from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx

from events import NullEventPublisher
from settings_store import SettingsStore
from telegram_notifier import (
    DURATION_REMARKS,
    SPEED_REMARKS,
    TelegramEventPublisher,
    caption_for_run,
    performance_remarks,
    publisher_from_environment,
    render_speed_graph,
)


PAYLOAD = {
    "topic": "catwheel/v1/run-completed",
    "run_id": "run-123",
    "time_ns": 1002.0,
    "max_speed": 8.5,
    "avg_speed": 4.0,
    "distance_travelled": 120.4,
    "run_duration": 40.0,
    "speed_samples": [
        {"timestamp": 1000.0, "speed_mph": 2.0},
        {"timestamp": 1002.0, "speed_mph": 8.5},
    ],
}


class RecordingClient:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self.json_body = json_body if json_body is not None else {"ok": True}
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return httpx.Response(
            self.status_code,
            json=self.json_body,
            request=httpx.Request("POST", url),
        )


class TelegramNotifierTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "state.db"
        self.store = SettingsStore(self.path)

    def tearDown(self):
        self.tempdir.cleanup()

    def _outbox_row(self):
        with sqlite3.connect(self.path) as connection:
            return connection.execute(
                "SELECT attempt_count, next_attempt_at, sent_at, last_error FROM telegram_notification_outbox"
            ).fetchone()

    def test_caption_and_png_rendering(self):
        caption = caption_for_run(PAYLOAD)
        self.assertIn("🐈 Catwheel run complete", caption)
        self.assertIn("Peak speed: 8.5 mph", caption)
        self.assertEqual(performance_remarks(PAYLOAD), ())
        image = render_speed_graph(PAYLOAD)
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_performance_copy(self):
        low_short = {**PAYLOAD, "max_speed": 3.0, "run_duration": 8.0}
        dead_band = {**PAYLOAD, "max_speed": 6.0, "run_duration": 40.0}
        high_long = {**PAYLOAD, "max_speed": 10.1, "run_duration": 60.1}
        self.assertIn(performance_remarks(low_short)[0], SPEED_REMARKS["low"][2])
        self.assertIn(performance_remarks(low_short)[1], DURATION_REMARKS["low"][2])
        self.assertEqual(performance_remarks(dead_band), ())
        self.assertIn(performance_remarks(high_long)[0], SPEED_REMARKS["high"][2])
        self.assertIn(performance_remarks(high_long)[1], DURATION_REMARKS["high"][2])
        self.assertEqual(
            performance_remarks(high_long)[-1], "Speed demon alert: over 10 mph!"
        )

    def test_new_speed_and_duration_records_are_added_to_the_outbox_payload(self):
        first = {**PAYLOAD, "run_id": "first-run"}
        self.store.enqueue_telegram_notification(first["run_id"], first)
        first_notification = self.store.claim_due_telegram_notification()
        self.assertEqual(first_notification["payload"]["record_breakers"], [])
        self.store.mark_telegram_notification_sent(first_notification["id"])

        record_breaker = {
            **PAYLOAD,
            "run_id": "record-run",
            "max_speed": 10.5,
            "run_duration": 70.0,
        }
        self.store.enqueue_telegram_notification(record_breaker["run_id"], record_breaker)
        notification = self.store.claim_due_telegram_notification()
        self.assertEqual(
            notification["payload"]["record_breakers"], ["max_speed", "run_duration"]
        )
        self.assertEqual(
            performance_remarks(notification["payload"]),
            (
                performance_remarks(record_breaker)[0],
                performance_remarks(record_breaker)[1],
                "🏆 New speed record! The wheel has a new blur.",
                "⏱️ New duration record! That was a marathon session.",
                "Speed demon alert: over 10 mph!",
            ),
        )

    def test_successful_send_marks_durable_notification_sent(self):
        client = RecordingClient()
        publisher = TelegramEventPublisher(
            self.store, "secret-token", "-100123", http_client=client, start_worker=False
        )
        publisher.run_completed(PAYLOAD)
        self.assertTrue(publisher.process_next_notification())

        self.assertEqual(len(client.posts), 1)
        url, request = client.posts[0]
        self.assertTrue(url.endswith("/botsecret-token/sendPhoto"))
        self.assertEqual(request["data"]["chat_id"], "-100123")
        self.assertIn("Peak speed: 8.5 mph", request["data"]["caption"])
        filename, image, content_type = request["files"]["photo"]
        self.assertEqual((filename, content_type), ("catwheel-speed.png", "image/png"))
        self.assertTrue(image.startswith(b"\x89PNG"))
        row = self._outbox_row()
        self.assertIsNotNone(row[2])
        self.assertEqual(row[0], 0)

    def test_retry_honors_telegram_retry_after_and_survives_restart(self):
        failed_client = RecordingClient(429, {"ok": False, "parameters": {"retry_after": 37}})
        publisher = TelegramEventPublisher(
            self.store, "secret-token", "chat", http_client=failed_client, start_worker=False
        )
        publisher.run_completed(PAYLOAD)
        publisher.process_next_notification()
        row = self._outbox_row()
        self.assertEqual(row[0], 1)
        self.assertIsNone(row[2])
        self.assertIn("429", row[3])
        next_attempt = datetime.fromisoformat(row[1])
        self.assertGreaterEqual(next_attempt, datetime.now(timezone.utc) + timedelta(seconds=35))

        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE telegram_notification_outbox SET next_attempt_at = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
            )
        restarted_store = SettingsStore(self.path)
        success_client = RecordingClient()
        restarted = TelegramEventPublisher(
            restarted_store, "secret-token", "chat", http_client=success_client, start_worker=False
        )
        self.assertTrue(restarted.process_next_notification())
        self.assertEqual(len(success_client.posts), 1)
        self.assertIsNotNone(self._outbox_row()[2])

    def test_environment_configuration_is_opt_in_and_requires_both_values(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=False):
            self.assertIsInstance(publisher_from_environment(self.store), NullEventPublisher)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "set", "TELEGRAM_CHAT_ID": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_CHAT_ID"):
                publisher_from_environment(self.store)
        sentinel = object()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "set", "TELEGRAM_CHAT_ID": "chat"}, clear=False):
            with patch("telegram_notifier.TelegramEventPublisher", return_value=sentinel) as constructor:
                self.assertIs(publisher_from_environment(self.store), sentinel)
        constructor.assert_called_once()
