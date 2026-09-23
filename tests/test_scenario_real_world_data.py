"""Integration-style scenario tests using real-world data.

The temperature/radiation forecast and the 15-minute electricity price
curve below are taken from a real `battery_controller` config_entry
diagnostics export (shared 2026-09-22, a sibling integration by the same
author, resampled to hourly for this integration's own price_forecast/
temp_forecast inputs - the actual location, weather, and price data a
live Home Assistant instance saw that day). Unlike the rest of this test
suite (synthetic, deliberately simple forecasts to isolate one behavior
at a time), the point of this file is the opposite: does the optimizer
still behave sensibly against a real, noisy, non-round-numbers forecast -
a genuine evening price spike (0.82 €/kWh around hour 4) next to cheap
overnight hours (0.19 €/kWh around hour 22), and real weather (mild
September temperatures, real solar radiation curve), not the clean
step-function prices most of the other tests use.

This is not a regression test for any specific bug - it is a sanity
check that the whole pipeline (thermal optimizer, heat-loss/solar-gain
calculation) produces plausible, internally-consistent results end to end
against real data, the way `docs/examples/*.md` do for humans reading the
docs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.const import (
    calculate_htc_from_energy_label,
)
from custom_components.heating_curve_optimizer.coordinator import (
    HeatCalculationCoordinator,
    OptimizationCoordinator,
)

# --- Real weather data (battery_controller diagnostics, 2026-09-22T12:00Z) ---
# 36 hourly values starting at forecast_start_utc.
TEMPERATURE_FORECAST = [
    18.6, 20.0, 19.7, 19.5, 19.3, 18.9, 16.9, 16.5, 16.1, 15.9, 15.7, 15.6,
    15.4, 14.7, 14.6, 14.8, 14.8, 14.7, 14.7, 15.0, 16.1, 17.4, 18.9, 20.3,
    21.0, 20.1, 20.3, 19.5, 19.2, 18.8, 18.2, 17.6, 17.2, 16.3, 15.9, 15.4,
]  # fmt: skip

RADIATION_FORECAST = [
    255.0, 395.0, 170.0, 101.0, 49.0, 20.0, 4.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.0, 51.0, 192.0, 300.0, 435.0, 552.0,
    538.0, 244.0, 307.0, 95.0, 58.0, 28.0, 6.0, 0.0, 0.0, 0.0, 0.0, 0.0,
]  # fmt: skip

# --- Real electricity price data (same diagnostics export) ---
# The original 144 steps are 15-minute dynamic prices (€/kWh); resampled
# to hourly here (mean of each 4-step block) to match time_base=60. Real
# evening price spike around hour 4 (0.82), cheap overnight hours around
# hour 22 (0.19) - the actual shape this test exercises the optimizer
# against.
PRICE_FORECAST = [
    0.3003, 0.3388, 0.4173, 0.658, 0.8168, 0.5768, 0.4613, 0.4188, 0.379,
    0.3544, 0.3433, 0.3423, 0.3401, 0.3401, 0.3511, 0.4022, 0.4646, 0.3939,
    0.3358, 0.2963, 0.2445, 0.2077, 0.1888, 0.2234, 0.2669, 0.3181, 0.3613,
    0.4096, 0.4065, 0.3826, 0.3514, 0.3246, 0.3122, 0.3445, 0.3309, 0.323,
]  # fmt: skip

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


def _demand_forecast_from_real_weather() -> list[float]:
    """Derive a realistic heat-demand forecast (kW) the same way
    HeatCalculationCoordinator does: HTC * delta-T, from the real outdoor
    temperature forecast above and a plausible target indoor temperature."""
    htc_w_per_k = calculate_htc_from_energy_label("C", 150)
    target_indoor = 20.0
    return [
        max(0.0, htc_w_per_k * (target_indoor - outdoor) / 1000.0)
        for outdoor in TEMPERATURE_FORECAST
    ]


@pytest.mark.asyncio
async def test_thermal_v2_optimizer_handles_real_world_forecasts(
    hass: HomeAssistant,
):
    """The redesigned optimizer (building_model/heatpump_model/
    thermal_optimizer) must also produce a usable, available result
    against this same real data - not just the clean, short synthetic
    forecasts test_thermal_v2_shadow.py otherwise uses."""
    heat_coordinator = MagicMock()
    coordinator = OptimizationCoordinator(hass, heat_coordinator, dict(BASE_CONFIG))

    demand_forecast = _demand_forecast_from_real_weather()
    horizon = 24  # a full real day of this diagnostics export

    result = coordinator._run_thermal_v2_optimization(
        demand_forecast=demand_forecast[:horizon],
        price_forecast=PRICE_FORECAST[:horizon],
        temp_forecast=TEMPERATURE_FORECAST[:horizon],
        solar_gain_forecast=[r * 0.01 for r in RADIATION_FORECAST[:horizon]],
        indoor_temperature=20.0,
        time_base=60,
        offset_delta_t=10,
        min_supply=25.0,
        max_supply=50.0,
        min_outdoor=-10.0,
        max_outdoor=18.0,
        current_offset=0,
    )

    assert result["available"] is True
    assert len(result["offsets"]) == horizon
    assert len(result["indoor_temps"]) == horizon
    assert result["total_cost_eur"] >= 0.0
    # Real weather means real, non-trivial building heat loss - the
    # optimizer must have something to actually optimize against.
    assert result["building_ua_w_per_k"] > 0
    assert result["emitter_nominal_power_kw"] > 0

    # Indoor temperature should stay within a physically sane band - the
    # optimizer must not let the building freeze or overheat even while
    # chasing the real price curve's spike/dip.
    assert all(5.0 < t < 30.0 for t in result["indoor_temps"])


@pytest.mark.asyncio
async def test_heat_calculation_coordinator_against_real_weather(
    hass: HomeAssistant,
):
    """HeatCalculationCoordinator's heat-loss/solar-gain pipeline against
    the real weather forecast - the input every other optimizer in this
    file ultimately derives its own demand_forecast from."""
    weather_coordinator = MagicMock()
    weather_coordinator.data = {
        "current_temperature": TEMPERATURE_FORECAST[0],
        "temperature_forecast": TEMPERATURE_FORECAST,
        "radiation_forecast": RADIATION_FORECAST,
    }

    config = {
        "area_m2": 150,
        "energy_label": "C",
        "glass_south_m2": 10,
        "glass_east_m2": 5,
        "glass_west_m2": 5,
        "glass_u_value": 1.2,
        "target_indoor_temp": 20.0,
    }
    coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, config, "real_world_scenario_entry"
    )

    result = await coordinator._async_update_data()

    assert result["net_heat_loss"] is not None
    assert len(result["heat_loss_forecast"]) == len(TEMPERATURE_FORECAST)
    # Daytime solar gain (real radiation up to 552 W/m2) must show up as
    # a real, non-zero effect, not silently ignored.
    assert max(result["solar_gain_forecast"]) > 0.0
