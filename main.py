from catwheellogger import CatwheelLogger
from dao.influxdb_dao import InfluxDBDAO
import gpiozero
import logging
import os
from logging_spinner import SpinnerHandler

SENSOR_PIN_BCM = 17

# database setup
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN")
BUCKET = os.environ.get("BUCKET")
ORG = os.environ.get("ORG")
DATABASE_URL = os.environ.get("DATABASE_URL")

def main():
    sensor = gpiozero.DigitalInputDevice(
        pin=SENSOR_PIN_BCM,
        pull_up=True,
    )

    dao = InfluxDBDAO(url=DATABASE_URL, token=INFLUXDB_TOKEN, org=ORG, bucket=BUCKET)
    
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.addHandler(SpinnerHandler())

    catwheel_logger = CatwheelLogger(dao, sensor, logger)
    catwheel_logger.run()

if __name__ == "__main__":
    main()

   