import gpiozero
import time
import logging
import sys

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
SENSOR_PIN_BCM = 17
def sensor_interrupt():
    logging.info("boop")
sensor = gpiozero.DigitalInputDevice(
    pin=SENSOR_PIN_BCM,
    pull_up=True,
    active_state=None,
)
sensor.when_activated=sensor_interrupt

try:
    while True:
        time.sleep(1)
        logging.warning("waiting")

except:
    logging.exception("exiting")
    sensor.close()
    exit(0)