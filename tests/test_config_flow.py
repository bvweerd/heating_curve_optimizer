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
        "expert_optimizer": {
            "comfort_penalty_weight": 50.0,
            "cycling_penalty_weight": 0.01,
            "hard_floor_penalty": 1000.0,
            "feed_in_price_fallback": 0.07,
            "offset_min": -4,
            "offset_max": 4,
            "heatpump_headroom": 1.3,
        },
        "expert_calibration": {
            "calibration_window": 300,
            "min_samples_to_apply": 30,
            "min_r_squared": 0.5,
            "min_share_each_direction": 0.15,
            "min_indoor_temp_delta": 0.3,
            "prior_strength": 3.0,
            "ratio_bounds_lower": 0.3,
            "ratio_bounds_upper": 3.0,
            "solar_factor_bounds_upper": 2.5,
            "internal_gain_max_w_per_m2": 12.0,
            "min_cop_samples": 20,
            "cop_scale_bounds_lower": 0.5,
            "cop_scale_bounds_upper": 1.5,
            "min_emitter_samples": 20,
            "emitter_exponent_bounds_lower": 0.9,
            "emitter_exponent_bounds_upper": 1.6,
            "calibration_min_hours": 0.25,
            "calibration_max_hours": 6.0,
            "calibration_max_jump_c": 1.0,
            "gas_min_window_hours": 2.0,
            "min_residual_samples": 48,
            "two_mass_autocorrelation": 0.6,
        },
        "expert_climate": {
            "ground_albedo": 0.2,
            "defrost_free_threshold": 6.0,
            "defrost_cold_threshold": -10.0,
            "defrost_base_penalty": 0.25,
            "defrost_min_cop_multiplier": 0.6,
            "min_cop": 0.5,
            "mwh_magnitude_threshold": 5.0,
            "idle_power_threshold_kw": 0.1,
            "price_change_rel": 0.10,
            "price_change_min_abs": 0.01,
            "min_running_power_kw": 0.3,
            "accuracy_horizon_hours": 1.0,
        },
    }
    for section, values in overrides.items():
        user_input[section] = {**user_input.get(section, {}), **values}
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
    with patch(
        "custom_components.heating_curve_optimizer.async_setup_entry", return_value=True
    ):
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

    data, error = validate_zone_input(
        zone_data(heat_curve_min=25.0, heat_curve_max=35.0)
    )
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


async def test_detected_integrations_prefill_selected_only(hass: HomeAssistant) -> None:
    from custom_components.heating_curve_optimizer.companion_integrations import (
        DetectedSensor,
    )

    detected = {
        "consumption_price_sensor": DetectedSensor("sensor.decc_price", "DECC"),
        "grid_import_sensor": DetectedSensor(
            "sensor.grid_import", "Battery Controller"
        ),
    }
    with patch(
        "custom_components.heating_curve_optimizer.config_flow.detect_main_flow_sensors",
        return_value=detected,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["step_id"] == "detected_integrations"
    options = result["data_schema"].schema["use_detected"].config["options"]
    assert options[0]["label"] == "Consumption price: sensor.decc_price (DECC)"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"use_detected": ["consumption_price_sensor"]}
    )
    assert result["step_id"] == "user"
    prices = result["data_schema"].schema["prices"].schema.schema
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in prices
        if key.description
    }
    assert suggested["consumption_price_sensor"] == "sensor.decc_price"


async def test_pv_import_takes_over_battery_controller_name(
    hass: HomeAssistant,
) -> None:
    from custom_components.heating_curve_optimizer.companion_integrations import (
        DetectedPvArray,
    )

    detected = [
        DetectedPvArray(
            name="Garage roof",
            peak_power_kwp=3.6,
            orientation=135.0,
            tilt=20.0,
            efficiency_factor=0.9,
            dc_coupled=True,
            source="Battery Controller",
        )
    ]
    entry = make_entry(zones=0)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.heating_curve_optimizer.config_flow.detect_pv_arrays",
        return_value=detected,
    ):
        result = await _start_subentry_flow(hass, entry, PV_SUBENTRY_TYPE)
        assert result["step_id"] == "user"
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"import_choice": "0"}
        )
    assert result["step_id"] == "configure"
    fields = {str(key): key for key in result["data_schema"].schema}
    assert fields["name"].description["suggested_value"] == "Garage roof"
    assert fields["peak_power_kwp"].description["suggested_value"] == 3.6

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Garage roof",
            "peak_power_kwp": 3.6,
            "orientation": 135,
            "tilt": 20,
            "efficiency_factor": 0.9,
            "dc_coupled": True,
        },
    )
    assert result["title"] == "Garage roof"
