"""Coordinator-level tests for phase 5b real-time controller wiring
(OptimizationCoordinator._get_realtime_grid_w / _handle_realtime_update),
as distinct from the pure-logic tests in test_realtime_controller.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.coordinator import (
    HeatCalculationCoordinator,
    OptimizationCoordinator,
)
from custom_components.heating_curve_optimizer.realtime_controller import (
    RealtimeController,
    RealtimeControllerConfig,
)

CONFIG = {
    "area_m2": 150,
    "energy_label": "C",
    "target_indoor_temp": 20.0,
    "indoor_temp_hysteresis_lower": 0.5,
    "indoor_temp_hysteresis_upper": 0.5,
    "heat_curve_min": 20.0,
    "heat_curve_max": 45.0,
    "heat_curve_min_outdoor": -10.0,
    "heat_curve_max_outdoor": 15.0,
    "control_mode": "optimize_v2",
    "grid_import_sensor": "sensor.grid_import",
    "grid_export_sensor": "sensor.grid_export",
}


def _make_coordinator(hass: HomeAssistant) -> OptimizationCoordinator:
    heat_coordinator = MagicMock()
    coordinator = OptimizationCoordinator(hass, heat_coordinator, CONFIG)
    coordinator._realtime_controller = RealtimeController(
        RealtimeControllerConfig(deadband_w=300.0)
    )
    return coordinator


def test_get_realtime_grid_w_none_without_sensors(hass: HomeAssistant):
    heat_coordinator = MagicMock()
    coordinator = OptimizationCoordinator(
        hass,
        heat_coordinator,
        {**CONFIG, "grid_import_sensor": None, "grid_export_sensor": None},
    )
    assert coordinator._get_realtime_grid_w() is None


def test_get_realtime_grid_w_combines_import_and_export(hass: HomeAssistant):
    coordinator = _make_coordinator(hass)
    hass.states.async_set("sensor.grid_import", "500", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.grid_export", "0.2", {"unit_of_measurement": "kW"})

    # 500 W import - 200 W export = 300 W net import
    assert coordinator._get_realtime_grid_w() == pytest.approx(300.0)


def test_get_realtime_grid_w_unknown_unit_ignored(hass: HomeAssistant):
    coordinator = _make_coordinator(hass)
    hass.states.async_set("sensor.grid_import", "500", {"unit_of_measurement": "A"})
    hass.states.async_set("sensor.grid_export", "unavailable")

    assert coordinator._get_realtime_grid_w() is None


@pytest.mark.asyncio
async def test_realtime_update_noop_without_controller(hass: HomeAssistant):
    heat_coordinator = MagicMock()
    coordinator = OptimizationCoordinator(hass, heat_coordinator, CONFIG)
    # No controller set up (async_setup not called) -> must not raise.
    await coordinator._handle_realtime_update(None)


@pytest.mark.asyncio
async def test_realtime_update_noop_when_not_optimize_v2(hass: HomeAssistant):
    coordinator = _make_coordinator(hass)
    coordinator._control_mode = "legacy"
    coordinator.data = {
        "thermal_v2": {
            "available": True,
            "offsets": [1],
            "shadow_price_eur_per_kwh": 0.1,
        }
    }
    hass.states.async_set("sensor.grid_import", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.grid_export", "500", {"unit_of_measurement": "W"})

    await coordinator._handle_realtime_update(None)

    assert "realtime" not in coordinator.data


@pytest.mark.asyncio
async def test_realtime_update_publishes_action_when_exporting(hass: HomeAssistant):
    coordinator = _make_coordinator(hass)
    coordinator.data = {
        "thermal_v2": {
            "available": True,
            "offsets": [1, 1, 0],
            "shadow_price_eur_per_kwh": 0.08,
        }
    }
    hass.states.async_set("sensor.grid_import", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.grid_export", "500", {"unit_of_measurement": "W"})

    await coordinator._handle_realtime_update(None)

    assert coordinator.data["realtime"]["adjustment"] == 1
    assert coordinator.data["realtime"]["planned_offset"] == 1
    assert coordinator.data["realtime"]["effective_offset"] == 2


@pytest.mark.asyncio
async def test_realtime_update_skipped_when_thermal_v2_unavailable(hass: HomeAssistant):
    coordinator = _make_coordinator(hass)
    coordinator.data = {"thermal_v2": {"available": False}}
    hass.states.async_set("sensor.grid_import", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.grid_export", "500", {"unit_of_measurement": "W"})

    await coordinator._handle_realtime_update(None)

    assert "realtime" not in coordinator.data


@pytest.mark.asyncio
async def test_full_dp_cycle_resets_realtime_controller_memory(hass: HomeAssistant):
    """A fresh DP baseline must discard stale real-time adjustment memory."""
    weather_coordinator = MagicMock()
    weather_coordinator.data = {"temperature_forecast": [2.0] * 6}
    heat_coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, CONFIG, "test_entry"
    )
    heat_coordinator.data = {
        "net_heat_loss_forecast": [1.0] * 6,
        "heat_demand_factor": 1.0,
        "heat_pump_on": True,
        "net_heat_loss": 1.0,
        "solar_gain_forecast": [],
        "indoor_temperature": 20.0,
    }
    heat_coordinator.weather_coordinator = weather_coordinator

    config = {**CONFIG, "consumption_price_sensor": "sensor.price"}
    coordinator = OptimizationCoordinator(hass, heat_coordinator, config)
    coordinator._realtime_controller = RealtimeController(RealtimeControllerConfig())
    coordinator._realtime_controller.reset(3)
    hass.states.async_set("sensor.price", "0.20", {"forecast_prices": [0.20] * 6})

    await coordinator._async_update_data()

    assert coordinator._realtime_controller.last_adjustment == 0
