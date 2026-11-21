"""Alarm panel: shows the alarm state and who changed it. Cannot be controlled."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HomelyConfigEntry, HomelyCoordinator
from .mapping import alarm_state

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomelyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HomelyAlarmPanel(entry.runtime_data)])


class HomelyAlarmPanel(CoordinatorEntity[HomelyCoordinator], AlarmControlPanelEntity):
    """Read-only alarm panel for the home."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature(0)

    def __init__(self, coordinator: HomelyCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.location_id}_alarm"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.location_id)})
        self._warned: set[str] = set()

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        raw = self.coordinator.data.home.get("alarmState")
        state = alarm_state(raw)
        if state is None:
            if raw is not None and str(raw) not in self._warned:
                self._warned.add(str(raw))
                _LOGGER.warning("Unknown Homely alarm state %r", raw)
            return None
        return AlarmControlPanelState(state)

    @property
    def changed_by(self) -> str | None:
        return self.coordinator.data.alarm_changed_by

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"changed_at": self.coordinator.data.alarm_changed_at}
