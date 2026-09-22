"""Tests for the phase-2 shadow-mode wiring: OptimizationCoordinator's
`_run_thermal_v2_optimization` and the diagnostic sensors that read it.

The point of this suite is the phase-2 promise from docs/redesign/
REDESIGN.md: the redesigned optimizer runs alongside the legacy one but
can never break it, even when its own inputs are bad.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.coordinator import (
    HeatCalculationCoordinator,
    OptimizationCoordinator,
)
from custom_components.heating_curve_optimizer.sensor_thermal_shadow import (
    ThermalShadowCostComparisonSensor,
    ThermalShadowOffsetSensor,
)

VALID_CONFIG = {
    "area_m2": 150,
    "energy_label": "C",
    "target_indoor_temp": 20.0,
    "indoor_temp_hysteresis_lower": 0.5,
    "indoor_temp_hysteresis_upper": 0.5,
    "heat_curve_min": 20.0,
    "heat_curve_max": 45.0,
    "heat_curve_min_outdoor": -10.0,
    "heat_curve_max_outdoor": 15.0,
}


@pytest.mark.asyncio
async def test_thermal_v2_returns_available_result_for_valid_config(
    hass: HomeAssistant,
):
    heat_coordinator = MagicMock()
    coordinator = OptimizationCoordinator(hass, heat_coordinator, VALID_CONFIG)

    horizon = 12
    result = coordinator._run_thermal_v2_optimization(
        demand_forecast=[1.0] * horizon,
        price_forecast=[0.10, 0.10, 0.10, 0.10, 0.40, 0.40, 0.10, 0.10] + [0.10] * 4,
        temp_forecast=[2.0] * horizon,
        solar_gain_forecast=[],
        indoor_temperature=20.0,
        time_base=60,
        offset_delta_t=10,
        min_supply=20.0,
        max_supply=45.0,
        min_outdoor=-10.0,
        max_outdoor=15.0,
        current_offset=0,
    )

    assert result["available"] is True
    assert len(result["offsets"]) == horizon
    assert len(result["indoor_temps"]) == horizon
    assert result["total_cost_eur"] >= 0.0
    assert result["building_ua_w_per_k"] > 0
    assert result["emitter_nominal_power_kw"] > 0


@pytest.mark.asyncio
async def test_thermal_v2_fails_gracefully_without_area_m2(hass: HomeAssistant):
    """Missing/zero area_m2 must produce available: False, not an exception -
    a coordinator update must never crash because the *shadow* calculation
    had incomplete config."""
    heat_coordinator = MagicMock()
    bad_config = {**VALID_CONFIG, "area_m2": 0}
    coordinator = OptimizationCoordinator(hass, heat_coordinator, bad_config)

    result = coordinator._run_thermal_v2_optimization(
        demand_forecast=[1.0] * 6,
        price_forecast=[0.2] * 6,
        temp_forecast=[2.0] * 6,
        solar_gain_forecast=[],
        indoor_temperature=20.0,
        time_base=60,
        offset_delta_t=10,
        min_supply=20.0,
        max_supply=45.0,
        min_outdoor=-10.0,
        max_outdoor=15.0,
        current_offset=0,
    )

    assert result["available"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_thermal_v2_dispatch_failure_does_not_break_legacy_result(
    hass: HomeAssistant,
):
    """End-to-end: even if the shadow executor call raises, the coordinator's
    `_async_update_data` must still return the legacy optimization result -
    see the belt-and-braces try/except around the shadow dispatch."""
    weather_coordinator = MagicMock()
    weather_coordinator.data = {
        "temperature_forecast": [2.0] * 6,
    }
    heat_coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, {**VALID_CONFIG}, "test_entry"
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

    config = {
        **VALID_CONFIG,
        "consumption_price_sensor": "sensor.price",
    }
    coordinator = OptimizationCoordinator(hass, heat_coordinator, config)
    hass.states.async_set("sensor.price", "0.20")

    # Force the shadow path to blow up at the dispatch level.
    coordinator._run_thermal_v2_optimization = MagicMock(
        side_effect=RuntimeError("boom")
    )

    result = await coordinator._async_update_data()

    assert "optimized_offset" in result  # legacy result present and intact
    assert result["thermal_v2"]["available"] is False
    assert "boom" in result["thermal_v2"]["error"]


def test_shadow_offset_sensor_unavailable_when_shadow_calc_failed():
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"thermal_v2": {"available": False, "error": "no area_m2"}}

    sensor = ThermalShadowOffsetSensor(
        coordinator=coordinator,
        name="Thermal V2 Offset",
        unique_id="x_thermal_v2_offset",
        icon="mdi:chart-timeline-variant",
        device=None,
    )

    assert sensor.available is False
    assert sensor.extra_state_attributes == {"error": "no area_m2"}


def test_shadow_offset_sensor_reports_offset_when_available():
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "thermal_v2": {
            "available": True,
            "offset": 2,
            "offsets": [2, 1, 0],
            "supply_temps": [37.0, 36.0, 35.0],
            "indoor_temps": [20.1, 20.2, 20.3],
            "thermal_power_kw": [1.0, 1.0, 1.0],
            "electrical_power_kw": [0.3, 0.3, 0.3],
            "cost_eur": [0.06, 0.06, 0.06],
        }
    }

    sensor = ThermalShadowOffsetSensor(
        coordinator=coordinator,
        name="Thermal V2 Offset",
        unique_id="x_thermal_v2_offset",
        icon="mdi:chart-timeline-variant",
        device=None,
    )

    assert sensor.available is True
    assert sensor.native_value == 2
    assert sensor.extra_state_attributes["offsets"] == [2, 1, 0]


def test_cost_comparison_sensor_reports_savings():
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "thermal_v2": {
            "available": True,
            "total_cost_eur": 1.20,
            "legacy_total_cost_eur": 1.50,
            "shadow_price_eur_per_kwh": 0.03,
        }
    }

    sensor = ThermalShadowCostComparisonSensor(
        coordinator=coordinator,
        name="Thermal V2 Cost Comparison",
        unique_id="x_thermal_v2_cost_comparison",
        icon="mdi:scale-balance",
        device=None,
    )

    assert sensor.available is True
    assert sensor.native_value == pytest.approx(0.30)


def test_cost_comparison_sensor_unavailable_without_legacy_cost():
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "thermal_v2": {
            "available": True,
            "total_cost_eur": 1.20,
            "legacy_total_cost_eur": None,
        }
    }

    sensor = ThermalShadowCostComparisonSensor(
        coordinator=coordinator,
        name="Thermal V2 Cost Comparison",
        unique_id="x_thermal_v2_cost_comparison",
        icon="mdi:scale-balance",
        device=None,
    )

    assert sensor.available is False
