"""End-to-end setup tests: real coordinators, mocked open-meteo and sensors."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.heating_curve_optimizer.const import DOMAIN

from .conftest import INDOOR_SENSOR, POWER_SENSOR, make_entry, setup_entry


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


def _state(hass: HomeAssistant, platform: str, unique_id: str):
    return hass.states.get(_entity_id(hass, platform, unique_id))


async def test_full_setup_produces_a_plan(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    prefix = entry.entry_id
    offset = _state(hass, "sensor", f"{prefix}_heating_curve_offset")
    assert offset is not None and offset.state not in ("unknown", "unavailable")
    offsets = offset.attributes["offsets"]
    assert len(offsets) >= 20  # 24 h horizon, first step shortened
    assert all(-4 <= o <= 4 for o in offsets)

    indoor = _state(hass, "sensor", f"{prefix}_planned_indoor_temperature")
    assert 18.0 < float(indoor.state) < 22.0
    assert indoor.attributes["comfort_min"] == 19.7

    for key in (
        "outdoor_temperature",
        "calculated_supply_temperature",
        "heat_loss",
        "window_solar_gain",
        "net_heat_loss",
        "optimized_supply_temperature",
        "heat_buffer",
        "planned_cop",
        "cost_savings_forecast",
        "total_cost_savings",
    ):
        state = _state(hass, "sensor", f"{prefix}_{key}")
        assert state.state not in ("unknown", "unavailable"), key

    assert _state(hass, "binary_sensor", f"{prefix}_heat_pump_demand") is not None
    assert _state(hass, "number", f"{prefix}_target_indoor_temp").state == "20.0"


async def test_translated_entity_names(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    state = _state(hass, "sensor", f"{entry.entry_id}_heating_curve_offset")
    assert (
        state.attributes["friendly_name"]
        == "Heating Curve Optimizer Heating curve offset"
    )


async def test_setup_without_zones_only_exposes_weather_sensors(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry(zones=0)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.optimization_coordinator is None
    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert unique_ids == {
        f"{entry.entry_id}_outdoor_temperature",
        f"{entry.entry_id}_calculated_supply_temperature",
    }


async def test_target_change_reaches_optimizer_without_reload(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    """Setpoints from the number entity must change the optimizer's comfort
    band right away (the review found them ignored until a restart)."""
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator

    number_id = _entity_id(hass, "number", f"{entry.entry_id}_target_indoor_temp")
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 21.5}, blocking=True
    )
    await hass.async_block_till_done()
    await coordinator.async_refresh()

    assert entry.runtime_data.optimization_coordinator is coordinator  # no reload
    assert coordinator.data["comfort_min"] == 21.2
    assert coordinator.data["comfort_max"] == 22.0


async def test_indoor_sensor_unavailable_falls_back_to_target_and_raises_issue(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    hass.states.async_set(INDOOR_SENSOR, "unavailable")
    entry = make_entry()
    await setup_entry(hass, entry)

    heat = entry.runtime_data.heat_coordinator
    assert heat.data["indoor_temperature"] == 20.0
    assert heat.data["indoor_temperature_source"] == "target_fallback"
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"indoor_sensor_unavailable_{entry.entry_id}"
    )
    assert issue is not None

    hass.states.async_set(INDOOR_SENSOR, "20.4")
    await heat.async_refresh()
    assert heat.data["indoor_temperature_source"] == "sensor"
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"indoor_sensor_unavailable_{entry.entry_id}"
        )
        is None
    )


async def test_price_sensor_unavailable_raises_repair_issue(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    hass.states.async_set("sensor.electricity_price", "unavailable")
    coordinator = entry.runtime_data.optimization_coordinator
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"price_sensor_unavailable_{entry.entry_id}"
    )


async def test_additional_zone_and_gas_boiler_get_their_own_devices(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    hass.states.async_set("sensor.gas_price", "1.20")
    entry = make_entry(zones=2, gas=True, pv=True)
    await setup_entry(hass, entry)

    runtime = entry.runtime_data
    assert len(runtime.zones) == 1
    zone_subentry_id = next(iter(runtime.zones))
    zone_state = _state(
        hass, "sensor", f"{entry.entry_id}_{zone_subentry_id}_heating_curve_offset"
    )
    assert zone_state.state not in ("unknown", "unavailable")

    gas_state = _state(hass, "sensor", f"{entry.entry_id}_gas_boiler_gas_cost")
    assert float(gas_state.state) > 0
    pv_state = _state(hass, "sensor", f"{entry.entry_id}_pv_production_forecast")
    assert pv_state is not None


async def test_power_sensor_enables_thermal_sensors(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    hass.states.async_set(POWER_SENSOR, "1200", {"unit_of_measurement": "W"})
    entry = make_entry(power_consumption=POWER_SENSOR)
    await setup_entry(hass, entry)
    thermal = _state(hass, "sensor", f"{entry.entry_id}_heat_pump_thermal_power")
    assert float(thermal.state) > 1.2  # COP > 1
    energy = _state(hass, "sensor", f"{entry.entry_id}_heat_pump_thermal_energy")
    assert energy is not None


async def test_structural_option_change_reloads(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    before = entry.runtime_data.optimization_coordinator
    hass.config_entries.async_update_entry(entry, options={"planning_window": 12})
    await hass.async_block_till_done()
    assert entry.runtime_data.optimization_coordinator is not before


async def test_unload(hass: HomeAssistant, mock_open_meteo, price_state) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.services.has_service(DOMAIN, "reset_thermal_calibration")


async def test_calibration_and_accuracy_sensors(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry(power_consumption=POWER_SENSOR)
    hass.states.async_set(POWER_SENSOR, "1200", {"unit_of_measurement": "W"})
    await setup_entry(hass, entry)
    calibration = _state(hass, "sensor", f"{entry.entry_id}_calibration")
    assert calibration.state == "collecting"
    assert calibration.attributes["samples_needed"] == 30
    assert calibration.attributes["heat_source"] == "cop_model"
    accuracy = _state(hass, "sensor", f"{entry.entry_id}_model_accuracy")
    assert accuracy is not None


async def test_repair_flow_switches_zone_to_apply(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    from homeassistant.setup import async_setup_component

    from custom_components.heating_curve_optimizer.repairs import (
        async_create_fix_flow,
    )

    assert await async_setup_component(hass, "repairs", {})
    entry = make_entry()
    await setup_entry(hass, entry)
    subentry_id = next(iter(entry.subentries))
    flow = await async_create_fix_flow(
        hass,
        f"calibration_ready_{entry.entry_id}",
        {"entry_id": entry.entry_id, "subentry_id": subentry_id},
    )
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["step_id"] == "confirm"
    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"
    assert entry.subentries[subentry_id].data["calibration_mode"] == "apply"
    # The fix flow schedules a reload; let it finish and unload, so no
    # first-refresh task of the reloaded entry outlives the test.
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _past_debounce(hass: HomeAssistant) -> None:
    """Let the coordinators' refresh debouncer (10 s cooldown) run."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()


def _issue(hass: HomeAssistant, issue_id: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


async def test_issues_clear_by_themselves_when_sensors_recover(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    indoor_issue = f"indoor_sensor_unavailable_{entry.entry_id}"
    price_issue = f"price_sensor_unavailable_{entry.entry_id}"

    hass.states.async_set(INDOOR_SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert _issue(hass, indoor_issue) is not None
    hass.states.async_set(INDOOR_SENSOR, "20.3")
    await _past_debounce(hass)
    assert _issue(hass, indoor_issue) is None

    attributes = dict(hass.states.get("sensor.electricity_price").attributes)
    hass.states.async_set("sensor.electricity_price", "unavailable")
    await entry.runtime_data.optimization_coordinator.async_refresh()
    assert _issue(hass, price_issue) is not None
    hass.states.async_set("sensor.electricity_price", "0.10", attributes)
    await _past_debounce(hass)
    assert _issue(hass, price_issue) is None


async def test_unload_removes_the_entry_issues(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    hass.states.async_set(INDOOR_SENSOR, "unavailable")
    entry = make_entry()
    await setup_entry(hass, entry)
    assert _issue(hass, f"indoor_sensor_unavailable_{entry.entry_id}") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, f"indoor_sensor_unavailable_{entry.entry_id}") is None


async def test_setpoints_are_text_boxes(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    for key in (
        "target_indoor_temp",
        "indoor_temp_hysteresis_lower",
        "indoor_temp_hysteresis_upper",
    ):
        assert (
            _state(hass, "number", f"{entry.entry_id}_{key}").attributes["mode"]
            == "box"
        )
