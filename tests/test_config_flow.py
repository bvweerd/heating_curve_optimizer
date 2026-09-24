"""Tests for the config flow, options flow and subentry flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.heating_curve_optimizer.config_flow import (
    flatten_main_input,
    validate_main_config,
    validate_zone_input,
)
from custom_components.heating_curve_optimizer.const import (
    DOMAIN,
    GAS_SUBENTRY_TYPE,
    PV_SUBENTRY_TYPE,
    ZONE_SUBENTRY_TYPE,
)

from .conftest import PRICE_SENSOR, make_entry, zone_data

API_CHECK = "custom_components.heating_curve_optimizer.config_flow._test_api_connection"


def _sectioned(**overrides) -> dict:
    user_input = {
        "prices": {"consumption_price_sensor": PRICE_SENSOR},
        "sensors": {},
        "heat_pump": {
            "base_cop": 4.0,
            "k_factor": 0.1,
            "outdoor_temp_coefficient": 0.08,
            "cop_compensation_factor": 0.95,
        },
        "heating_curve": {
            "heat_curve_min": 25.0,
            "heat_curve_max": 45.0,
            "heat_curve_min_outdoor": -10.0,
            "heat_curve_max_outdoor": 15.0,
            "offset_delta_t": 30,
        },
        "advanced": {"planning_window": 24},
    }
    for section, values in overrides.items():
        user_input[section] = {**user_input[section], **values}
    return user_input


def test_flatten_stores_cleared_optional_fields_as_none() -> None:
    flat = flatten_main_input(_sectioned())
    assert flat["consumption_price_sensor"] == PRICE_SENSOR
    assert flat["production_price_sensor"] is None
    assert flat["offset_delta_t"] == 30
    assert validate_main_config(flat) == {}


def test_validate_rejects_inverted_curve() -> None:
    flat = flatten_main_input(
        _sectioned(heating_curve={"heat_curve_min": 50.0, "heat_curve_max": 40.0})
    )
    assert validate_main_config(flat) == {"base": "curve_supply_range"}
    flat = flatten_main_input(
        _sectioned(heating_curve={"heat_curve_min_outdoor": 20.0})
    )
    assert validate_main_config(flat) == {"base": "curve_outdoor_range"}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with patch(API_CHECK, return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _sectioned()
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["base_cop"] == 4.0
    assert result["data"]["planning_window"] == 24


async def test_user_flow_reports_unreachable_api(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(API_CHECK, return_value="cannot_connect"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _sectioned()
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_single_instance(hass: HomeAssistant) -> None:
    make_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT


async def test_options_flow_keeps_entity_managed_setpoints(hass: HomeAssistant) -> None:
    entry = make_entry(options={"target_indoor_temp": 21.0})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.heating_curve_optimizer.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _sectioned(advanced={"planning_window": 12})
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["planning_window"] == 12
    assert entry.options["target_indoor_temp"] == 21.0


def test_zone_validation() -> None:
    data, error = validate_zone_input(zone_data())
    assert error is None
    assert "heat_curve_min" not in data

    _, error = validate_zone_input(zone_data(heat_curve_min=30.0))
    assert error == "zone_curve_incomplete"

    data, error = validate_zone_input(zone_data(heat_curve_min=25.0, heat_curve_max=35.0))
    assert error is None and data["heat_curve_max"] == 35.0

    _, error = validate_zone_input(zone_data(name="  "))
    assert error == "name_required"


async def _start_subentry_flow(hass: HomeAssistant, entry, subentry_type: str):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type),
        context={"source": config_entries.SOURCE_USER},
    )


async def test_zone_subentry_flow(hass: HomeAssistant) -> None:
    entry = make_entry(zones=0)
    entry.add_to_hass(hass)
    result = await _start_subentry_flow(hass, entry, ZONE_SUBENTRY_TYPE)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], zone_data("Upstairs")
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Upstairs"


async def test_pv_subentry_flow(hass: HomeAssistant) -> None:
    entry = make_entry(zones=0)
    entry.add_to_hass(hass)
    result = await _start_subentry_flow(hass, entry, PV_SUBENTRY_TYPE)
    assert result["step_id"] == "configure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "peak_power_kwp": 4.2,
            "orientation": 180,
            "tilt": 30,
            "efficiency_factor": 0.85,
            "dc_coupled": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "4.2 kWp"


async def test_gas_subentry_is_single_instance(hass: HomeAssistant) -> None:
    entry = make_entry(zones=0, gas=True)
    entry.add_to_hass(hass)
    result = await _start_subentry_flow(hass, entry, GAS_SUBENTRY_TYPE)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
