import time
from enum import Enum
import uuid

from events import NullEventPublisher
from settings_store import DetectionSettings


class WheelState(Enum):
    SLEEP = "sleep"
    RUNNING = "running"


class CatwheelLogger:
    """Collect Hall-sensor events and persist qualifying wheel runs."""

    SLEEP_POLL_FREQUENCY = 1  # Hz
    RUN_POLL_FREQUENCY = 1  # Hz
    NUM_MAGNETS = 16
    WHEEL_CIRCUMFERENCE = 1.08 * 3.141592  # meters
    MPS_TO_MPH = 2.23694
    M_TO_FT = 3.28084

    def __init__(self, dao, sensor, logger, settings_store=None, event_publisher=None):
        self.run_id: str = uuid.uuid4().hex
        self.state = WheelState.SLEEP
        self.dao = dao
        self.sensor = sensor
        self.sensor.when_activated = self._sensor_interrupt
        self.log = logger
        self.settings_store = settings_store
        self.event_publisher = event_publisher or NullEventPublisher()
        self.settings = self._load_settings()
        self.WHEEL_SEGMENT_LENGTH = self.WHEEL_CIRCUMFERENCE / self.NUM_MAGNETS
        self._running = True
        self._initialize_measurement_vars()

    def _load_settings(self) -> DetectionSettings:
        return self.settings_store.get_settings() if self.settings_store else DetectionSettings()

    def _initialize_measurement_vars(self):
        self.max_speed_mps = 0.0
        self.total_speed = 0.0
        self.distance_travelled_m = 0.0
        self.poll_count = 0
        self.idle_cycles = 0
        self.avg_speed_mps = 0.0
        self.last_interrupt_time = None
        self.active = False
        self.logged_wait = False
        self.previous_speed = 0.0
        self.rejected_sample_count = 0

    def _sensor_interrupt(self):
        current_time = time.time()
        # Snapshot settings at the first magnet pass. They remain immutable for
        # this run, even if an admin saves a newer revision while it is active.
        if self.last_interrupt_time is None:
            self.settings = self._load_settings()
        else:
            elapsed_time = current_time - self.last_interrupt_time
            if elapsed_time <= 0:
                self.log.warning("Ignoring non-positive Hall sensor interval.")
            else:
                raw_speed_mps = self.WHEEL_SEGMENT_LENGTH / elapsed_time
                raw_speed_mph = raw_speed_mps * self.MPS_TO_MPH
                if raw_speed_mph > self.settings.max_valid_speed_mph:
                    self.rejected_sample_count += 1
                    self.log.warning(
                        "Ignoring invalid speed sample %.2f mph (limit %.2f mph).",
                        raw_speed_mph,
                        self.settings.max_valid_speed_mph,
                    )
                else:
                    speed_mps = raw_speed_mps
                    if (
                        self.previous_speed > speed_mps
                        and (self.previous_speed - speed_mps) * self.MPS_TO_MPH
                        > self.settings.drop_correction_mph
                    ):
                        speed_mps = self.previous_speed
                    speed_mph = speed_mps * self.MPS_TO_MPH
                    self.max_speed_mps = max(self.max_speed_mps, speed_mps)
                    self.total_speed += speed_mps
                    self.distance_travelled_m += self.WHEEL_SEGMENT_LENGTH
                    self.poll_count += 1
                    self.avg_speed_mps = self.total_speed / self.poll_count
                    self.dao.write_run_data(self.run_id, current_time, speed_mph)
                    self.event_publisher.live_speed(
                        {
                            "topic": "catwheel/v1/live-speed",
                            "run_id": self.run_id,
                            "timestamp": current_time,
                            "speed_mph": speed_mph,
                            "settings_revision": self.settings.revision,
                        }
                    )
                    self.previous_speed = speed_mps
                    self.log.info(
                        "Speed: %.2f m/s, Max Speed: %.2f m/s, Avg Speed: %.2f m/s, Distance: %.2f m",
                        speed_mps,
                        self.max_speed_mps,
                        self.avg_speed_mps,
                        self.distance_travelled_m,
                    )
        self.last_interrupt_time = current_time
        self.active = True

    def stop(self):
        """Request a clean exit from the logger loop."""
        self._running = False

    def _finish_current_run(self):
        run_duration_s = round(self.stop_time - self.start_time, 2)
        settings = self.settings
        self.log.warning("Idle timeout reached, ending run...")
        if (
            run_duration_s >= settings.min_duration_seconds
            and self.max_speed_mps * self.MPS_TO_MPH >= settings.min_peak_speed_mph
            and self.distance_travelled_m * self.M_TO_FT >= settings.min_distance_ft
        ):
            metadata = {
                "run_id": self.run_id,
                "time_ns": self.stop_time,
                "max_speed": self.max_speed_mps * self.MPS_TO_MPH,
                "avg_speed": self.avg_speed_mps * self.MPS_TO_MPH,
                "distance_travelled": self.distance_travelled_m * self.M_TO_FT,
                "run_duration": run_duration_s,
                "settings_revision": settings.revision,
                "rejected_sample_count": self.rejected_sample_count,
            }
            self.dao.write_run_metadata(**metadata)
            self.event_publisher.run_completed(
                {"topic": "catwheel/v1/run-completed", **metadata}
            )
        else:
            self.log.warning(
                "Run %s of %.2f seconds below threshold, deleting run data...",
                self.run_id,
                run_duration_s,
            )
            self.dao.delete_run_data(self.run_id)
        self._initialize_measurement_vars()
        self.run_id = uuid.uuid4().hex
        self.state = WheelState.SLEEP

    def run(self):
        try:
            while self._running:
                if self.state == WheelState.SLEEP:
                    time.sleep(1 / self.SLEEP_POLL_FREQUENCY)
                    if not self.logged_wait:
                        self.log.warning("Waiting for activity...")
                        self.logged_wait = True
                    if self.poll_count > 0:
                        self.state = WheelState.RUNNING
                        self.logged_wait = False
                        self.start_time = time.time()
                        self.log.warning("Movement detected, starting run...")
                elif self.state == WheelState.RUNNING:
                    time.sleep(1 / self.RUN_POLL_FREQUENCY)
                    if not self.active:
                        self.idle_cycles += 1
                        if self.idle_cycles <= 2:
                            self.stop_time = time.time()
                    else:
                        self.idle_cycles = 0
                    self.active = False
                    if self.idle_cycles >= settings_idle_cycles(self.settings, self.RUN_POLL_FREQUENCY):
                        self._finish_current_run()
        except Exception:
            self.log.exception("Encountered an error; exiting.")
            raise
        finally:
            self.log.info("Cleaning up GPIO and database resources.")
            self.sensor.close()
            self.dao.close()


def settings_idle_cycles(settings: DetectionSettings, frequency: float) -> int:
    return max(1, round(settings.idle_timeout_seconds * frequency))
