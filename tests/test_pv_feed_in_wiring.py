"""Tests for fase 5's PV surplus / feed-in price wiring into the thermal
v2 shadow optimizer (docs/redesign/REDESIGN.md §2.1.G).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.coordinator import (
    HeatCalculationCoordinator,
    OptimizationCoordinator,
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
}


async def _build_heat_coordinator(
    hass: HomeAssistant, entry_id: str = "test_entry"
) -> HeatCalculationCoordinator:
    weather_coordinator = MagicMock()
    weather_coordinator.data = {"temperature_forecast": [2.0] * 8}
    heat_coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, CONFIG, entry_id
    )
    heat_coordinator.data = {
        "net_heat_loss_forecast": [1.0] * 8,
        "heat_demand_factor": 1.0,
        "heat_pump_on": True,
        "net_heat_loss": 1.0,
        "solar_gain_forecast": [],
        "indoor_temperature": 20.0,
        "pv_production_forecast": [3.0] * 8,
    }
    heat_coordinator.weather_coordinator = weather_coordinator
    return heat_coordinator


@pytest.mark.asyncio
async def test_pv_surplus_reduces_v2_cost_with_cheap_feed_in(hass: HomeAssistant):
    heat_coordinator = await _build_heat_coordinator(hass)

    config_with_pv = {
        **CONFIG,
        "consumption_price_sensor": "sensor.price",
        "production_price_sensor": "sensor.feed_in_price",
    }
    hass.states.async_set("sensor.price", "0.30", {"forecast_prices": [0.30] * 8})
    hass.states.async_set(
        "sensor.feed_in_price", "0.05", {"forecast_prices": [0.05] * 8}
    )

    coordinator_with_pv = OptimizationCoordinator(
        hass, heat_coordinator, config_with_pv
    )
    result_with_pv = await coordinator_with_pv._async_update_data()

    config_without_pv = {
        **CONFIG,
        "consumption_price_sensor": "sensor.price",
    }
    heat_coordinator_no_pv = HeatCalculationCoordinator(
        hass, heat_coordinator.weather_coordinator, config_without_pv, "test_entry_2"
    )
    heat_coordinator_no_pv.data = {
        **heat_coordinator.data,
        "pv_production_forecast": [],
    }
    coordinator_without_pv = OptimizationCoordinator(
        hass, heat_coordinator_no_pv, config_without_pv
    )
    result_without_pv = await coordinator_without_pv._async_update_data()

    assert result_with_pv["thermal_v2"]["available"] is True
    assert result_without_pv["thermal_v2"]["available"] is True
    # With PV surplus covering part of the heat pump's electrical draw at a
    # cheap feed-in price, total v2 cost must be no higher than without it.
    assert (
        result_with_pv["thermal_v2"]["total_cost_eur"]
        <= result_without_pv["thermal_v2"]["total_cost_eur"] + 1e-9
    )


@pytest.mark.asyncio
async def test_missing_production_price_sensor_falls_back_gracefully(
    hass: HomeAssistant,
):
    """No production_price_sensor configured must not break the shadow
    optimizer - it falls back to thermal_optimizer's fixed feed-in price."""
    heat_coordinator = await _build_heat_coordinator(hass)
    config = {**CONFIG, "consumption_price_sensor": "sensor.price"}
    hass.states.async_set("sensor.price", "0.20", {"forecast_prices": [0.20] * 8})

    coordinator = OptimizationCoordinator(hass, heat_coordinator, config)
    result = await coordinator._async_update_data()

    assert result["thermal_v2"]["available"] is True
