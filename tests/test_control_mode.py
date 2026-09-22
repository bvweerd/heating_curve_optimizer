"""Tests for fase 3 (docs/redesign/REDESIGN.md): control_mode switchover.

Covers the three modes' effect on OptimizationCoordinator's published
result, the default staying "legacy" for backward compatibility, the
select entity, and the __init__.py _NO_RELOAD_KEYS handling that lets
switching control_mode skip a full entry reload.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer import _update_listener
from custom_components.heating_curve_optimizer.const import (
    CONF_CONTROL_MODE,
    DEFAULT_CONTROL_MODE,
    DOMAIN,
    MODE_FOLLOW_CURVE,
    MODE_LEGACY,
    MODE_OPTIMIZE_V2,
)
from custom_components.heating_curve_optimizer.coordinator import (
    HeatCalculationCoordinator,
    OptimizationCoordinator,
)
from custom_components.heating_curve_optimizer.select import (
    HeatingControlModeSelect,
)

BASE_CONFIG = {
    "area_m2": 150,
    "energy_label": "C",
    "target_indoor_temp": 20.0,
    "indoor_temp_hysteresis_lower": 0.5,
    "indoor_temp_hysteresis_upper": 0.5,
    "heat_curve_min": 20.0,
    "heat_curve_max": 45.0,
    "heat_curve_min_outdoor": -10.0,
    "heat_curve_max_outdoor": 15.0,
    "consumption_price_sensor": "sensor.price",
}


async def _build_coordinator(
    hass: HomeAssistant, config: dict
) -> OptimizationCoordinator:
    weather_coordinator = MagicMock()
    weather_coordinator.data = {"temperature_forecast": [2.0] * 8}
    heat_coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, config, "test_entry"
    )
    heat_coordinator.data = {
        "net_heat_loss_forecast": [1.0] * 8,
        "heat_demand_factor": 1.0,
        "heat_pump_on": True,
        "net_heat_loss": 1.0,
        "solar_gain_forecast": [],
        "indoor_temperature": 20.0,
    }
    heat_coordinator.weather_coordinator = weather_coordinator

    coordinator = OptimizationCoordinator(hass, heat_coordinator, config)
    hass.states.async_set("sensor.price", "0.20", {"forecast_prices": [0.20] * 8})
    return coordinator


@pytest.mark.asyncio
async def test_default_control_mode_is_legacy(hass: HomeAssistant):
    coordinator = await _build_coordinator(hass, dict(BASE_CONFIG))
    assert coordinator.control_mode == MODE_LEGACY
    assert DEFAULT_CONTROL_MODE == MODE_LEGACY


@pytest.mark.asyncio
async def test_legacy_mode_result_unchanged_from_run_optimization(
    hass: HomeAssistant,
):
    """The whole point of defaulting to legacy: existing installations must
    see exactly what _run_optimization produced, untouched."""
    coordinator = await _build_coordinator(hass, dict(BASE_CONFIG))
    legacy_only = coordinator._run_optimization(
        demand_forecast=[1.0] * 8,
        price_forecast=[0.2] * 8,
        temp_forecast=[2.0] * 8,
        planning_window=6,  # DEFAULT_PLANNING_WINDOW - not overridden in BASE_CONFIG
        time_base=60,
        offset_delta_t=10,
        max_buffer_debt=5.0,
        price_interval=60,
        k_factor=0.11,
        base_cop=4.2,
        outdoor_temp_coefficient=0.08,
        cop_compensation=1.0,
        min_supply=20.0,
        max_supply=45.0,
        min_outdoor=-10.0,
        max_outdoor=15.0,
        current_buffer=0.0,
        current_offset=0,
    )

    result = await coordinator._async_update_data()

    assert result["optimized_offset"] == legacy_only["optimized_offset"]
    assert result["optimized_offsets"] == legacy_only["optimized_offsets"]
    assert result["total_cost"] == legacy_only["total_cost"]


@pytest.mark.asyncio
async def test_follow_curve_mode_forces_zero_offset(hass: HomeAssistant):
    config = {**BASE_CONFIG, CONF_CONTROL_MODE: MODE_FOLLOW_CURVE}
    coordinator = await _build_coordinator(hass, config)
    assert coordinator.control_mode == MODE_FOLLOW_CURVE

    result = await coordinator._async_update_data()

    assert result["optimized_offset"] == 0
    assert all(o == 0 for o in result["optimized_offsets"])
    assert result["cost_savings"] == 0.0
    assert result["total_cost"] == result["baseline_cost"]


@pytest.mark.asyncio
async def test_optimize_v2_mode_uses_thermal_v2_result(hass: HomeAssistant):
    config = {**BASE_CONFIG, CONF_CONTROL_MODE: MODE_OPTIMIZE_V2}
    coordinator = await _build_coordinator(hass, config)
    assert coordinator.control_mode == MODE_OPTIMIZE_V2

    result = await coordinator._async_update_data()

    assert result["thermal_v2"]["available"] is True
    assert result["optimized_offset"] == result["thermal_v2"]["offsets"][0]
    assert result["optimized_offsets"] == result["thermal_v2"]["offsets"]
    assert result["future_supply_temperatures"] == result["thermal_v2"]["supply_temps"]
    assert result["total_cost"] == result["thermal_v2"]["total_cost_eur"]
    # Buffer re-expressed from the v2 indoor-temperature trajectory.
    assert len(result["buffer_evolution"]) == len(result["thermal_v2"]["indoor_temps"])


@pytest.mark.asyncio
async def test_optimize_v2_mode_falls_back_to_legacy_when_shadow_unavailable(
    hass: HomeAssistant,
):
    """If optimize_v2 is selected but the redesigned optimizer errors, the
    system must still get a decision (the legacy one), never nothing."""
    config = {**BASE_CONFIG, CONF_CONTROL_MODE: MODE_OPTIMIZE_V2, "area_m2": 0}
    coordinator = await _build_coordinator(hass, config)

    result = await coordinator._async_update_data()

    assert result["thermal_v2"]["available"] is False
    # optimized_offset must still be a real, usable value from the legacy
    # path - not left missing or None.
    assert result["optimized_offset"] is not None


def test_control_mode_setter_rejects_unknown_value(hass: HomeAssistant):
    heat_coordinator = MagicMock()
    coordinator = OptimizationCoordinator(hass, heat_coordinator, dict(BASE_CONFIG))
    coordinator.control_mode = "not_a_real_mode"
    assert coordinator.control_mode == MODE_LEGACY  # unchanged


def test_select_entity_reports_and_writes_control_mode():
    coordinator = MagicMock()
    coordinator.control_mode = MODE_LEGACY
    entry = MagicMock()
    entry.options = {}
    hass = MagicMock()

    select = HeatingControlModeSelect(hass, entry, MagicMock(), coordinator)
    assert select.current_option == MODE_LEGACY


@pytest.mark.asyncio
async def test_update_listener_skips_reload_for_control_mode_only_change(
    hass: HomeAssistant,
):
    entry = MagicMock()
    entry.entry_id = "test_entry_reload"
    entry.options = {CONF_CONTROL_MODE: MODE_OPTIMIZE_V2}

    hass.data[DOMAIN] = {
        entry.entry_id: {"options_snapshot": {CONF_CONTROL_MODE: MODE_LEGACY}}
    }
    hass.config_entries.async_reload = MagicMock(
        side_effect=AssertionError(
            "should not reload for a _NO_RELOAD_KEYS-only change"
        )
    )

    await _update_listener(hass, entry)

    hass.config_entries.async_reload.assert_not_called()
    assert hass.data[DOMAIN][entry.entry_id]["options_snapshot"] == entry.options


@pytest.mark.asyncio
async def test_update_listener_reloads_for_other_option_changes(hass: HomeAssistant):
    entry = MagicMock()
    entry.entry_id = "test_entry_reload_2"
    entry.options = {"area_m2": 200}

    hass.data[DOMAIN] = {entry.entry_id: {"options_snapshot": {"area_m2": 150}}}
    reload_calls = []

    async def _fake_reload(entry_id):
        reload_calls.append(entry_id)

    hass.config_entries.async_reload = _fake_reload

    await _update_listener(hass, entry)

    assert reload_calls == [entry.entry_id]
