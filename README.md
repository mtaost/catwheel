# Catwheel

Catwheel reads a Hall-effect sensor on BCM GPIO 17 and writes wheel-speed and
completed-run data to InfluxDB.

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
