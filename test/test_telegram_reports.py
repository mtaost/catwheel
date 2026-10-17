from __future__ import annotations

from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from telegram_reports import (
    ReportCommandError,
    histogram_counts,
    parse_report_command,
    render_activity_heatmap,
    render_histogram,
    report_window,
)


class TelegramReportTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("America/New_York")
        self.now = datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc)
        self.runs = [
            {"timestamp": datetime(2026, 10, 16, 23, 5, tzinfo=timezone.utc)},
            {"timestamp": datetime(2026, 10, 17, 2, 15, tzinfo=timezone.utc)},
            {"timestamp": datetime(2026, 10, 17, 13, 10, tzinfo=timezone.utc)},
        ]

    def test_histogram_request_has_complete_local_buckets(self):
        request = parse_report_command("/hist 24h")
        self.assertEqual((request.kind, request.count, request.unit), ("hist", 24, "hr"))
        start, stop = report_window(request, self.now, self.zone)
        counts = histogram_counts(self.runs, start, stop, request.unit, self.zone)
        self.assertEqual(len(counts), 24)
        self.assertEqual(sum(count for _timestamp, count in counts), 3)
        self.assertTrue(render_histogram(counts, request.unit, "Test").startswith(b"\x89PNG\r\n\x1a\n"))

    def test_heatmap_rejects_hourly_windows_and_renders_days(self):
        with self.assertRaises(ReportCommandError):
            parse_report_command("/heatmap 24h")
        request = parse_report_command("/heatmap 7d")
        start, stop = report_window(request, self.now, self.zone)
        self.assertTrue(render_activity_heatmap(self.runs, start, stop, self.zone).startswith(b"\x89PNG\r\n\x1a\n"))
