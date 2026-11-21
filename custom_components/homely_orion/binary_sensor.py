"""Binary sensors: motion, door, smoke, tamper, battery, online."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HomelyConfigEntry, HomelyCoordinator
from .entity import HomelyEntity, specs_for_platform
from .mapping import EntitySpec


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomelyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HomelyBinarySensor(coordinator, device_id, spec)
        for device_id, spec in specs_for_platform(coordinator, "binary_sensor")
    )


class HomelyBinarySensor(HomelyEntity, BinarySensorEntity):
    """A Homely binary state."""

    def __init__(self, coordinator: HomelyCoordinator, device_id: str, spec: EntitySpec) -> None:
        super().__init__(coordinator, device_id, spec)
        if spec.device_class:
            self._attr_device_class = BinarySensorDeviceClass(spec.device_class)

    @property
    def is_on(self) -> bool | None:
        value = self.value
        return value if isinstance(value, bool) else None
