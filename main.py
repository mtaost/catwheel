from catwheellogger import CatwheelLogger
from dao.influxdb_dao import InfluxDBDAO
import gpiozero
import logging
import os
import signal

SENSOR_PIN_BCM = 17

# database setup
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN")
BUCKET = os.environ.get("BUCKET")
ORG = os.environ.get("ORG")
DATABASE_URL = os.environ.get("DATABASE_URL")


def validate_configuration():
    """Fail early with an actionable message when service configuration is absent."""
    required_values = {
        "INFLUXDB_TOKEN": INFLUXDB_TOKEN,
        "BUCKET": BUCKET,
        "ORG": ORG,
        "DATABASE_URL": DATABASE_URL,
    }
    missing = [name for name, value in required_values.items() if not value]
    if missing:
        raise RuntimeError("Missing required configuration: " + ", ".join(missing))


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    validate_configuration()

    sensor = gpiozero.DigitalInputDevice(
        pin=SENSOR_PIN_BCM,
        pull_up=True,
    )

    dao = InfluxDBDAO(url=DATABASE_URL, token=INFLUXDB_TOKEN, org=ORG, bucket=BUCKET)
    catwheel_logger = CatwheelLogger(dao, sensor, logger)

    def request_shutdown(signum, _frame):
        logger.info("Received signal %s; shutting down.", signum)
        catwheel_logger.stop()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    catwheel_logger.run()

if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        logging.getLogger(__name__).error("Catwheel startup failed: %s", exc)
        raise SystemExit(1) from exc
