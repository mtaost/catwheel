# Catwheel contributor notes

## Purpose and runtime

Catwheel is a Raspberry Pi service that reads a Hall-effect sensor on BCM GPIO
17 and stores speed samples and completed-run metadata in InfluxDB.

- Run as the `pi` user with access to the `gpio` group.
- The production service is `catwheel.service`; its source template is
  `deploy/catwheel.service`.
- Service logs belong in the system journal. Do not reintroduce `nohup`, output
  files, or terminal spinner handlers for production execution.

## Credentials and configuration

- The production configuration is `/etc/catwheel/catwheel.env`, not a tracked
  project file. It must remain owned by `root:pi` with mode `640`.
- Required keys are `INFLUXDB_TOKEN`, `BUCKET`, `ORG`, and `DATABASE_URL`.
- Never print, commit, copy into documentation, or place real values in an
  example file. Redact values in diagnostics.
- Treat any local token files as sensitive. Do not assume an older token is
  valid; verify it against InfluxDB only with explicit user approval.
- The dashboard configuration is `/etc/catwheel/web.env`, owned by
  `root:catwheel` with mode `640`. It contains the dashboard's read-only
  InfluxDB token and must never be committed or printed.

## Dashboard runtime

- The LAN dashboard is served by `catwheel-web.service` on port 8080. Its
  source unit is `deploy/catwheel-web.service`; it runs as `catwheel-web` and
  must not gain GPIO access.
- Dashboard settings and manual labels share `/var/lib/catwheel/state.db` with
  the logger. Preserve group-writable access for the `catwheel` group; do not
  add database files to version control.
- The dashboard intentionally has no login for the trusted home LAN. Do not
  expose port 8080 outside that network without adding appropriate HTTPS and
  access controls.

## Useful commands

Run these from `/home/pi/projects/catwheel`.

```bash
# GPIO-only sensor test: does not contact InfluxDB.
./venv/bin/python test/gpiotest.py

# Syntax check without touching hardware or InfluxDB.
./venv/bin/python -m py_compile main.py catwheellogger.py dao/influxdb_dao.py

# Focused dashboard checks: use fakes only, never production InfluxDB or GPIO.
./venv/bin/python -m unittest discover -s test -p 'test_web.py' -v

# Inspect the running service and its logs.
sudo systemctl status catwheel.service
journalctl -u catwheel.service -f

# Apply code or configuration changes after validation.
sudo systemctl restart catwheel.service
sudo systemctl restart catwheel-web.service
```

Installing or enabling the service changes host state. Do that only when the
user explicitly asks:

```bash
sudo install -o root -g root -m 644 deploy/catwheel.service /etc/systemd/system/catwheel.service
sudo systemctl daemon-reload
sudo systemctl enable --now catwheel.service
```

## Hardware and behavior constraints

- GPIO 17 is configured with an internal pull-up. The expected Hall sensor
  output pulls the pin low when a magnet passes.
- Raspberry Pi GPIO is 3.3 V only. Never connect a 5 V sensor output directly
  to GPIO 17.
- The service sleeps/polls at one-second intervals while callbacks record
  sensor transitions. Preserve clean shutdown: `SIGTERM` and `SIGINT` must
  close GPIO and InfluxDB resources.
- The logger writes raw speed data while a run is in progress, then either
  saves metadata or deletes the run when it falls below configured thresholds.

## Change expectations

- Keep `README.md`, `catwheel.env.example`, and the systemd unit aligned with
  any startup, configuration, or deployment change.
- Validate Python compilation and `systemd-analyze verify` for every modified
  service unit after modifying runtime or service files.
- Do not test `main.py` against production InfluxDB or live GPIO unless the
  user explicitly requests it. Prefer `test/gpiotest.py` for hardware checks.
