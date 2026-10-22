"""End-to-end setup tests: real coordinators, mocked open-meteo and sensors."""

from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EntityCategory
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.setup import async_setup_component

from custom_components.heating_curve_optimizer.const import DOMAIN

from .conftest import (
    GAS_PRICE_SENSOR,
    INDOOR_SENSOR,
    POWER_SENSOR,
    PRICE_SENSOR,
    make_entry,
    price_attributes,
    setup_entry,
)


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


def _state(hass: HomeAssistant, platform: str, unique_id: str):
    return hass.states.get(_entity_id(hass, platform, unique_id))


def _device(hass: HomeAssistant, entry, identifier: str) -> dr.DeviceEntry:
    """Look up one of the entry's devices on old and new Home Assistant.

    ``async_get_device_by_identifier`` only exists since 2026.10, where the
    older ``async_get_device(identifiers=...)`` is deprecated.
    """
    dev_reg = dr.async_get(hass)
    if hasattr(dev_reg, "async_get_device_by_identifier"):
        device = dev_reg.async_get_device_by_identifier(
            (DOMAIN, identifier), entry.entry_id
        )
    else:
        device = dev_reg.async_get_device(identifiers={(DOMAIN, identifier)})
    assert device is not None, identifier
    return device


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

    for key in (
        "outdoor_temperature",
        "calculated_supply_temperature",
        "heat_loss",
        "window_solar_gain",
        "net_heat_loss",
        "optimized_supply_temperature",
        "heat_buffer",
        "planned_cop",
    ):
        state = _state(hass, "sensor", f"{prefix}_{key}")
        assert state.state not in ("unknown", "unavailable"), key

    assert _state(hass, "number", f"{prefix}_target_indoor_temp").state == "20.0"


async def test_translated_entity_names(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    state = _state(hass, "sensor", f"{entry.entry_id}_heating_curve_offset")
    assert state.attributes["friendly_name"] == "Heating Curve Optimizer Planned offset"


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


async def test_restart_waits_for_source_sensors_without_errors(
    hass: HomeAssistant, mock_open_meteo, caplog, monkeypatch
) -> None:
    """After a restart the price/indoor/gas sensors belong to integrations
    that are still loading. That must not log errors or raise repair issues;
    the first plan and gas comparison run once the sensors report."""
    import custom_components.heating_curve_optimizer as hco

    monkeypatch.setattr(hco, "FIRST_REFRESH_DELAY_S", 0)
    monkeypatch.setattr(hco, "FIRST_REFRESH_POLL_S", 0.01)
    hass.set_state(CoreState.not_running)
    entry = make_entry(gas=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    runtime = entry.runtime_data
    assert runtime.optimization_coordinator.data is None
    assert not ir.async_get(hass).issues

    # Home Assistant finishes starting; the sources follow a moment later.
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert runtime.optimization_coordinator.data is None

    hass.states.async_set(INDOOR_SENSOR, "20.1", {"unit_of_measurement": "°C"})
    hass.states.async_set(PRICE_SENSOR, "0.10", price_attributes())
    hass.states.async_set(GAS_PRICE_SENSOR, "1.20")
    for _ in range(50):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()
        if runtime.gas_boiler_coordinator.data is not None:
            break

    assert runtime.optimization_coordinator.data is not None
    assert runtime.optimization_coordinator.data["initial_indoor_temp"] == 20.1
    assert runtime.gas_boiler_coordinator.data is not None
    assert not ir.async_get(hass).issues
    assert "Error fetching" not in caplog.text


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


async def test_hybrid_run_advice_and_cheaper_binary_sensors(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    hass.states.async_set("sensor.gas_price", "1.20")
    hass.states.async_set(POWER_SENSOR, "1200", {"unit_of_measurement": "W"})
    entry = make_entry(gas=True, power_consumption=POWER_SENSOR)
    await setup_entry(hass, entry)

    # Gas boiler: run advice + pure price comparison.
    gas_advice = _state(hass, "binary_sensor", f"{entry.entry_id}_gas_boiler_preferred")
    assert gas_advice is not None and gas_advice.state in ("on", "off")
    gas_cheaper = _state(hass, "binary_sensor", f"{entry.entry_id}_gas_boiler_cheaper")
    assert gas_cheaper is not None and gas_cheaper.state in ("on", "off")

    # Heat pump: plan-active advice already exists; the price comparison is
    # the exact mirror of the gas boiler's.
    hp_cheaper = _state(hass, "binary_sensor", f"{entry.entry_id}_heat_pump_cheaper")
    assert hp_cheaper is not None and hp_cheaper.state in ("on", "off")
    assert hp_cheaper.state != gas_cheaper.state

    # The heat pump's own cost per kWh lives on the heat-pump device; the
    # gas cost stays on the gas boiler's device.
    hp_device = _device(hass, entry, f"{entry.entry_id}_heat_pump")
    hp_cost_id = _entity_id(hass, "sensor", f"{entry.entry_id}_heat_pump_cost")
    hp_cost_entity = er.async_get(hass).async_get(hp_cost_id)
    assert hp_cost_entity is not None and hp_cost_entity.device_id == hp_device.id

    gas_device = _device(hass, entry, f"{entry.entry_id}_gas_boiler")
    assert gas_device.name == "Gas boiler"
    assert gas_advice.attributes["friendly_name"] == "Gas boiler Advised"
    gas_cost_id = _entity_id(hass, "sensor", f"{entry.entry_id}_gas_boiler_gas_cost")
    gas_cost_entity = er.async_get(hass).async_get(gas_cost_id)
    assert gas_cost_entity is not None and gas_cost_entity.device_id == gas_device.id


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

    # The measured heat-pump sensors live on their own child device.
    device = _device(hass, entry, f"{entry.entry_id}_heat_pump")
    thermal_id = _entity_id(hass, "sensor", f"{entry.entry_id}_heat_pump_thermal_power")
    registry_entity = er.async_get(hass).async_get(thermal_id)
    assert registry_entity is not None and registry_entity.device_id == device.id

    # The primary zone's plan-active advice also belongs to the heat pump.
    plan_id = _entity_id(
        hass, "binary_sensor", f"{entry.entry_id}_heat_pump_plan_active"
    )
    plan_entity = er.async_get(hass).async_get(plan_id)
    assert plan_entity is not None and plan_entity.device_id == device.id

    # The planned COP is a heat-pump property, so it lives there as well.
    cop_id = _entity_id(hass, "sensor", f"{entry.entry_id}_planned_cop")
    cop_entity = er.async_get(hass).async_get(cop_id)
    assert cop_entity is not None and cop_entity.device_id == device.id


async def test_heat_pump_device_exists_without_power_sensor(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    """The heat-pump entities use short names, so they must never fall back
    to the main device (where "Plan active" would be meaningless)."""
    entry = make_entry()
    await setup_entry(hass, entry)

    device = _device(hass, entry, f"{entry.entry_id}_heat_pump")
    assert device.name == "Heat pump"
    plan_id = _entity_id(
        hass, "binary_sensor", f"{entry.entry_id}_heat_pump_plan_active"
    )
    plan_entity = er.async_get(hass).async_get(plan_id)
    assert plan_entity is not None and plan_entity.device_id == device.id
    state = hass.states.get(plan_id)
    assert state is not None
    assert state.attributes["friendly_name"] == "Heat pump Plan active"


async def test_indoor_change_reoptimizes_without_waiting_for_the_poll(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    heat = entry.runtime_data.heat_coordinator
    before = coordinator.data["indoor_temps"]

    # A small change is ignored ...
    hass.states.async_set(INDOOR_SENSOR, "20.2", {"unit_of_measurement": "°C"})
    await heat.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["indoor_temps"] == before

    # ... a change of 0.5 K or more re-plans from the new starting point.
    hass.states.async_set(INDOOR_SENSOR, "21.5", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert coordinator.data["indoor_temps"] != before


async def test_savings_sensors_are_disabled_diagnostics_by_default(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)

    registry = er.async_get(hass)
    for key in (
        "cost_savings_forecast",
        "total_cost_savings",
        "planned_indoor_temperature",
    ):
        entity_id = _entity_id(hass, "sensor", f"{entry.entry_id}_{key}")
        entity = registry.async_get(entity_id)
        assert entity is not None
        assert entity.entity_category == EntityCategory.DIAGNOSTIC
        assert entity.disabled_by is not None
        assert hass.states.get(entity_id) is None  # disabled by default


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


async def test_replan_keeps_the_surplus_adjustment(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    """A new DP run must not drop the real-time offset back to the plan."""
    hass.states.async_set("sensor.grid", "0", {"unit_of_measurement": "W"})
    entry = make_entry(grid_import_sensor="sensor.grid")
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    coordinator._realtime_controller.reset(1)

    await coordinator.async_refresh()

    realtime = coordinator.data["realtime"]
    assert realtime["adjustment"] == 1
    assert realtime["planned_offset"] == coordinator.data["offset"]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_home_schedule_lowers_the_floor_while_away(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    """Steps that end while the home schedule is off get the away floor."""
    days = ("monday", "tuesday", "wednesday", "thursday", "friday")
    weekend = ("saturday", "sunday")
    block = [{"from": "18:00:00", "to": "22:00:00"}]
    assert await async_setup_component(
        hass,
        "schedule",
        {
            "schedule": {
                "home": {"name": "Home", **dict.fromkeys(days + weekend, block)}
            }
        },
    )
    entry = make_entry(
        zone_overrides={"comfort_schedule": "schedule.home", "away_setback": 2.5}
    )
    await setup_entry(hass, entry)
    data = entry.runtime_data.optimization_coordinator.data

    assert data["comfort_min"] == 19.7
    assert set(data["comfort_mins"]) == {19.7, 17.2}
    assert len(data["comfort_mins"]) == len(data["indoor_temps"])
    issues = ir.async_get(hass).issues
    assert (DOMAIN, f"comfort_schedule_unavailable_{entry.entry_id}") not in issues

    # The schedule helper's timer for its next switch outlives the test
    # unless the entity is removed.
    schedule = hass.data[DATA_INSTANCES]["schedule"].get_entity("schedule.home")
    assert schedule is not None
    await schedule.async_remove()


async def test_unreadable_home_schedule_keeps_the_full_band_and_raises_issue(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry(zone_overrides={"comfort_schedule": "schedule.missing"})
    await setup_entry(hass, entry)
    data = entry.runtime_data.optimization_coordinator.data

    assert set(data["comfort_mins"]) == {data["comfort_min"]}
    issue_id = f"comfort_schedule_unavailable_{entry.entry_id}"
    assert (DOMAIN, issue_id) in ir.async_get(hass).issues


async def test_battery_discharge_counts_as_grid_import(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    """A zero-grid battery covering the heat pump is not surplus: its
    discharge is added to the grid power the surplus layer judges."""
    hass.states.async_set("sensor.grid", "0", {"unit_of_measurement": "W"})
    hass.states.async_set(POWER_SENSOR, "1.2", {"unit_of_measurement": "kW"})
    # Batteries (positive = charging): one discharging 1200 W - covering
    # the heat pump - one charging, one unavailable.
    hass.states.async_set("sensor.battery_a", "-1200", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.battery_b", "0.2", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.battery_c", "unavailable")
    entry = make_entry(
        grid_import_sensor="sensor.grid",
        power_consumption=POWER_SENSOR,
        battery_power_sensors=[
            "sensor.battery_a",
            "sensor.battery_b",
            "sensor.battery_c",
        ],
    )
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    coordinator._realtime_controller.reset(1)

    await coordinator.async_refresh()

    realtime = coordinator.data["realtime"]
    assert realtime["battery_discharge_w"] == 1200.0
    assert realtime["current_grid_w"] == 1200.0
    # Without the heat pump (and the battery covering it) nothing would be
    # exported: no surplus, the adjustment steps down.
    assert realtime["adjustment"] == 0


async def test_plan_driven_pump_holds_its_advice_for_the_minimum(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    """With "follows the plan" the published on/off advice never changes
    within the minimum run or pause time, whatever a re-plan says."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    entry = make_entry(
        heat_pump_control="follows_plan", min_run_minutes=60, min_off_minutes=60
    )
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    data = coordinator.data
    assert data["heat_pump_control_follows_plan"] is True
    assert data["heat_pump_plan_on"] is not None
    assert data["heat_pump_plan_hold_until"] is not None

    # Pretend the advice flipped to "off" ten minutes ago: a re-plan must
    # keep it off until the hour is over.
    since = dt_util.utcnow() - timedelta(minutes=10)
    coordinator._plan_on = False
    coordinator._plan_on_since = since
    await coordinator.async_refresh()
    assert coordinator.data["heat_pump_plan_on"] is False
    assert (
        coordinator.data["heat_pump_plan_hold_until"]
        == (since + timedelta(minutes=60)).isoformat()
    )
    state = _state(hass, "binary_sensor", f"{entry.entry_id}_heat_pump_plan_active")
    assert state.state == "off"
    assert state.attributes["hold_until"] is not None


async def test_pump_cycle_starts_from_the_published_advice(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    hass.states.async_set(POWER_SENSOR, "0.05", {"unit_of_measurement": "kW"})
    entry = make_entry(
        heat_pump_control="follows_plan",
        min_run_minutes=60,
        min_off_minutes=30,
        power_consumption=POWER_SENSOR,
    )
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    now = dt_util.utcnow()

    # After a restart: the measured (idle) heat pump, free to switch.
    coordinator._plan_on = None
    cycle = coordinator._pump_cycle(15, now)
    assert cycle == {
        "pump_follows_plan": True,
        "min_on_steps": 4,
        "min_off_steps": 2,
        "initial_pump_on": False,
    }
    # On for 20 minutes: 40 more minutes = 3 steps, so 1 step counts as done.
    coordinator._plan_on = True
    coordinator._plan_on_since = now - timedelta(minutes=20)
    cycle = coordinator._pump_cycle(15, now)
    assert cycle["initial_pump_on"] is True
    assert cycle["initial_mode_steps"] == 1


async def test_own_thermostat_is_the_default(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    assert coordinator.data["heat_pump_control_follows_plan"] is False
    assert coordinator.data["heat_pump_plan_hold_until"] is None
    assert coordinator._pump_cycle(15, coordinator.data["timestamp"]) == {
        "pump_follows_plan": False
    }


async def test_flow_sensor_measures_the_delivered_heat(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    """Flow x supply-return spread replaces the COP model: for the thermal
    power sensor (with a measured COP) and as the calibration heat source."""
    hass.states.async_set(POWER_SENSOR, "1000", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.supply", "33.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.return", "28.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.flow", "15", {"unit_of_measurement": "L/min"})
    entry = make_entry(
        power_consumption=POWER_SENSOR,
        supply_temperature_sensor="sensor.supply",
        return_temperature_sensor="sensor.return",
        flow_sensor="sensor.flow",
    )
    await setup_entry(hass, entry)

    thermal = _state(hass, "sensor", f"{entry.entry_id}_heat_pump_thermal_power")
    # 15 L/min = 0.25 L/s x 4.163 kJ/(L K) x 5 K = 5.20 kW
    assert float(thermal.state) == 5.204
    assert thermal.attributes["source"] == "measured"
    assert thermal.attributes["cop"] == 5.204
    calibration = entry.runtime_data.optimization_coordinator.data["calibration"]
    assert calibration["heat_source"] == "measured"

    # Flow in m³/h converts too; the sensor follows the flow sensor.
    hass.states.async_set("sensor.flow", "0.9", {"unit_of_measurement": "m³/h"})
    await hass.async_block_till_done()
    thermal = _state(hass, "sensor", f"{entry.entry_id}_heat_pump_thermal_power")
    assert float(thermal.state) == 5.204


async def test_heat_meter_feeds_the_thermal_power_sensor(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    hass.states.async_set(POWER_SENSOR, "1000", {"unit_of_measurement": "W"})
    hass.states.async_set(
        "sensor.heat_counter", "500.0", {"unit_of_measurement": "kWh"}
    )
    entry = make_entry(
        power_consumption=POWER_SENSOR,
        heat_pump_thermal_energy_sensor="sensor.heat_counter",
    )
    await setup_entry(hass, entry)
    calibration = entry.runtime_data.optimization_coordinator.data["calibration"]
    assert calibration["heat_source"] == "measured"
    thermal_id = _entity_id(hass, "sensor", f"{entry.entry_id}_heat_pump_thermal_power")
    # A single reading gives no rate yet: the sensor waits.
    assert hass.states.get(thermal_id).state == "unavailable"
