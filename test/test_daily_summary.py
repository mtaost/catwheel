from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from daily_summary import (
    DailySummaryScheduler,
    build_daily_summary,
    daily_summary_caption,
    render_daily_histogram,
)
from settings_store import SettingsStore


class FakeRepository:
    def __init__(self, runs):
        self.runs = runs
        self.windows = []

    def completed_runs(self, start, stop):
        self.windows.append((start, stop))
        return self.runs


class DailySummaryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SettingsStore(Path(self.tempdir.name) / "state.db")
        self.zone = ZoneInfo("America/New_York")
        self.runs = [
            {
                "timestamp": datetime(2026, 9, 20, 5, 15, tzinfo=timezone.utc),
                "distance_travelled_ft": 120.0,
                "max_speed_mph": 8.5,
            },
            {
                "timestamp": datetime(2026, 9, 20, 23, 45, tzinfo=timezone.utc),
                "distance_travelled_ft": 80.0,
                "max_speed_mph": 11.0,
            },
        ]

    def tearDown(self):
        self.tempdir.cleanup()

    def test_summary_totals_and_histogram_are_local_time(self):
        payload = build_daily_summary(self.runs, datetime(2026, 9, 20).date(), self.zone)
        self.assertEqual(payload["run_count"], 2)
        self.assertEqual(payload["total_distance_ft"], 200.0)
        self.assertEqual(payload["max_speed_mph"], 11.0)
        self.assertEqual(payload["hourly_run_counts"][1], 1)
        self.assertEqual(payload["hourly_run_counts"][19], 1)
        self.assertIn("Runs: 2", daily_summary_caption(payload))
        self.assertTrue(render_daily_histogram(payload).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_scheduler_queues_prior_day_at_1205(self):
        repository = FakeRepository(self.runs)
        scheduler = DailySummaryScheduler(self.store, repository, "America/New_York")
        self.assertTrue(
            scheduler.process_due_summary(datetime(2026, 9, 21, 0, 5, tzinfo=self.zone))
        )
        notification = self.store.claim_due_telegram_notification()
        self.assertEqual(notification["run_id"], "daily-summary:2026-09-20")
        self.assertEqual(notification["payload"]["topic"], "catwheel/v1/daily-summary")
        self.assertEqual(len(repository.windows), 1)
        self.assertFalse(
            scheduler.process_due_summary(datetime(2026, 9, 21, 0, 6, tzinfo=self.zone))
        )
