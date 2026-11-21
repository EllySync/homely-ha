# Homely Orion for Home Assistant

[![Validate](https://github.com/EllySync/homely-ha-orion/actions/workflows/validate.yml/badge.svg)](https://github.com/EllySync/homely-ha-orion/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

A small, read-only Home Assistant integration for the [Homely](https://www.homely.no/) alarm system. It shows the alarm state and every device in your home — motion, doors, smoke, lock, battery, temperature and signal strength — using Homely's official API, with real-time updates over WebSocket.

> **Read-only.** Homely's API does not allow arming, disarming or locking. The alarm panel and lock show their state but cannot be controlled from Home Assistant.

## Features

- Alarm panel with the current state and who last changed it
- One Home Assistant device per Homely device, placed in the room set in the Homely app
- Entities are created from what each device reports, not from a list of known models, so new device types are picked up automatically
- Real-time updates over WebSocket, plus a full refresh every 5 minutes as a safety net
- Recovers on its own after network or Homely outages, without restarting Home Assistant
- Re-authentication from the UI if the password changes
- No YAML: set up from **Settings → Devices & services**

## Requirements

- Home Assistant 2026.11 or newer
- A Homely account (the same e-mail and password as in the Homely app)

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/EllySync/homely-ha-orion` with type **Integration**.
3. Install **Homely Orion** and restart Home Assistant.

### Manual

1. Copy `custom_components/homely_orion` into `custom_components/` in your Home Assistant configuration folder.
2. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services → Add integration** and search for **Homely Orion**.
2. Log in with your Homely e-mail and password.
3. If your account has several homes, choose one. Add the integration again for each additional home.

The password is stored in Home Assistant's config entry so the integration can log in again after a long downtime (Homely's refresh tokens expire after 30 minutes).

## Entities

| Homely state | Entity | Device class | Category |
|---|---|---|---|
| Alarm state of the home | Alarm panel | – | – |
| `alarm.alarm` (motion sensor) | Binary sensor | motion | – |
| `alarm.alarm` (entry sensor) | Binary sensor | door | – |
| `alarm.fire` | Binary sensor | smoke | – |
| `alarm.flood` | Binary sensor | moisture | – |
| `alarm.tamper` | Binary sensor | tamper | diagnostic |
| `battery.low`, `report.lowbat` | Binary sensor | battery | diagnostic |
| `battery.defect` | Binary sensor | problem | diagnostic |
| `battery.voltage` | Sensor (V) | voltage | diagnostic |
| `temperature.temperature` | Sensor (°C) | temperature | – |
| `diagnostic.networklinkstrength` | Sensor (%) | – | diagnostic |
| Device online | Binary sensor | connectivity | diagnostic |
| `lock.state` | Lock (state only) | – | – |
| `report.doorclosed` | Binary sensor | door | – |
| `report.event` | Sensor (text, e.g. `DOORLOCK_MANUAL_UNLOCK`) | – | – |

When a device is offline, all its entities become unavailable except the connectivity sensor.

Alarm states map as follows: `DISARMED` → disarmed, `ARMED_AWAY` → armed away, `ARMED_NIGHT` → armed night, `ARMED_PARTLY` → armed home, `ARMED_AWAY_PENDING` / `ARMED_NIGHT_PENDING` → arming, `ALARM_PENDING` / `ALARM_STAY_PENDING` → pending, `BREACHED` → triggered.

### Tested devices

- Alarm Motion Sensor 2
- Alarm Entry Sensor 2
- Intelligent Smoke Alarm
- Yale Doorman V2N

Other devices should work if they report the same features. Unknown features are logged at debug level; please open an issue with the log line if you have a device that is missing entities.

## How it works

The integration only talks to `sdk.iotiliti.cloud` over HTTPS, the same cloud API the Homely app uses. It has no contact with the alarm hub on your network.

- `POST /homely/oauth/token` and `/homely/oauth/refresh-token` for login and token renewal
- `GET /homely/locations` and `/homely/home/{id}` for homes, alarm state and devices
- A Socket.IO WebSocket for real-time device and alarm events

The WebSocket reconnects forever with a growing delay (5 s up to 5 min) and fetches the full state after every reconnect. If Homely answers *429 Too Many Requests*, the integration keeps the last data and slows down polling.

## Troubleshooting

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.homely_orion: debug
```

Tokens and passwords are never logged.

## Development

```bash
uv python install 3.14
uv venv --python 3.14 --seed .venv
.venv/bin/pip install --group test
.venv/bin/pytest
```

`api.py` and `mapping.py` do not import Home Assistant, so they are tested with plain pytest. Test data in `tests/fixtures/` is anonymised data from a real home.

To check the API client against your own account (read-only, prints only counts and types):

```bash
mkdir -p ~/.config/homely
install -m 600 /dev/null ~/.config/homely/credentials   # HOMELY_USER=... and HOMELY_PASSWORD=...
.venv/bin/python scripts/check_api.py 60
```

## Disclaimer

This is an unofficial integration. It is not affiliated with, endorsed by or supported by Homely AS. Homely's API is in beta and may change without notice. Do not rely on Home Assistant as your only alarm notification.

## Authors

Written by Morten Egge ([@EllySync](https://github.com/EllySync)) together with [Claude Code](https://claude.com/claude-code) (Claude Opus 5.5, Anthropic), which wrote most of the code, tests and documentation in close dialogue: every design decision was discussed and approved before any code was written, and the result was tested against a real Homely installation.

## Credits

Inspired by [bjafl/homely-ha](https://github.com/bjafl/homely-ha) and [ludvikroed/homely-integration](https://github.com/ludvikroed/homely-integration) (both MIT), and by [hansrune/homely-tools](https://github.com/hansrune/homely-tools).

## License

[MIT](LICENSE)
