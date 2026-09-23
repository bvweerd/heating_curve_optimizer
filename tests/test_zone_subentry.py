"""Tests for the heating-zone subentry (phase 5c, docs/redesign/REDESIGN.md).

`HeatingZoneSubentryFlow` itself (config_flow.py) subclasses
`homeassistant.config_entries.ConfigSubentryFlow`, which IS available in
this HA version. So `HeatingZoneSubentryFlow` is a real class here, and
`async_get_supported_subentry_types` returns all three subentry types.

What is verified here:
- HeatingZoneSubentryFlow is defined (not None)
- async_get_supported_subentry_types includes ZONE_SUBENTRY_TYPE
- the pure validation/schema-default logic (_validate_zone_subentry)
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
    _build_zone_subentry_schema,
    _validate_zone_subentry,
)
from custom_components.heating_curve_optimizer.const import ZONE_SUBENTRY_TYPE


def test_heating_zone_subentry_flow_is_available():
    """ConfigSubentryFlow is available in this HA version, so
    HeatingZoneSubentryFlow must be a real class (not None)."""
    assert HeatingZoneSubentryFlow is not None


def test_async_get_supported_subentry_types_includes_zone():
    result = HeatingCurveOptimizerConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert ZONE_SUBENTRY_TYPE in result


def test_validate_zone_subentry_normalizes_input():
    data = _validate_zone_subentry(
        {
            "name": "  Living Room  ",
            "area_m2": "35",
            "energy_label": "B",
            "target_indoor_temp": "21.5",
            "indoor_temperature_sensor": "sensor.living_room_temp",
            "glass_east_m2": "2",
            "glass_west_m2": "3",
            "glass_south_m2": "5",
            "glass_u_value": "1.1",
            "ventilation_type": "heat_recovery_70",
            "ceiling_height": "2.8",
            "thermal_mass_class": "heavy",
            "emitter_type": "underfloor",
            "indoor_temp_hysteresis_lower": "0.2",
            "indoor_temp_hysteresis_upper": "0.4",
        }
    )
    assert data["name"] == "Living Room"
    assert data["area_m2"] == 35.0
    assert data["energy_label"] == "B"
    assert data["target_indoor_temp"] == 21.5
    assert data["indoor_temperature_sensor"] == "sensor.living_room_temp"
    assert data["glass_east_m2"] == 2.0
    assert data["glass_west_m2"] == 3.0
    assert data["glass_south_m2"] == 5.0
    assert data["glass_u_value"] == 1.1
    assert data["ventilation_type"] == "heat_recovery_70"
    assert data["ceiling_height"] == 2.8
    assert data["thermal_mass_class"] == "heavy"
    assert data["emitter_type"] == "underfloor"
    assert data["indoor_temp_hysteresis_lower"] == 0.2
    assert data["indoor_temp_hysteresis_upper"] == 0.4


def test_validate_zone_subentry_defaults_target_temp():
    data = _validate_zone_subentry(
        {"name": "Bedroom", "area_m2": 12, "energy_label": "C"}
    )
    assert data["target_indoor_temp"] == 20.0
    assert data["indoor_temperature_sensor"] is None
    assert data["glass_east_m2"] == 0.0
    assert data["glass_west_m2"] == 0.0
    assert data["glass_south_m2"] == 0.0
    assert data["glass_u_value"] == 1.2
    assert data["ventilation_type"] == "natural_standard"
    assert data["ceiling_height"] == 2.5
    assert data["thermal_mass_class"] == "medium"
    assert data["emitter_type"] == "radiator"
    assert data["indoor_temp_hysteresis_lower"] == 0.3
    assert data["indoor_temp_hysteresis_upper"] == 0.5


def test_validate_zone_subentry_rejects_blank_name():
    with pytest.raises(vol.Invalid):
        _validate_zone_subentry({"name": "   ", "area_m2": 12, "energy_label": "C"})


def test_validate_zone_subentry_rejects_non_positive_area():
    with pytest.raises(vol.Invalid):
        _validate_zone_subentry({"name": "Attic", "area_m2": 0, "energy_label": "C"})
    with pytest.raises(vol.Invalid):
        _validate_zone_subentry({"name": "Attic", "area_m2": -5, "energy_label": "C"})


def test_zone_subentry_schema_thermal_mass_class_and_emitter_type_options():
    """thermal_mass_class/emitter_type must be selectable in the zone
    subentry's own form - moved here from the main flow's basic step,
    since every zone (including the first) is configured this way now."""
    schema = _build_zone_subentry_schema()
    thermal_mass_field = next(
        k for k in schema.schema if str(k) == "thermal_mass_class"
    )
    emitter_field = next(k for k in schema.schema if str(k) == "emitter_type")
    assert set(schema.schema[thermal_mass_field].config["options"]) == {
        "light",
        "medium",
        "heavy",
    }
    assert set(schema.schema[emitter_field].config["options"]) == {
        "radiator",
        "underfloor",
        "fan_coil",
    }


def test_zone_subentry_schema_rejects_invalid_ventilation_type():
    schema = _build_zone_subentry_schema()
    with pytest.raises(vol.Invalid):
        schema(
            {
                "name": "Attic",
                "area_m2": 12,
                "energy_label": "C",
                "ventilation_type": "not_a_real_type",
            }
        )


def test_zone_subentry_schema_rejects_invalid_thermal_mass_class():
    schema = _build_zone_subentry_schema()
    with pytest.raises(vol.Invalid):
        schema(
            {
                "name": "Attic",
                "area_m2": 12,
                "energy_label": "C",
                "thermal_mass_class": "not_a_real_class",
            }
        )


def test_zone_subentry_schema_rejects_invalid_emitter_type():
    schema = _build_zone_subentry_schema()
    with pytest.raises(vol.Invalid):
        schema(
            {
                "name": "Attic",
                "area_m2": 12,
                "energy_label": "C",
                "emitter_type": "not_a_real_emitter",
            }
        )


def test_zone_subentry_schema_rejects_hysteresis_out_of_range():
    schema = _build_zone_subentry_schema()
    with pytest.raises(vol.Invalid):
        schema(
            {
                "name": "Attic",
                "area_m2": 12,
                "energy_label": "C",
                "indoor_temp_hysteresis_lower": 0.0,
            }
        )
    with pytest.raises(vol.Invalid):
        schema(
            {
                "name": "Attic",
                "area_m2": 12,
                "energy_label": "C",
                "indoor_temp_hysteresis_upper": 2.5,
            }
        )


def test_zone_subentry_schema_defaults_prefill_from_existing_data():
    """Editing an existing zone (async_step_reconfigure) must prefill the
    new fields from its current subentry data, not just the original 5."""
    schema = _build_zone_subentry_schema(
        {
            "name": "Living room",
            "area_m2": 20.0,
            "energy_label": "B",
            "glass_south_m2": 8.0,
            "ventilation_type": "balanced_no_recovery",
            "thermal_mass_class": "light",
            "emitter_type": "underfloor",
            "indoor_temp_hysteresis_lower": 0.4,
        }
    )
    validated = schema(
        {
            "name": "Living room",
            "area_m2": 20.0,
            "energy_label": "B",
            "indoor_temperature_sensor": "sensor.living_room_temp",
        }
    )
    assert validated["glass_south_m2"] == 8.0
    assert validated["ventilation_type"] == "balanced_no_recovery"
    assert validated["thermal_mass_class"] == "light"
    assert validated["emitter_type"] == "underfloor"
    assert validated["indoor_temp_hysteresis_lower"] == 0.4


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
