"""Homely Orion: read-only Homely alarm integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HomelyClient
from .const import DOMAIN
from .coordinator import HomelyConfigEntry, HomelyCoordinator

PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.LOCK,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: HomelyConfigEntry) -> bool:
    """Set up Homely from a config entry."""
    client = HomelyClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = HomelyCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # The home (alarm system) is the hub; every Homely device links to it.
    home = coordinator.data.home
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.location_id)},
        name=home.get("name") or entry.title,
        manufacturer="Homely",
        model="Homely alarm system",
        serial_number=home.get("gatewayserial"),
    )
    coordinator.hub_device_id = hub.id

    await coordinator.async_start_websocket()
    entry.async_on_unload(coordinator.async_stop_websocket)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomelyConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
