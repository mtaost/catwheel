import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

class InfluxDBDAO:
    def __init__(self, url, token, org, bucket):
        self.client = influxdb_client.InfluxDBClient(
            url=url,
            token=token,
            org=org
        )
        self.write_api = self.client.write_api()
        self.delete_api = self.client.delete_api()
        self.bucket = bucket

    def write_run_data(self, run_id, time_ns, speed):
        point = influxdb_client.Point("catwheel_speed") \
            .tag("run_id", run_id) \
            .field("speed", speed) \
            .time(int(time_ns * 1000000000), influxdb_client.WritePrecision.NS)
        self.write_api.write(bucket=self.bucket, org=self.client.org, record=point)

    def write_run_metadata(self, run_id, time_ns, max_speed, avg_speed, distance_travelled, run_duration):
        point = influxdb_client.Point("catwheel_run_metadata") \
            .tag("run_id", run_id) \
            .field("max_speed_mph", max_speed) \
            .field("avg_speed_mph", avg_speed) \
            .field("distance_travelled_ft", distance_travelled) \
            .field("run_duration_seconds", run_duration) \
            .time(int(time_ns * 1000000000), influxdb_client.WritePrecision.NS)
        self.write_api.write(bucket=self.bucket, org=self.client.org, record=point)

    def delete_run_data(self, run_id):
        self.delete_api.delete(
            start="1970-01-01T00:00:00Z",
            stop="2100-01-01T00:00:00Z",
            predicate=f'_measurement="catwheel_speed" AND run_id="{run_id}"',
            bucket=self.bucket,
            org=self.client.org
        )
        self.delete_api.delete(
            start="1970-01-01T00:00:00Z",
            stop="2100-01-01T00:00:00Z",
            predicate=f'_measurement="catwheel_run_metadata" AND run_id="{run_id}"',
            bucket=self.bucket,
            org=self.client.org
        )

# Example usage:
# dao = InfluxDBDAO(url="http://localhost:8086", token="my-token", org="my-org", bucket="my-bucket")
# dao.write_data(run_id="1234", speed=10.5)


# dao.delete_api.delete(
#     start="2025-03-07T08:23:42.000Z",
#     stop="2025-03-07T08:23:43.100Z",
#     predicate='_measurement="catwheel_run_metadata" AND run_id="eacf4aa92176452b9eb6e7b09605e561"',
#     bucket=dao.bucket,
#     org=dao.client.org
# )

# point = influxdb_client.Point("catwheel_run_metadata") \
#     .tag("run_id", "eacf4aa92176452b9eb6e7b09605e561") \
#     .field("max_speed_mph", 11.4) \
#     .field("avg_speed_mph", 5.72) \
#     .field("distance_travelled_ft", 348.0) \
#     .field("run_duration_seconds", 100.0) \
#     .time(int(1741335823 * 1000000000), influxdb_client.WritePrecision.NS)
# dao.write_api.write(bucket=dao.bucket, org=dao.client.org, record=point)
