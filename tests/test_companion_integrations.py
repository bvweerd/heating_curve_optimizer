"""Tests for companion_integrations.py - detecting battery_controller /
dynamic_energy_contract_calculator settings during setup."""

from types import SimpleNamespace

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
    FIELD_SOURCES_CONSUMPTION,
    FIELD_SOURCES_PRODUCTION,
    detect_gas_price_sensor,
    detect_main_flow_sensors,
    detect_pv_arrays,
    detect_source_sensors,
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
async def test_battery_controller_ignores_migrated_away_consumption_field(
    hass: HomeAssistant,
):
    """electricity_consumption_sensors is a pre-v6 field battery_controller
    itself migrates away automatically (see its async_migrate_entry) - it
    no longer exists on any real, current install, so it must not be read
    as a fallback for power_consumption. Only power_consumption_sensors
    (the current field) is a valid source."""
    entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={"electricity_consumption_sensors": ["sensor.elec_consumption"]},
    )
    entry.add_to_hass(hass)

    detected = detect_main_flow_sensors(hass)

    assert FIELD_POWER_CONSUMPTION not in detected


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


def _decc_source_subentry(source_type: str, sources: list[str]) -> SimpleNamespace:
    """A fake DECC source subentry - just needs a `.data` dict with the same
    key/value shape DECC's real ConfigSubentry objects carry."""
    return SimpleNamespace(data={"source_type": source_type, "sources": sources})


@pytest.mark.asyncio
async def test_no_companion_integrations_has_no_source_candidates(
    hass: HomeAssistant,
):
    assert detect_source_sensors(hass) == {}


@pytest.mark.asyncio
async def test_decc_source_subentries_detected_per_source_type(hass: HomeAssistant):
    decc_entry = MockConfigEntry(domain=DECC_DOMAIN, data={})
    decc_entry.add_to_hass(hass)
    decc_entry.subentries = {
        "sub1": _decc_source_subentry(
            "Electricity consumption", ["sensor.consumption_kwh"]
        ),
        "sub2": _decc_source_subentry(
            "Electricity production", ["sensor.production_kwh"]
        ),
        "sub3": _decc_source_subentry("Gas consumption", ["sensor.gas_m3"]),
    }

    detected = detect_source_sensors(hass)

    assert detected[FIELD_SOURCES_CONSUMPTION].entity_ids == ["sensor.consumption_kwh"]
    assert detected[FIELD_SOURCES_CONSUMPTION].source == (
        "Dynamic Energy Contract Calculator"
    )
    assert detected[FIELD_SOURCES_PRODUCTION].entity_ids == ["sensor.production_kwh"]


@pytest.mark.asyncio
async def test_decc_only_consumption_configured_falls_back_for_production(
    hass: HomeAssistant,
):
    """DECC has a consumption source subentry but no production one -
    production falls back to battery_controller's flat list."""
    decc_entry = MockConfigEntry(domain=DECC_DOMAIN, data={})
    decc_entry.add_to_hass(hass)
    decc_entry.subentries = {
        "sub1": _decc_source_subentry(
            "Electricity consumption", ["sensor.consumption_kwh"]
        ),
    }
    bc_entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={"pv_production_sensors": ["sensor.pv_production"]},
    )
    bc_entry.add_to_hass(hass)

    detected = detect_source_sensors(hass)

    assert detected[FIELD_SOURCES_CONSUMPTION].entity_ids == ["sensor.consumption_kwh"]
    assert detected[FIELD_SOURCES_CONSUMPTION].source == (
        "Dynamic Energy Contract Calculator"
    )
    assert detected[FIELD_SOURCES_PRODUCTION].entity_ids == ["sensor.pv_production"]
    assert detected[FIELD_SOURCES_PRODUCTION].source == "Battery Controller"


@pytest.mark.asyncio
async def test_battery_controller_only_detects_both_source_lists(
    hass: HomeAssistant,
):
    bc_entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={
            "grid_import_sensors": ["sensor.consumption"],
            "grid_export_sensors": ["sensor.production"],
        },
    )
    bc_entry.add_to_hass(hass)

    detected = detect_source_sensors(hass)

    assert detected[FIELD_SOURCES_CONSUMPTION].entity_ids == ["sensor.consumption"]
    assert detected[FIELD_SOURCES_PRODUCTION].entity_ids == ["sensor.production"]
    assert detected[FIELD_SOURCES_CONSUMPTION].source == "Battery Controller"


@pytest.mark.asyncio
async def test_battery_controller_consumption_unions_grid_import_and_gross_load(
    hass: HomeAssistant,
):
    """Both fields represent metered consumption from a different vantage
    point (see battery_controller's own v5->v6 migration comment) - both
    are valid candidates, unioned rather than one taking exclusive
    precedence."""
    bc_entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={
            "grid_import_sensors": ["sensor.grid_import"],
            "gross_load_sensors": ["sensor.gross_load"],
        },
    )
    bc_entry.add_to_hass(hass)

    detected = detect_source_sensors(hass)

    assert detected[FIELD_SOURCES_CONSUMPTION].entity_ids == [
        "sensor.grid_import",
        "sensor.gross_load",
    ]


@pytest.mark.asyncio
async def test_battery_controller_production_includes_pv_array_subentries(
    hass: HomeAssistant,
):
    """Each PV array is its own subentry in battery_controller (added via
    the integration page after setup, like this integration's own zone/
    gas-boiler subentries) - its measured-production sensor lives only
    there, not in entry.data/entry.options, so it needs its own subentry
    read to show up as a production candidate at all."""
    bc_entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={"grid_export_sensors": ["sensor.grid_export"]},
    )
    bc_entry.add_to_hass(hass)
    bc_entry.subentries = {
        "sub1": SimpleNamespace(
            subentry_type="pv_array",
            data={"pv_measured_production_sensor": "sensor.pv_east_production"},
        ),
        "sub2": SimpleNamespace(
            subentry_type="pv_array",
            data={"pv_measured_production_sensor": "sensor.pv_west_production"},
        ),
        "sub3": SimpleNamespace(
            subentry_type="battery",
            data={"capacity_kwh": 10.0},
        ),
    }

    detected = detect_source_sensors(hass)

    assert detected[FIELD_SOURCES_PRODUCTION].entity_ids == [
        "sensor.grid_export",
        "sensor.pv_east_production",
        "sensor.pv_west_production",
    ]
    assert detected[FIELD_SOURCES_PRODUCTION].source == "Battery Controller"


@pytest.mark.asyncio
async def test_battery_controller_pv_subentries_alone_are_enough_for_production(
    hass: HomeAssistant,
):
    """No flat production field at all - PV-array subentries alone must
    still be picked up, not just as an addition to an existing list."""
    bc_entry = MockConfigEntry(domain=BATTERY_CONTROLLER_DOMAIN, data={})
    bc_entry.add_to_hass(hass)
    bc_entry.subentries = {
        "sub1": SimpleNamespace(
            subentry_type="pv_array",
            data={"pv_measured_production_sensor": "sensor.pv_south_production"},
        ),
    }

    detected = detect_source_sensors(hass)

    assert detected[FIELD_SOURCES_PRODUCTION].entity_ids == [
        "sensor.pv_south_production"
    ]


@pytest.mark.asyncio
async def test_decc_subentry_missing_sources_key_is_skipped_not_crashed(
    hass: HomeAssistant,
):
    """A subentry with the right source_type but a malformed/empty sources
    list must be skipped cleanly, not raise."""
    decc_entry = MockConfigEntry(domain=DECC_DOMAIN, data={})
    decc_entry.add_to_hass(hass)
    decc_entry.subentries = {
        "sub1": SimpleNamespace(data={"source_type": "Electricity consumption"}),
        "sub2": _decc_source_subentry("Electricity consumption", []),
    }

    assert detect_source_sensors(hass) == {}


@pytest.mark.asyncio
async def test_detect_pv_arrays_returns_empty_without_battery_controller(
    hass: HomeAssistant,
):
    assert detect_pv_arrays(hass) == []


@pytest.mark.asyncio
async def test_detect_pv_arrays_reads_physical_parameters(hass: HomeAssistant):
    """The array's own peak_power_kwp/orientation/tilt/efficiency_factor/
    dc_coupled must be read directly - field names are a straight match
    with this integration's own PV-array subentry schema (see
    DetectedPvArray's docstring), not remapped."""
    bc_entry = MockConfigEntry(domain=BATTERY_CONTROLLER_DOMAIN, data={})
    bc_entry.add_to_hass(hass)
    bc_entry.subentries = {
        "sub1": SimpleNamespace(
            subentry_type="pv_array",
            title="East roof",
            data={
                "peak_power_kwp": 4.2,
                "orientation": 90.0,
                "tilt": 30.0,
                "efficiency_factor": 0.9,
                "dc_coupled": True,
                # battery_controller-only fields, never copied over:
                "pv_measured_production_sensor": "sensor.pv_east_production",
                "pv_forecast_sensors": ["sensor.pv_east_forecast"],
            },
        ),
    }

    detected = detect_pv_arrays(hass)

    assert len(detected) == 1
    array = detected[0]
    assert array.name == "East roof"
    assert array.peak_power_kwp == 4.2
    assert array.orientation == 90.0
    assert array.tilt == 30.0
    assert array.efficiency_factor == 0.9
    assert array.dc_coupled is True
    assert array.source == "Battery Controller"


@pytest.mark.asyncio
async def test_detect_pv_arrays_returns_one_per_subentry_and_ignores_others(
    hass: HomeAssistant,
):
    bc_entry = MockConfigEntry(domain=BATTERY_CONTROLLER_DOMAIN, data={})
    bc_entry.add_to_hass(hass)
    bc_entry.subentries = {
        "sub1": SimpleNamespace(
            subentry_type="pv_array",
            title="South roof",
            data={"peak_power_kwp": 3.0, "orientation": 180.0, "tilt": 35.0},
        ),
        "sub2": SimpleNamespace(
            subentry_type="pv_array",
            title="West roof",
            data={"peak_power_kwp": 2.5, "orientation": 270.0, "tilt": 35.0},
        ),
        "sub3": SimpleNamespace(
            subentry_type="battery",
            title="Home battery",
            data={"capacity_kwh": 10.0},
        ),
    }

    detected = detect_pv_arrays(hass)

    assert [array.name for array in detected] == ["South roof", "West roof"]


@pytest.mark.asyncio
async def test_detect_pv_arrays_fills_defaults_for_missing_optional_fields(
    hass: HomeAssistant,
):
    """Only peak_power_kwp is required by battery_controller's own schema -
    a subentry missing the rest must still be detected, using this
    integration's own defaults rather than being skipped entirely."""
    bc_entry = MockConfigEntry(domain=BATTERY_CONTROLLER_DOMAIN, data={})
    bc_entry.add_to_hass(hass)
    bc_entry.subentries = {
        "sub1": SimpleNamespace(
            subentry_type="pv_array",
            title="Minimal array",
            data={"peak_power_kwp": 1.5},
        ),
    }

    detected = detect_pv_arrays(hass)

    assert len(detected) == 1
    array = detected[0]
    assert array.orientation == 180.0
    assert array.tilt == 35.0
    assert array.efficiency_factor == 0.85
    assert array.dc_coupled is False


@pytest.mark.asyncio
async def test_detect_pv_arrays_skips_subentry_missing_peak_power(
    hass: HomeAssistant,
):
    """A malformed subentry (no peak_power_kwp at all) must be skipped, not
    crash detection for every other array - a fabricated peak power would
    silently poison the forecast."""
    bc_entry = MockConfigEntry(domain=BATTERY_CONTROLLER_DOMAIN, data={})
    bc_entry.add_to_hass(hass)
    bc_entry.subentries = {
        "sub1": SimpleNamespace(
            subentry_type="pv_array",
            title="Broken array",
            data={"orientation": 180.0},
        ),
        "sub2": SimpleNamespace(
            subentry_type="pv_array",
            title="Valid array",
            data={"peak_power_kwp": 2.0},
        ),
    }

    detected = detect_pv_arrays(hass)

    assert [array.name for array in detected] == ["Valid array"]
