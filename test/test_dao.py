import os
from dao.influxdb_dao import InfluxDBDAO
import random

INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN")
BUCKET = os.environ.get("TEST_BUCKET")
ORG = os.environ.get("ORG")
DATABASE_URL = os.environ.get("DATABASE_URL")

if __name__ == "__main__":
    dao = InfluxDBDAO(url=DATABASE_URL, token=os.environ.get("INFLUXDB_TOKEN"), org=ORG, bucket=BUCKET)
    dao.write_run_data(0, random.randint(0, 10))