"""Sensors: temperature, battery voltage, signal strength, last lock event."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
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
        HomelySensor(coordinator, device_id, spec)
        for device_id, spec in specs_for_platform(coordinator, "sensor")
    )


class HomelySensor(HomelyEntity, SensorEntity):
    """A Homely measurement or text state."""

    def __init__(self, coordinator: HomelyCoordinator, device_id: str, spec: EntitySpec) -> None:
        super().__init__(coordinator, device_id, spec)
        if spec.device_class:
            self._attr_device_class = SensorDeviceClass(spec.device_class)
        if spec.unit:
            self._attr_native_unit_of_measurement = spec.unit
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | str | None:
        value = self.value
        return None if isinstance(value, bool) else value
