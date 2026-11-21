"""Base entity for Homely devices."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HomelyCoordinator
from .mapping import (
    EntitySpec,
    device_spec,
    entities_for_device,
    entity_value,
    is_available,
    unique_id,
)


def specs_for_platform(coordinator: HomelyCoordinator, platform: str) -> Iterator[tuple[str, EntitySpec]]:
    """(device_id, spec) for every entity of a platform."""
    for device in coordinator.data.home.get("devices", []):
        if not isinstance(device, dict) or not isinstance(device.get("id"), str):
            continue
        for spec in entities_for_device(device):
            if spec.platform == platform:
                yield device["id"], spec


class HomelyEntity(CoordinatorEntity[HomelyCoordinator]):
    """An entity backed by one state of one Homely device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HomelyCoordinator, device_id: str, spec: EntitySpec) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self.spec = spec
        self._attr_unique_id = unique_id(device_id, spec)
        if spec.translation_key:
            self._attr_translation_key = spec.translation_key
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        info = device_spec(coordinator.device(device_id) or {"id": device_id})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=info.name,
            model=info.model,
            serial_number=info.serial,
            suggested_area=info.suggested_area,
            via_device_id=coordinator.hub_device_id,
        )

    @property
    def device(self) -> dict[str, Any] | None:
        """The device data from the latest update."""
        return self.coordinator.device(self._device_id)

    @property
    def available(self) -> bool:
        device = self.device
        return super().available and device is not None and is_available(device, self.spec)

    @property
    def value(self) -> bool | float | str | None:
        """The mapped value, or None if unknown."""
        device = self.device
        return entity_value(device, self.spec) if device is not None else None
