# Catwheel

Lumi loves to run:

https://github.com/user-attachments/assets/9d50facd-fc38-4fd6-8d27-cc8fcf8d2b0d

Catwheel uses a Raspberry Pi, magnets, a Hall-effect sensor, InfluxDB, and
Grafana to measure how fast and how far each catwheel run goes. It records
speed samples and completed-run metadata in a locally hosted InfluxDB database.

<img width="2243" height="1013" alt="Single-run Grafana dashboard" src="https://github.com/user-attachments/assets/3ff5e46e-92a6-488e-9114-a68280e5b4d3" />

The aggregated dashboard tracks cumulative distance plus maximum and average
speed over a selected time window.

<img width="2250" height="1089" alt="Aggregate Grafana dashboard" src="https://github.com/user-attachments/assets/8d1db0a9-6ac2-425a-b0bc-4dfc7690ca12" />

## Local use

Create the virtual environment and install dependencies:

```bash
cd /home/pi/projects/catwheel
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Supply the required InfluxDB settings in the environment, then run:

```bash
export INFLUXDB_TOKEN='...'
export BUCKET='...'
export ORG='...'
export DATABASE_URL='http://influxdb-host:8086'
./start_catwheel_logger.sh
```

For a GPIO-only check that never contacts InfluxDB:

```bash
./venv/bin/python test/gpiotest.py
```

It prints `boop` each time the sensor activates. Stop either command with
`Ctrl+C`.

## Install as a system service

The included unit runs as user `pi`, needs access to the `gpio` group, waits
for the network, restarts after failures, and writes logs to the system journal.

1. Create a protected configuration file. Do not put the real token in this
   repository.

   ```bash
   sudo install -d -m 750 /etc/catwheel
   sudo install -o root -g pi -m 640 catwheel.env.example /etc/catwheel/catwheel.env
   sudoedit /etc/catwheel/catwheel.env
   ```

2. Install and start the unit.

   ```bash
   sudo install -o root -g root -m 644 deploy/catwheel.service /etc/systemd/system/catwheel.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now catwheel.service
   ```

3. Inspect its state and logs.

   ```bash
   systemctl status catwheel.service
   journalctl -u catwheel.service -f
   ```

To apply a code or configuration change, run `sudo systemctl restart catwheel.service`.
To stop it from starting at boot, run `sudo systemctl disable --now catwheel.service`.

## Telegram run notifications

The logger can send one speed-graph photo for every completed qualifying run.
Its caption includes peak and average speed, distance, and duration. It adds a low remark at 4 mph
or 10 seconds and below, and a high remark at 10 mph or 60 seconds and above;
the middle ranges intentionally have no speed/duration remark. Speeds over 10 mph
receive an extra speed-demon remark. It queues
notifications in the shared state database before delivery, so a temporary
network or Telegram outage does not affect sensor logging; queued messages are
retried with backoff and may be delivered more than once after an ambiguous
failure.

The first qualifying run after enabling Telegram establishes the local record
baseline. Later runs that exceed its peak speed or duration receive a record
announcement in their notification.

Create a bot with BotFather, add or start it in the destination direct chat,
group, or channel, then determine that destination's chat ID. Add both values
to the protected logger configuration and restart the logger:

```bash
sudoedit /etc/catwheel/catwheel.env
sudo systemctl restart catwheel.service
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` together. Do not put the bot
token in this repository, shell history, logs, or diagnostics. Leaving both
keys unset disables Telegram notifications; setting only one causes startup to
fail with an actionable configuration error. Install the updated Python
dependencies before restarting after this feature is added.

## Web dashboard

The dashboard is a separate service for the trusted home LAN. It is available
without a login to any device on that network. It reads completed runs from
InfluxDB with a **read-only** token and stores
editable detection settings plus manual cat labels in a local SQLite database.
It does not replace the existing Grafana dashboards.

The dashboard uses HTTP by design for a trusted LAN. Do not expose port 8080
to the internet; put it behind HTTPS before allowing remote access.

### Install the dashboard service

Install the updated Python dependencies, create the shared state directory and
least-privilege service account, then create protected configuration files.
The `catwheel` group lets the GPIO logger read the settings while the web
service writes them; the web service itself has no GPIO access.

```bash
cd /home/pi/projects/catwheel
./venv/bin/pip install -r requirements.txt
sudo groupadd --force catwheel
sudo useradd --system --gid catwheel --home-dir /nonexistent --shell /usr/sbin/nologin catwheel-web
sudo usermod -aG catwheel pi
sudo install -d -o pi -g catwheel -m 2770 /var/lib/catwheel
sudoedit /etc/catwheel/catwheel.env
sudo test ! -e /etc/catwheel/web.env && sudo install -o root -g catwheel -m 640 web.env.example /etc/catwheel/web.env
sudoedit /etc/catwheel/web.env
```

Add `CATWHEEL_STATE_DB=/var/lib/catwheel/state.db` to the existing logger
configuration. The guarded `install` command intentionally refuses to overwrite
an existing dashboard configuration.

Create an InfluxDB token that has read access only to the configured bucket and
place it in `INFLUXDB_READ_TOKEN` in `web.env`; never put that value in this
repository.

Install both service units after the group setup, then restart the logger so it
can use the shared settings database:

```bash
sudo install -o root -g root -m 644 deploy/catwheel.service /etc/systemd/system/catwheel.service
sudo install -o root -g root -m 644 deploy/catwheel-web.service /etc/systemd/system/catwheel-web.service
sudo systemctl daemon-reload
sudo systemctl restart catwheel.service
sudo systemctl enable --now catwheel-web.service
```

Visit `http://<logger-pi-hostname-or-ip>:8080`. The home page defaults to the
last 30 days and supports cat/date filtering. Run details provide an interactive
speed trace and manual `Lumi`, `Miso`, or `unknown` labels. Detection setting
changes are saved atomically and apply only when the logger begins its next run.

The logger records the applied settings revision and the count of rejected
over-limit speed samples in new completed-run metadata. Existing historical
runs and Grafana dashboards remain valid.

## Hardware assumptions

The application configures GPIO 17 as a pulled-up digital input, so it expects
a Hall sensor output that pulls the pin low when a magnet passes. Pi GPIO pins
are 3.3 V only; do not connect a 5 V sensor output directly to GPIO 17.

## Roadmap

- RGB Matrix speed display so a camera can see the MPH output.
- Telegram notifications with a graph snapshot when events are captured.
- Cat identification based on running pattern (Miso is not as elegant).

The logger now has an inactive event-publisher boundary with versioned future
topics `catwheel/v1/live-speed` and `catwheel/v1/run-completed`. A future MQTT
publisher/outbox can deliver those to the display and Telegram services without
making network outages affect sensor logging.
