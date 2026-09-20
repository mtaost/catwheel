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

## Hardware assumptions

The application configures GPIO 17 as a pulled-up digital input, so it expects
a Hall sensor output that pulls the pin low when a magnet passes. Pi GPIO pins
are 3.3 V only; do not connect a 5 V sensor output directly to GPIO 17.

## Roadmap

- RGB Matrix speed display so a camera can see the MPH output.
- Telegram notifications with a graph snapshot when events are captured.
- Cat identification based on running pattern (Miso is not as elegant).
