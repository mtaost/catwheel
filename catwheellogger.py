import time
from enum import Enum
from dao.influxdb_dao import InfluxDBDAO
import threading
import uuid
import gpiozero

class WheelState(Enum):
    SLEEP = "sleep"
    RUNNING = "running"

class CatwheelLogger:
    # Constants
    SLEEP_POLL_FREQUENCY = 1  # Hz
    RUN_POLL_FREQUENCY = 1  # Hz
    IDLE_TIMEOUT = 45  # seconds
    MIN_DURATION_KEEP = 5 # seconds
    MIN_SPEED_KEEP = 1.5 * 0.44 # m/s
    MIN_DISTANCE_KEEP = 3.14 # meters
    MAX_SPEED_MPH = 16 # mph, do not log if speed is over this value, consider it a sensor error

    NUM_MAGNETS = 16 # Number of magnets on the wheel
    WHEEL_CIRCUMFERENCE = 1.08 * 3.141592 # meters


    MPS_TO_MPH = 2.23694 # conversion factor from m/s to mph
    M_TO_YDS = 1.09361 # conversion factor from meters to yards
    M_TO_FT = 3.28084 # conversion factor from meters to feet
    

    def __init__(self, dao, sensor, logger):
        self.run_id: str = uuid.uuid4().hex
        self.state = WheelState.SLEEP
        self.dao = dao
        self.sensor = sensor
        self.sensor.when_activated = self._sensor_interrupt
        self.log = logger
        self.WHEEL_SEGMENT_LENGTH = self.WHEEL_CIRCUMFERENCE / self.NUM_MAGNETS
        self._initialize_measurement_vars()

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

    def _sensor_interrupt(self):
        current_time = time.time()
        if self.last_interrupt_time is not None:
            elapsed_time = current_time - self.last_interrupt_time
            speed = self.WHEEL_SEGMENT_LENGTH / elapsed_time
            self.max_speed_mps = max(self.max_speed_mps, speed)
            self.total_speed += speed
            self.distance_travelled_m += self.WHEEL_SEGMENT_LENGTH
            self.poll_count += 1
            self.avg_speed_mps = self.total_speed / self.poll_count if self.poll_count > 0 else 0
            self.log.info(f"Speed: {speed:.2f} m/s, Max Speed: {self.max_speed_mps:.2f} m/s, Avg Speed: {self.avg_speed_mps:.2f} m/s, Distance: {self.distance_travelled_m:.2f} m")
            if (speed * self.MPS_TO_MPH < self.MAX_SPEED_MPH):
              # If the drop in speed from last measurement is > 4mph, assume we missed the last sensor reading and just log previous speed
              if self.previous_speed > speed and  (self.previous_speed - speed) * self.MPS_TO_MPH > 4:
                  speed = self.previous_speed
              self.dao.write_run_data(self.run_id, current_time, speed * self.MPS_TO_MPH)
              self.previous_speed = speed
        self.last_interrupt_time = current_time
        self.active = True

    def run(self):
        try:
            while True:
                if self.state == WheelState.SLEEP:
                    time.sleep(int(1 / self.SLEEP_POLL_FREQUENCY))
                    if not self.logged_wait:
                        self.log.warn("Waiting for cactivity...")#, extra={'user_waiting': True})
                        self.logged_wait = True

                    if self.poll_count > 0:
                        self.state = WheelState.RUNNING
                        self.logged_wait = False
                        self.start_time = time.time()
                        self.log.warn("Movement detected, starting run...")#, extra={'user_waiting': False})

                elif self.state == WheelState.RUNNING:
                    time.sleep(1 / self.RUN_POLL_FREQUENCY)

                    if not self.active:
                        self.idle_cycles += 1
                        if self.idle_cycles <= 2:
                            self.stop_time = time.time()
                    else:
                        self.idle_cycles = 0

                    self.active = False

                    if self.idle_cycles >= self.IDLE_TIMEOUT * self.RUN_POLL_FREQUENCY:
                        run_duration_s = round(self.stop_time - self.start_time, 2)
                        self.log.warn("Idle timeout reached, ending run...")
                        self.log.info(f"Max Speed: {self.max_speed_mps:.2f} m/s, Avg Speed: {self.avg_speed_mps:.2f} m/s, Distance: {self.distance_travelled_m:.2f} m, Duration: {run_duration_s} seconds")
                        # Only keep runs longer than thresholds
                        if run_duration_s >= self.MIN_DURATION_KEEP and self.max_speed_mps >= self.MIN_SPEED_KEEP and self.distance_travelled_m >= self.MIN_DISTANCE_KEEP:
                            self.dao.write_run_metadata(
                                run_id=self.run_id,
                                time_ns=self.stop_time,
                                max_speed=self.max_speed_mps * self.MPS_TO_MPH, 
                                avg_speed=self.avg_speed_mps * self.MPS_TO_MPH, 
                                distance_travelled=self.distance_travelled_m * self.M_TO_FT, 
                                run_duration=run_duration_s,
                            )
                        else:
                            self.log.warn(f"Run {self.run_id} of {run_duration_s} seconds below threshold, deleting run data...")
                            self.dao.delete_run_data(self.run_id)
                        # Set up for next run 
                        self._initialize_measurement_vars()
                        self.run_id = uuid.uuid4().hex

                        self.state = WheelState.SLEEP
        except Exception as e:
            self.log.exception("Encountered {e} Exiting...")
            self.log.exception("Cleaning up gpios")
            self.sensor.close()