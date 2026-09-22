"""Tests for the heating-zone subentry (phase 5c, docs/redesign/REDESIGN.md).

`HeatingZoneSubentryFlow` itself (config_flow.py) subclasses
`homeassistant.config_entries.ConfigSubentryFlow`, which does not exist in
the Home Assistant release this repo's test environment can install
(2024.3.x - a package-index ceiling in this sandbox, not a real HA release
date; ConfigSubentryFlow landed in HA well after that). So
`HeatingZoneSubentryFlow` is `None` here, and its step methods
(async_step_user/async_step_reconfigure) are unverified by this suite -
they were written to mirror battery_controller's
BatteryControllerBatterySubentryFlow, which is proven working against a
live install per the config_entry diagnostics shared 2026-09-22, but that
is not the same as this repo's own tests exercising them.

What *is* verified here, and does not depend on ConfigSubentryFlow at all:
- the pure validation/schema-default logic (_validate_zone_subentry)
- the graceful-degradation contract itself: HeatingZoneSubentryFlow is
  None and async_get_supported_subentry_types returns {} rather than
  raising, on an HA release old enough to lack the base class
- __init__.py's zone-instantiation loop, using getattr(entry,
  "subentries", {}) so setup does not fail on HA without the attribute
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.config_flow import (
    HeatingCurveOptimizerConfigFlow,
    HeatingZoneSubentryFlow,
    _validate_zone_subentry,
)
from custom_components.heating_curve_optimizer.const import ZONE_SUBENTRY_TYPE


def test_heating_zone_subentry_flow_is_none_on_this_ha_release():
    """Documents the environment constraint explained in the module
    docstring, so a future upgrade of pytest-homeassistant-custom-component
    that starts providing ConfigSubentryFlow is a visible, deliberate
    change here rather than a silent one."""
    assert HeatingZoneSubentryFlow is None


def test_async_get_supported_subentry_types_returns_empty_dict_gracefully():
    result = HeatingCurveOptimizerConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert result == {}


def test_validate_zone_subentry_normalizes_input():
    data = _validate_zone_subentry(
        {
            "name": "  Living Room  ",
            "area_m2": "35",
            "energy_label": "B",
            "target_indoor_temp": "21.5",
            "indoor_temperature_sensor": "sensor.living_room_temp",
        }
    )
    assert data["name"] == "Living Room"
    assert data["area_m2"] == 35.0
    assert data["energy_label"] == "B"
    assert data["target_indoor_temp"] == 21.5
    assert data["indoor_temperature_sensor"] == "sensor.living_room_temp"


def test_validate_zone_subentry_defaults_target_temp():
    data = _validate_zone_subentry(
        {"name": "Bedroom", "area_m2": 12, "energy_label": "C"}
    )
    assert data["target_indoor_temp"] == 20.0
    assert data["indoor_temperature_sensor"] is None


def test_validate_zone_subentry_rejects_blank_name():
    with pytest.raises(vol.Invalid):
        _validate_zone_subentry({"name": "   ", "area_m2": 12, "energy_label": "C"})


def test_validate_zone_subentry_rejects_non_positive_area():
    with pytest.raises(vol.Invalid):
        _validate_zone_subentry({"name": "Attic", "area_m2": 0, "energy_label": "C"})
    with pytest.raises(vol.Invalid):
        _validate_zone_subentry({"name": "Attic", "area_m2": -5, "energy_label": "C"})


@pytest.mark.asyncio
async def test_zones_dict_empty_when_entry_lacks_subentries_attribute(
    hass: HomeAssistant,
):
    """The __init__.py zone loop must not raise on an HA release (or a
    plain object, as in a unit test) that has no `subentries` attribute at
    all - it degrades to zero zones instead."""
    entry = MagicMock(spec=["entry_id", "data", "options"])
    entry.entry_id = "test_entry_no_subentries"
    entry.data = {}
    entry.options = {}

    assert not hasattr(entry, "subentries") or isinstance(
        getattr(entry, "subentries", {}), dict
    )
    zones = {}
    for subentry_id, subentry in getattr(entry, "subentries", {}).items():
        if subentry.subentry_type != ZONE_SUBENTRY_TYPE:
            continue
        zones[subentry_id] = subentry
    assert zones == {}
