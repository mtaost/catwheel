from catwheellogger import CatwheelLogger
from dao.influxdb_dao import InfluxDBDAO
from settings_store import SettingsStore
from telegram_notifier import publisher_from_environment
from daily_summary import DailySummaryScheduler
from web.data import InfluxRunRepository
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
CATWHEEL_STATE_DB = os.environ.get("CATWHEEL_STATE_DB", "/var/lib/catwheel/state.db")


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
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bool(telegram_token) != bool(telegram_chat_id):
        missing_telegram_key = "TELEGRAM_BOT_TOKEN" if not telegram_token else "TELEGRAM_CHAT_ID"
        raise RuntimeError(
            f"Telegram notifications require {missing_telegram_key} when enabled"
        )
    daily_summary_enabled = os.environ.get("TELEGRAM_DAILY_SUMMARY_ENABLED", "false").lower() == "true"
    weekly_summary_enabled = os.environ.get("TELEGRAM_WEEKLY_SUMMARY_ENABLED", "false").lower() == "true"
    commands_enabled = os.environ.get("TELEGRAM_COMMANDS_ENABLED", "false").lower() == "true"
    if (daily_summary_enabled or weekly_summary_enabled or commands_enabled) and (not telegram_token or not telegram_chat_id):
        raise RuntimeError("Telegram summaries and commands require Telegram notifications to be enabled")
    if (daily_summary_enabled or weekly_summary_enabled or commands_enabled) and not os.environ.get("INFLUXDB_READ_TOKEN"):
        raise RuntimeError("Telegram summaries and commands require INFLUXDB_READ_TOKEN")


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
    settings_store = SettingsStore(CATWHEEL_STATE_DB)
    daily_summary_enabled = os.environ.get("TELEGRAM_DAILY_SUMMARY_ENABLED", "false").lower() == "true"
    weekly_summary_enabled = os.environ.get("TELEGRAM_WEEKLY_SUMMARY_ENABLED", "false").lower() == "true"
    commands_enabled = os.environ.get("TELEGRAM_COMMANDS_ENABLED", "false").lower() == "true"
    repository = None
    if daily_summary_enabled or weekly_summary_enabled or commands_enabled:
        repository = InfluxRunRepository(
            DATABASE_URL, os.environ["INFLUXDB_READ_TOKEN"], ORG, BUCKET
        )
    daily_summary_scheduler = None
    if daily_summary_enabled or weekly_summary_enabled:
        daily_summary_scheduler = DailySummaryScheduler(
            settings_store,
            repository,
            os.environ.get("DAILY_SUMMARY_TIME_ZONE", "America/New_York"),
            logger,
            daily_enabled=daily_summary_enabled,
            weekly_enabled=weekly_summary_enabled,
        )
    event_publisher = publisher_from_environment(
        settings_store,
        logger,
        daily_summary_scheduler,
        run_repository=repository if commands_enabled else None,
        report_timezone=os.environ.get("TELEGRAM_REPORT_TIME_ZONE", os.environ.get("DAILY_SUMMARY_TIME_ZONE")),
    )
    catwheel_logger = CatwheelLogger(
        dao,
        sensor,
        logger,
        settings_store=settings_store,
        event_publisher=event_publisher,
    )

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
