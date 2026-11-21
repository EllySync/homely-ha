"""Turn Homely devices from /home into entity descriptions.

Entities are created from the features and states a device reports, not from
its model name, so new device types are picked up automatically.
This module must not import Home Assistant, so it can be tested with plain pytest.
Platforms, device classes and states are plain strings; the platform files
convert them to Home Assistant types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EntitySpec:
    """Description of one Home Assistant entity."""

    key: str
    platform: str
    feature: str | None
    state: str | None
    device_class: str | None = None
    unit: str | None = None
    diagnostic: bool = False
    invert: bool = False
    translation_key: str | None = None


@dataclass(frozen=True)
class DeviceSpec:
    """Description of one Home Assistant device."""

    id: str
    name: str
    model: str | None
    serial: str | None
    suggested_area: str | None


def _spec(feature: str, state: str, platform: str, **kwargs: Any) -> EntitySpec:
    return EntitySpec(key=f"{feature}_{state}", platform=platform, feature=feature, state=state, **kwargs)


# (feature, state) -> entity. alarm.alarm is handled separately (motion or door).
_RULES: dict[tuple[str, str], EntitySpec] = {
    ("alarm", "fire"): _spec("alarm", "fire", "binary_sensor", device_class="smoke"),
    ("alarm", "flood"): _spec("alarm", "flood", "binary_sensor", device_class="moisture"),
    ("alarm", "tamper"): _spec("alarm", "tamper", "binary_sensor", device_class="tamper", diagnostic=True),
    ("battery", "low"): _spec("battery", "low", "binary_sensor", device_class="battery", diagnostic=True),
    ("battery", "defect"): _spec(
        "battery", "defect", "binary_sensor", device_class="problem", diagnostic=True,
        translation_key="battery_defect",
    ),
    ("battery", "voltage"): _spec(
        "battery", "voltage", "sensor", device_class="voltage", unit="V", diagnostic=True
    ),
    ("temperature", "temperature"): _spec(
        "temperature", "temperature", "sensor", device_class="temperature", unit="°C"
    ),
    ("diagnostic", "networklinkstrength"): _spec(
        "diagnostic", "networklinkstrength", "sensor", unit="%", diagnostic=True,
        translation_key="signal_strength",
    ),
    ("lock", "state"): _spec("lock", "state", "lock"),
    ("report", "doorclosed"): _spec("report", "doorclosed", "binary_sensor", device_class="door", invert=True),
    ("report", "event"): _spec("report", "event", "sensor", translation_key="last_event"),
    ("report", "lowbat"): _spec("report", "lowbat", "binary_sensor", device_class="battery", diagnostic=True),
}

_MOTION = _spec("alarm", "alarm", "binary_sensor", device_class="motion")
_DOOR = _spec("alarm", "alarm", "binary_sensor", device_class="door")

ONLINE = EntitySpec(
    key="online", platform="binary_sensor", feature=None, state=None,
    device_class="connectivity", diagnostic=True,
)

# States that are deliberately not turned into entities.
_IGNORED_FEATURES = {"setup"}
_IGNORED_STATES = {
    ("alarm", "sensitivitylevel"),  # only used to tell motion from door
    ("diagnostic", "networklinkaddress"),
    ("lock", "autorelocktime"),
    ("lock", "soundvolume"),
    ("lock", "language"),
}
_HANDLED_REPORT_STATES = {"doorclosed", "event", "lowbat"}

# Homely alarmState -> Home Assistant AlarmControlPanelState value.
_ALARM_STATES = {
    "DISARMED": "disarmed",
    "ARMED_AWAY": "armed_away",
    "ARMED_NIGHT": "armed_night",
    "ARMED_PARTLY": "armed_home",
    "ARMED_AWAY_PENDING": "arming",
    "ARMED_NIGHT_PENDING": "arming",
    "ALARM_PENDING": "pending",
    "ALARM_STAY_PENDING": "pending",
    "BREACHED": "triggered",
}


def _states(device: dict[str, Any]) -> list[tuple[str, str]]:
    """All (feature, state) pairs the device reports."""
    features = device.get("features")
    if not isinstance(features, dict):
        return []
    pairs = []
    for feature, body in features.items():
        states = body.get("states") if isinstance(body, dict) else None
        if isinstance(states, dict):
            pairs.extend((feature, state) for state in states)
    return pairs


def device_spec(device: dict[str, Any]) -> DeviceSpec:
    """Return the device description for a Homely device."""
    return DeviceSpec(
        id=device["id"],
        name=_str(device.get("name")) or _str(device.get("modelName")) or device["id"],
        model=_str(device.get("modelName")),
        serial=_str(device.get("serialNumber")),
        suggested_area=suggested_area(device.get("location")),
    )


def suggested_area(location: Any) -> str | None:
    """'Floor 0 - Kjeller gang' -> 'Kjeller gang'."""
    if not isinstance(location, str) or not location.strip():
        return None
    _, sep, room = location.partition(" - ")
    return (room if sep else location).strip() or None


def entities_for_device(device: dict[str, Any]) -> list[EntitySpec]:
    """Return the entities a device should have, based on its states."""
    pairs = set(_states(device))
    entities = []
    if ("alarm", "alarm") in pairs:
        entities.append(_MOTION if ("alarm", "sensitivitylevel") in pairs else _DOOR)
    entities.extend(spec for pair, spec in _RULES.items() if pair in pairs)
    entities.append(ONLINE)
    return entities


def unmapped_states(device: dict[str, Any]) -> list[str]:
    """States that are neither mapped nor deliberately ignored (for debug logging)."""
    return sorted(
        f"{feature}.{state}"
        for feature, state in _states(device)
        if (feature, state) not in _RULES
        and (feature, state) != ("alarm", "alarm")
        and feature not in _IGNORED_FEATURES
        and (feature, state) not in _IGNORED_STATES
        and not (feature == "report" and state not in _HANDLED_REPORT_STATES)
    )


def raw_value(device: dict[str, Any], feature: str, state: str) -> Any:
    """The raw value of a state, or None if missing."""
    try:
        return device["features"][feature]["states"][state].get("value")
    except (KeyError, TypeError, AttributeError):
        return None


def entity_value(device: dict[str, Any], spec: EntitySpec) -> bool | float | str | None:
    """The entity value, or None if missing or of the wrong type."""
    if spec.key == "online":
        online = device.get("online")
        return online if isinstance(online, bool) else None
    value = raw_value(device, spec.feature, spec.state)  # type: ignore[arg-type]
    if spec.platform in ("binary_sensor", "lock"):
        if not isinstance(value, bool):
            return None
        return not value if spec.invert else value
    if spec.unit is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value
    return value if isinstance(value, str) else None


def is_available(device: dict[str, Any], spec: EntitySpec) -> bool:
    """Entities are unavailable while the device is offline, except the online sensor."""
    return spec.key == "online" or device.get("online") is True


def alarm_state(value: Any) -> str | None:
    """Homely alarmState -> Home Assistant alarm panel state, or None if unknown."""
    return _ALARM_STATES.get(value) if isinstance(value, str) else None


def unique_id(device_id: str, spec: EntitySpec) -> str:
    """Stable unique ID, independent of names."""
    return f"{device_id}_{spec.key}"


def apply_change(
    home: dict[str, Any],
    device_id: str,
    feature: str,
    state: str,
    value: Any,
    last_updated: str | None,
) -> bool:
    """Put a WebSocket change into the /home data. False if the device is unknown."""
    for device in home.get("devices", []):
        if isinstance(device, dict) and device.get("id") == device_id:
            features = device.setdefault("features", {})
            states = features.setdefault(feature, {}).setdefault("states", {})
            entry = states.setdefault(state, {})
            entry["value"] = value
            if last_updated is not None:
                entry["lastUpdated"] = last_updated
            return True
    return False


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
