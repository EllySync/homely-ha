"""Lock: shows whether the Yale lock is locked. Cannot be controlled."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HomelyConfigEntry
from .entity import HomelyEntity, specs_for_platform

READ_ONLY = "Homely's API is read-only: the lock cannot be controlled from Home Assistant"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomelyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HomelyLock(coordinator, device_id, spec)
        for device_id, spec in specs_for_platform(coordinator, "lock")
    )


class HomelyLock(HomelyEntity, LockEntity):
    """Read-only lock state."""

    _attr_name = None  # the lock is the main feature: use the device name

    @property
    def is_locked(self) -> bool | None:
        value = self.value
        return value if isinstance(value, bool) else None

    async def async_lock(self, **kwargs: Any) -> None:
        raise HomeAssistantError(READ_ONLY)

    async def async_unlock(self, **kwargs: Any) -> None:
        raise HomeAssistantError(READ_ONLY)

    async def async_open(self, **kwargs: Any) -> None:
        raise HomeAssistantError(READ_ONLY)
