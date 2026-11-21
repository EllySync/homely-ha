"""Check api.py against a real Homely account (read-only).

    .venv/bin/python scripts/check_api.py [seconds]

Reads HOMELY_USER and HOMELY_PASSWORD from ~/.config/homely/credentials (mode 600).
Prints only counts and types, never IDs, tokens or raw data.
"""

from __future__ import annotations

import asyncio
from collections import Counter
import logging
from pathlib import Path
import stat
import sys

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "homely_orion"))

from api import AlarmChange, HomelyClient, HomelyWebSocket  # noqa: E402

CREDENTIALS = Path.home() / ".config" / "homely" / "credentials"


def read_credentials() -> tuple[str, str]:
    if CREDENTIALS.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        sys.exit(f"{CREDENTIALS} must have mode 600")
    values = {}
    for line in CREDENTIALS.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values["HOMELY_USER"], values["HOMELY_PASSWORD"]


async def main(seconds: int) -> None:
    user, password = read_credentials()
    async with aiohttp.ClientSession() as session:
        client = HomelyClient(session, user, password)
        await client.login()
        print(f"login ok, token renewal in {client.seconds_until_renewal():.0f} s")

        locations = await client.get_locations()
        print(f"locations: {len(locations)}")
        location_id = locations[0]["locationId"]

        home = await client.get_home(location_id)
        models = Counter(device.get("modelName") for device in home["devices"])
        online = sum(1 for device in home["devices"] if device.get("online"))
        print(f"alarmState: {home.get('alarmState')}, devices: {len(home['devices'])} ({online} online)")
        for model, count in sorted(models.items()):
            print(f"  {count} x {model}")

        events: Counter[str] = Counter()
        connects = 0

        def on_event(event) -> None:
            if isinstance(event, AlarmChange):
                events[f"alarm -> {event.state}"] += 1
            else:
                for change in event:
                    events[f"{change.feature}.{change.state}"] += 1

        async def on_connected() -> None:
            nonlocal connects
            connects += 1
            print("websocket connected")

        ws = HomelyWebSocket(client, location_id, on_event, on_connected, http_session=session)
        await ws.start()
        print(f"listening {seconds} s ... (open a door or walk past a sensor)")
        await asyncio.sleep(seconds)
        print(f"websocket connected now: {ws.connected}, connects: {connects}")
        await ws.stop()

        print(f"events: {sum(events.values())}")
        for name, count in sorted(events.items()):
            print(f"  {count} x {name}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 60))
