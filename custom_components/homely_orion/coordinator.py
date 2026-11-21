"""Keeps the /home data up to date from polling and the WebSocket."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AlarmChange,
    HomelyAuthError,
    HomelyClient,
    HomelyError,
    HomelyEvent,
    HomelyRateLimitError,
    HomelyWebSocket,
)
from .const import CONF_LOCATION_ID, DOMAIN, MAX_UPDATE_INTERVAL, UPDATE_INTERVAL
from .mapping import apply_change, unmapped_states

_LOGGER = logging.getLogger(__name__)

type HomelyConfigEntry = ConfigEntry[HomelyCoordinator]


@dataclass
class HomelyData:
    """The whole /home response plus who last changed the alarm."""

    home: dict[str, Any]
    alarm_changed_by: str | None = None
    alarm_changed_at: str | None = None


class HomelyCoordinator(DataUpdateCoordinator[HomelyData]):
    """Fetches /home every 5 minutes and applies WebSocket changes in between."""

    config_entry: HomelyConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HomelyConfigEntry, client: HomelyClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.location_id: str = entry.data[CONF_LOCATION_ID]
        self.hub_device_id: str | None = None
        self._websocket: HomelyWebSocket | None = None
        self._logged_unmapped: set[str] = set()

    @property
    def websocket_connected(self) -> bool:
        """Whether real-time updates are flowing."""
        return self._websocket is not None and self._websocket.connected

    def device(self, device_id: str) -> dict[str, Any] | None:
        """Look up a device in the current data."""
        if self.data is None:
            return None
        for device in self.data.home.get("devices", []):
            if isinstance(device, dict) and device.get("id") == device_id:
                return device
        return None

    async def _async_update_data(self) -> HomelyData:
        try:
            home = await self.client.get_home(self.location_id)
        except HomelyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HomelyRateLimitError as err:
            self.update_interval = min(self.update_interval * 2, MAX_UPDATE_INTERVAL)
            _LOGGER.warning("Homely rate limit, next fetch in %s", self.update_interval)
            if self.data is not None:
                return self.data
            raise UpdateFailed(str(err)) from err
        except HomelyError as err:
            if self.data is not None and self.websocket_connected:
                _LOGGER.warning("Could not fetch Homely data (%s), keeping real-time data", err)
                return self.data
            raise UpdateFailed(str(err)) from err

        self.update_interval = UPDATE_INTERVAL
        self._log_unmapped(home)
        if self.data is None:
            return HomelyData(home)
        if _device_ids(home) - _device_ids(self.data.home):
            _LOGGER.info("New Homely devices found, reloading the integration")
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return HomelyData(home, self.data.alarm_changed_by, self.data.alarm_changed_at)

    async def async_start_websocket(self) -> None:
        """Start real-time updates."""
        self._websocket = HomelyWebSocket(
            self.client,
            self.location_id,
            self._on_event,
            self._on_connected,
            http_session=async_get_clientsession(self.hass),
        )
        await self._websocket.start()

        async def _on_stop(_: Event) -> None:
            await self.async_stop_websocket()

        self.config_entry.async_on_unload(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
        )

    async def async_stop_websocket(self) -> None:
        """Stop real-time updates."""
        websocket, self._websocket = self._websocket, None
        if websocket is not None:
            await websocket.stop()

    async def _on_connected(self) -> None:
        # Catch up on anything that happened while the connection was down.
        await self.async_request_refresh()

    @callback
    def _on_event(self, event: HomelyEvent) -> None:
        if self.data is None:
            return
        if isinstance(event, AlarmChange):
            self.data.home["alarmState"] = event.state
            self.data.alarm_changed_by = event.user_name
            self.data.alarm_changed_at = event.timestamp
        else:
            unknown = False
            for change in event:
                if not apply_change(
                    self.data.home,
                    change.device_id,
                    change.feature,
                    change.state,
                    change.value,
                    change.last_updated,
                ):
                    unknown = True
            if unknown:
                self.config_entry.async_create_background_task(
                    self.hass, self.async_request_refresh(), "homely_orion_refresh"
                )
        # Not async_set_updated_data(): that would restart the 5-minute timer,
        # and frequent signal-strength events would then stop polling entirely.
        self.async_update_listeners()

    def _log_unmapped(self, home: dict[str, Any]) -> None:
        for device in home.get("devices", []):
            if not isinstance(device, dict):
                continue
            for name in unmapped_states(device):
                key = f"{device.get('modelName')}:{name}"
                if key not in self._logged_unmapped:
                    self._logged_unmapped.add(key)
                    _LOGGER.debug("Homely state %s on %s is not mapped", name, device.get("modelName"))


def _device_ids(home: dict[str, Any]) -> set[str]:
    return {
        device["id"]
        for device in home.get("devices", [])
        if isinstance(device, dict) and isinstance(device.get("id"), str)
    }
