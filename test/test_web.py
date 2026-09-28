from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

import httpx

from settings_store import SettingsStore
from web.app import create_app
from web.config import WebConfig


class FakeRepository:
    def __init__(self):
        self.run = {
            "run_id": "run-1",
            "timestamp": datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            "max_speed_mph": 8.5,
            "avg_speed_mph": 4.0,
            "distance_travelled_ft": 120.0,
            "run_duration_seconds": 40.0,
            "settings_revision": 1,
            "rejected_sample_count": 0,
        }
        self.runs = [self.run]

    def completed_runs(self, start, stop):
        return self.runs

    def speed_samples(self, run_id, start, stop):
        return [{"timestamp": self.run["timestamp"], "speed_mph": 4.0}]


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        config = WebConfig(
            influx_url="http://unused",
            influx_token="unused",
            influx_org="unused",
            influx_bucket="unused",
            state_db=Path(self.tempdir.name) / "state.db",
        )
        self.repository = FakeRepository()
        self.app = create_app(config, self.repository, SettingsStore(config.state_db))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.tempdir.cleanup()

    async def test_dashboard_is_available_without_login(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Wheel health", response.text)

    async def test_dashboard_marks_quick_and_custom_date_ranges(self):
        quick = await self.client.get("/?start=2026-09-01&end=2026-09-07")
        custom = await self.client.get("/?start=2026-09-01&end=2026-09-05")
        rolling = await self.client.get("/?range=12h")
        self.assertIn('data-active-range="7d"', quick.text)
        self.assertIn('data-active-range="custom"', custom.text)
        self.assertIn('data-active-range="12h"', rolling.text)
        self.assertEqual((await self.client.get("/api/v1/dashboard?range=12h")).json()["trend_bucket"], "half_hour")
        hourly = (await self.client.get("/api/v1/dashboard?start=2026-09-01&end=2026-09-01")).json()
        self.assertEqual(hourly["trend_bucket"], "hour")
        self.assertEqual(len(hourly["trend"]), 24)
        self.assertEqual(sum(bucket["distance_ft"] for bucket in hourly["trend"]), 120.0)

    async def test_label_write_is_available_without_login(self):
        saved = await self.client.post(
            "/api/v1/runs/run-1/label",
            json={"cat_id": "Lumi"},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["cat_id"], "Lumi")

    async def test_run_page_places_chart_before_compact_summary(self):
        response = await self.client.get("/runs/run-1")
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="summary-card run-summary"', response.text)
        self.assertLess(response.text.index('id="speed-chart"'), response.text.index('class="summary-card run-summary"'))
        self.assertIn("stepped: 'after'", response.text)
        self.assertIn("Elapsed time (seconds)", response.text)

    async def test_recent_runs_are_compact_and_paginated(self):
        self.repository.runs = [
            {**self.repository.run, "run_id": f"run-{index}", "timestamp": datetime(2026, 9, index, 12, tzinfo=timezone.utc)}
            for index in range(1, 17)
        ]
        response = await self.client.get("/?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="run-list"', response.text)
        self.assertIn('href="/runs/run-1"', response.text)
        self.assertNotIn('href="/runs/run-16"', response.text)
        self.assertIn("Page 2 of 2", response.text)
