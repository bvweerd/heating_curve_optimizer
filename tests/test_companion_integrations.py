"""Tests for companion_integrations.py - detecting battery_controller /
dynamic_energy_contract_calculator settings during setup."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_curve_optimizer.companion_integrations import (
    BATTERY_CONTROLLER_DOMAIN,
    DECC_DOMAIN,
    FIELD_CONSUMPTION_PRICE_SENSOR,
    FIELD_GRID_EXPORT_SENSOR,
    FIELD_GRID_IMPORT_SENSOR,
    FIELD_POWER_CONSUMPTION,
    FIELD_PRODUCTION_PRICE_SENSOR,
    detect_gas_price_sensor,
    detect_main_flow_sensors,
)


def _register_decc_sensor(hass: HomeAssistant, suffix: str, entity_id: str) -> None:
    registry = er.async_get(hass)
    object_id = entity_id.split(".", 1)[1]
    registry.async_get_or_create(
        "sensor",
        DECC_DOMAIN,
        f"{DECC_DOMAIN}_{suffix}",
        suggested_object_id=object_id,
    )


@pytest.mark.asyncio
async def test_no_companion_integrations_returns_empty(hass: HomeAssistant):
    assert detect_main_flow_sensors(hass) == {}
    assert detect_gas_price_sensor(hass) is None


@pytest.mark.asyncio
async def test_battery_controller_only_uses_raw_price_sensors(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={
            "power_consumption_sensors": ["sensor.hp_power"],
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
            "price_sensor": "sensor.raw_price",
            "feed_in_price_sensor": "sensor.raw_feed_in_price",
        },
    )
    entry.add_to_hass(hass)

    detected = detect_main_flow_sensors(hass)

    assert detected[FIELD_POWER_CONSUMPTION].entity_id == "sensor.hp_power"
    assert detected[FIELD_GRID_IMPORT_SENSOR].entity_id == "sensor.grid_import"
    assert detected[FIELD_GRID_EXPORT_SENSOR].entity_id == "sensor.grid_export"
    assert detected[FIELD_CONSUMPTION_PRICE_SENSOR].entity_id == "sensor.raw_price"
    assert (
        detected[FIELD_PRODUCTION_PRICE_SENSOR].entity_id == "sensor.raw_feed_in_price"
    )
    assert detected[FIELD_POWER_CONSUMPTION].source == "Battery Controller"


@pytest.mark.asyncio
async def test_battery_controller_falls_back_to_consumption_sensors_list(
    hass: HomeAssistant,
):
    """power_consumption_sensors absent -> falls back to
    electricity_consumption_sensors."""
    entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={"electricity_consumption_sensors": ["sensor.elec_consumption"]},
    )
    entry.add_to_hass(hass)

    detected = detect_main_flow_sensors(hass)

    assert detected[FIELD_POWER_CONSUMPTION].entity_id == "sensor.elec_consumption"


@pytest.mark.asyncio
async def test_decc_price_sensors_preferred_over_battery_controller(
    hass: HomeAssistant,
):
    bc_entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={"price_sensor": "sensor.raw_price"},
    )
    bc_entry.add_to_hass(hass)
    decc_entry = MockConfigEntry(domain=DECC_DOMAIN, data={})
    decc_entry.add_to_hass(hass)
    _register_decc_sensor(
        hass, "current_consumption_price", "sensor.current_consumption_price"
    )
    _register_decc_sensor(
        hass, "current_production_price", "sensor.current_production_price"
    )

    detected = detect_main_flow_sensors(hass)

    assert (
        detected[FIELD_CONSUMPTION_PRICE_SENSOR].entity_id
        == "sensor.current_consumption_price"
    )
    assert detected[FIELD_CONSUMPTION_PRICE_SENSOR].source == (
        "Dynamic Energy Contract Calculator"
    )
    assert (
        detected[FIELD_PRODUCTION_PRICE_SENSOR].entity_id
        == "sensor.current_production_price"
    )


@pytest.mark.asyncio
async def test_decc_configured_without_gas_source_has_no_gas_candidate(
    hass: HomeAssistant,
):
    decc_entry = MockConfigEntry(domain=DECC_DOMAIN, data={})
    decc_entry.add_to_hass(hass)
    _register_decc_sensor(
        hass, "current_consumption_price", "sensor.current_consumption_price"
    )
    # No current_gas_consumption_price entity registered - gas source was
    # never configured in DECC.

    assert detect_gas_price_sensor(hass) is None


@pytest.mark.asyncio
async def test_detect_gas_price_sensor_found(hass: HomeAssistant):
    decc_entry = MockConfigEntry(domain=DECC_DOMAIN, data={})
    decc_entry.add_to_hass(hass)
    _register_decc_sensor(
        hass, "current_gas_consumption_price", "sensor.current_gas_consumption_price"
    )

    detected = detect_gas_price_sensor(hass)

    assert detected is not None
    assert detected.entity_id == "sensor.current_gas_consumption_price"
    assert detected.source == "Dynamic Energy Contract Calculator"


@pytest.mark.asyncio
async def test_options_override_data_for_battery_controller(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={"price_sensor": "sensor.old_price"},
        options={"price_sensor": "sensor.new_price"},
    )
    entry.add_to_hass(hass)

    detected = detect_main_flow_sensors(hass)

    assert detected[FIELD_CONSUMPTION_PRICE_SENSOR].entity_id == "sensor.new_price"
