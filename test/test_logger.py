import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catwheellogger import CatwheelLogger
from settings_store import SettingsStore


class FakeSensor:
    when_activated = None

    def close(self):
        pass


class FakeDAO:
    def __init__(self):
        self.samples = []
        self.metadata = []
        self.deleted = []

    def write_run_data(self, *values):
        self.samples.append(values)

    def write_run_metadata(self, **values):
        self.metadata.append(values)

    def delete_run_data(self, run_id):
        self.deleted.append(run_id)

    def close(self):
        pass


class FakeLog:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class FakePublisher:
    def __init__(self):
        self.completed = []
        self.live = []
        self.closed = False

    def live_speed(self, payload):
        self.live.append(payload)

    def run_completed(self, payload):
        self.completed.append(payload)

    def close(self):
        self.closed = True


class LoggerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SettingsStore(Path(self.tempdir.name) / "state.db")
        self.dao = FakeDAO()
        self.publisher = FakePublisher()
        self.logger = CatwheelLogger(
            self.dao, FakeSensor(), FakeLog(), self.store, self.publisher
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_invalid_spike_does_not_affect_stats_or_raw_trace(self):
        with patch("catwheellogger.time.time", side_effect=[1.0, 1.01, 2.0]):
            self.logger._sensor_interrupt()  # starts a settings snapshot
            self.logger._sensor_interrupt()  # impossible speed spike
            self.logger._sensor_interrupt()  # accepted speed
        self.assertEqual(self.logger.rejected_sample_count, 1)
        self.assertEqual(len(self.dao.samples), 1)
        self.assertEqual(self.logger.poll_count, 1)
        self.assertEqual(len(self.logger.speed_samples), 1)
        self.assertEqual(self.logger.speed_samples[0]["timestamp"], 2.0)

    def test_completed_run_records_a_prompt_zero_speed_endpoint(self):
        run_id = self.logger.run_id
        self.logger.start_time = 10.0
        self.logger.stop_time = 30.0
        self.logger.max_speed_mps = 2.0
        self.logger.avg_speed_mps = 1.0
        self.logger.distance_travelled_m = 10.0
        self.logger.rejected_sample_count = 2
        self.logger._finish_current_run()
        metadata = self.dao.metadata[0]
        self.assertEqual(metadata["settings_revision"], 1)
        self.assertEqual(metadata["rejected_sample_count"], 2)
        self.assertEqual(self.dao.samples, [(run_id, 30.0, 0.0)])
        self.assertEqual(len(self.publisher.completed), 1)
        self.assertEqual(
            self.publisher.completed[0]["speed_samples"],
            [{"timestamp": 30.0, "speed_mph": 0.0}],
        )

    def test_discarded_run_does_not_publish_completion(self):
        self.logger.start_time = 10.0
        self.logger.stop_time = 11.0
        self.logger._finish_current_run()
        self.assertEqual(self.publisher.completed, [])
