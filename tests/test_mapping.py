"""Tests for mapping.py, using the anonymised /home response."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from mapping import (
    ONLINE,
    alarm_state,
    apply_change,
    device_spec,
    entities_for_device,
    entity_value,
    is_available,
    suggested_area,
    unique_id,
    unmapped_states,
)

FIXTURES = Path(__file__).parent / "fixtures"

MOTION = {
    "alarm_alarm", "alarm_tamper", "temperature_temperature", "battery_low",
    "battery_defect", "battery_voltage", "diagnostic_networklinkstrength", "online",
}
SMOKE = {
    "alarm_fire", "temperature_temperature", "battery_low", "battery_voltage",
    "diagnostic_networklinkstrength", "online",
}
ENTRY = {
    "alarm_alarm", "alarm_tamper", "temperature_temperature", "battery_low",
    "battery_voltage", "diagnostic_networklinkstrength", "online",
}
YALE = {"lock_state", "report_doorclosed", "report_event", "report_lowbat", "online"}

EXPECTED = {
    "Alarm Motion Sensor 2": MOTION,
    "Intelligent Smoke Alarm": SMOKE,
    "Alarm Entry Sensor 2": ENTRY,
    "Yale Doorman V2N": YALE,
}


@pytest.fixture
def home() -> dict[str, Any]:
    return json.loads((FIXTURES / "home.json").read_text())


def by_name(home: dict[str, Any], name: str) -> dict[str, Any]:
    return next(device for device in home["devices"] if device["name"] == name)


def spec(device: dict[str, Any], key: str):
    return next(s for s in entities_for_device(device) if s.key == key)


def test_every_device_gets_expected_entities(home):
    total = 0
    for device in home["devices"]:
        keys = {s.key for s in entities_for_device(device)}
        assert keys == EXPECTED[device["modelName"]], device["name"]
        total += len(keys)
    assert total == 60


def test_unique_ids_are_unique(home):
    ids = [unique_id(d["id"], s) for d in home["devices"] for s in entities_for_device(d)]
    assert len(ids) == len(set(ids))


def test_no_unmapped_states_in_fixture(home):
    for device in home["devices"]:
        assert unmapped_states(device) == [], device["name"]


def test_unknown_state_is_reported(home):
    device = copy.deepcopy(by_name(home, "Ir-kjeller"))
    device["features"]["thermostat"] = {"states": {"setpoint": {"value": 21}}}
    assert unmapped_states(device) == ["thermostat.setpoint"]


def test_motion_and_door_are_told_apart(home):
    assert spec(by_name(home, "Ir-kjeller"), "alarm_alarm").device_class == "motion"
    assert spec(by_name(home, "Kjellerdør"), "alarm_alarm").device_class == "door"


def test_platforms_and_categories(home):
    yale = by_name(home, "Yale Doorman V2N")
    assert spec(yale, "lock_state").platform == "lock"
    assert spec(yale, "report_event").platform == "sensor"
    assert spec(yale, "report_event").translation_key == "last_event"
    motion = by_name(home, "Ir-kjeller")
    assert spec(motion, "battery_voltage").unit == "V"
    assert spec(motion, "battery_voltage").diagnostic
    assert spec(motion, "temperature_temperature").unit == "°C"
    assert not spec(motion, "temperature_temperature").diagnostic
    assert spec(motion, "diagnostic_networklinkstrength").translation_key == "signal_strength"


def test_values_from_fixture(home):
    motion = by_name(home, "Ir-kjeller")
    assert entity_value(motion, spec(motion, "alarm_alarm")) is False
    assert entity_value(motion, spec(motion, "temperature_temperature")) == 15.3
    assert entity_value(motion, spec(motion, "battery_voltage")) == 2.8
    assert entity_value(motion, spec(motion, "diagnostic_networklinkstrength")) == 89
    assert entity_value(motion, ONLINE) is True

    yale = by_name(home, "Yale Doorman V2N")
    assert entity_value(yale, spec(yale, "lock_state")) is True  # locked
    assert entity_value(yale, spec(yale, "report_doorclosed")) is False  # closed -> door off
    assert entity_value(yale, spec(yale, "report_event")) == "DOORLOCK_LOCKED_FROM_APP"


def test_wrong_types_give_none(home):
    device = copy.deepcopy(by_name(home, "Ir-kjeller"))
    states = device["features"]
    states["alarm"]["states"]["alarm"]["value"] = "yes"
    states["temperature"]["states"]["temperature"]["value"] = True
    states["battery"]["states"]["voltage"]["value"] = None
    del states["battery"]["states"]["low"]
    device["online"] = "true"
    assert entity_value(device, spec(by_name(home, "Ir-kjeller"), "alarm_alarm")) is None
    assert entity_value(device, spec(by_name(home, "Ir-kjeller"), "temperature_temperature")) is None
    assert entity_value(device, spec(by_name(home, "Ir-kjeller"), "battery_voltage")) is None
    assert entity_value(device, spec(by_name(home, "Ir-kjeller"), "battery_low")) is None
    assert entity_value(device, ONLINE) is None


def test_availability_follows_online(home):
    device = copy.deepcopy(by_name(home, "Kjellerdør"))
    door = spec(device, "alarm_alarm")
    assert is_available(device, door)
    device["online"] = False
    assert not is_available(device, door)
    assert is_available(device, ONLINE)
    assert entity_value(device, ONLINE) is False


def test_device_spec(home):
    device = device_spec(by_name(home, "Ir-kjeller"))
    assert device.name == "Ir-kjeller"
    assert device.model == "Alarm Motion Sensor 2"
    assert device.suggested_area == "Kjeller gang"


@pytest.mark.parametrize(
    ("location", "area"),
    [
        ("Floor 0 - Kjeller gang", "Kjeller gang"),
        ("Floor -1 - Stue", "Stue"),
        ("Garasje", "Garasje"),
        ("", None),
        (None, None),
    ],
)
def test_suggested_area(location, area):
    assert suggested_area(location) == area


@pytest.mark.parametrize(
    ("homely", "ha"),
    [
        ("DISARMED", "disarmed"),
        ("ARMED_AWAY", "armed_away"),
        ("ARMED_NIGHT", "armed_night"),
        ("ARMED_PARTLY", "armed_home"),
        ("ARMED_AWAY_PENDING", "arming"),
        ("ARMED_NIGHT_PENDING", "arming"),
        ("ALARM_PENDING", "pending"),
        ("ALARM_STAY_PENDING", "pending"),
        ("BREACHED", "triggered"),
        ("SOMETHING_NEW", None),
        (None, None),
    ],
)
def test_alarm_state(homely, ha):
    assert alarm_state(homely) == ha


def test_apply_change_updates_value_and_time(home):
    device = by_name(home, "Kjellerdør")
    door = spec(device, "alarm_alarm")
    assert apply_change(home, device["id"], "alarm", "alarm", True, "2026-11-21T08:00:00.000Z")
    assert entity_value(device, door) is True
    assert device["features"]["alarm"]["states"]["alarm"]["lastUpdated"] == "2026-11-21T08:00:00.000Z"


def test_apply_change_new_state_and_unknown_device(home):
    device = by_name(home, "Kjellerdør")
    assert apply_change(home, device["id"], "alarm", "sensitivitylevel", 3, None)
    assert spec(device, "alarm_alarm").device_class == "motion"  # entities follow the data
    assert not apply_change(home, "unknown-device", "alarm", "alarm", True, None)


def test_apply_recorded_events(home):
    events = json.loads((FIXTURES / "ws_events.json").read_text())
    for message in events:
        data = message["data"]
        for change in data["changes"]:
            assert apply_change(
                home, data["deviceId"], change["feature"], change["stateName"],
                change["value"], change["lastUpdated"],
            )
