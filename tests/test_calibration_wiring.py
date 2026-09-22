"""Coordinator-level tests for phase 4 calibration wiring
(OptimizationCoordinator._maybe_record_calibration_sample /
async_reset_thermal_calibration), as distinct from the pure-math tests in
test_calibration.py.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.calibration import (
    MIN_SAMPLES_TO_APPLY,
    ThermalCalibrationState,
)
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
    "indoor_temperature_sensor": "sensor.indoor_temp",
    "power_consumption": "sensor.hp_power",
    "consumption_price_sensor": "sensor.price",
}


def _make_coordinator_with_calibration(hass: HomeAssistant) -> OptimizationCoordinator:
    weather_coordinator = MagicMock()
    heat_coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, CONFIG, "test_entry"
    )
    coordinator = OptimizationCoordinator(hass, heat_coordinator, CONFIG, "test_entry")
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    store.async_save = AsyncMock()
    coordinator._calibration = ThermalCalibrationState(store=store)
    return coordinator


@pytest.mark.asyncio
async def test_no_sample_recorded_without_real_indoor_sensor(hass: HomeAssistant):
    weather_coordinator = MagicMock()
    config = {**CONFIG, "indoor_temperature_sensor": None}
    heat_coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, config, "test_entry"
    )
    coordinator = OptimizationCoordinator(hass, heat_coordinator, config, "test_entry")
    store = MagicMock()
    store.async_save = AsyncMock()
    coordinator._calibration = ThermalCalibrationState(store=store)

    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.0,
        outdoor_temp=5.0,
        solar_gain_kw=0.0,
        supply_temp=35.0,
        heat_pump_on=True,
    )

    assert coordinator._calibration.sample_count == 0
    assert coordinator._last_calibration_snapshot is None


@pytest.mark.asyncio
async def test_no_sample_without_power_unit(hass: HomeAssistant):
    """A power sensor with no recognized unit must never be guessed at."""
    coordinator = _make_coordinator_with_calibration(hass)
    hass.states.async_set("sensor.hp_power", "1500")  # no unit_of_measurement

    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.0,
        outdoor_temp=5.0,
        solar_gain_kw=0.0,
        supply_temp=35.0,
        heat_pump_on=True,
    )

    # First call only seeds the snapshot (no previous to compare against);
    # heat_and_solar_kw must be None because the power unit is unrecognized.
    assert coordinator._last_calibration_snapshot["heat_and_solar_kw"] is None


@pytest.mark.asyncio
async def test_two_cycles_produce_one_sample(hass: HomeAssistant):
    coordinator = _make_coordinator_with_calibration(hass)
    hass.states.async_set("sensor.hp_power", "2.0", {"unit_of_measurement": "kW"})

    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.0,
        outdoor_temp=5.0,
        solar_gain_kw=0.0,
        supply_temp=35.0,
        heat_pump_on=True,
    )
    assert coordinator._calibration.sample_count == 0  # first cycle only seeds

    # Simulate 15 minutes elapsed and a temperature response.
    coordinator._last_calibration_snapshot["timestamp"] -= timedelta(minutes=15)

    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.3,
        outdoor_temp=5.0,
        solar_gain_kw=0.0,
        supply_temp=35.0,
        heat_pump_on=True,
    )

    assert coordinator._calibration.sample_count == 1


@pytest.mark.asyncio
async def test_large_time_gap_is_skipped(hass: HomeAssistant):
    """An implausibly long gap (HA restart, network loss) must not produce
    a sample - the observed rate over that gap is meaningless."""
    coordinator = _make_coordinator_with_calibration(hass)
    hass.states.async_set("sensor.hp_power", "2.0", {"unit_of_measurement": "kW"})

    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.0,
        outdoor_temp=5.0,
        solar_gain_kw=0.0,
        supply_temp=35.0,
        heat_pump_on=True,
    )
    coordinator._last_calibration_snapshot["timestamp"] -= timedelta(hours=10)

    await coordinator._maybe_record_calibration_sample(
        indoor_temp=19.0,
        outdoor_temp=5.0,
        solar_gain_kw=0.0,
        supply_temp=35.0,
        heat_pump_on=True,
    )

    assert coordinator._calibration.sample_count == 0


@pytest.mark.asyncio
async def test_reset_service_handler_clears_and_refreshes(hass: HomeAssistant):
    coordinator = _make_coordinator_with_calibration(hass)
    for _ in range(MIN_SAMPLES_TO_APPLY + 5):
        coordinator._calibration.record_sample(
            delta_t=10.0,
            heat_and_solar_kw=4.0,
            rate_c_per_h=0.0,
            prior_ua_w_per_k=400.0,
            prior_thermal_mass_kwh_per_k=12.0,
        )
    coordinator.async_request_refresh = AsyncMock()

    await coordinator.async_reset_thermal_calibration()

    assert coordinator._calibration.sample_count == 0
    assert coordinator._last_calibration_snapshot is None
    coordinator.async_request_refresh.assert_awaited()


@pytest.mark.asyncio
async def test_reset_without_calibration_set_up_warns_and_does_not_crash(
    hass: HomeAssistant,
):
    weather_coordinator = MagicMock()
    heat_coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, CONFIG, "test_entry"
    )
    coordinator = OptimizationCoordinator(hass, heat_coordinator, CONFIG)
    # No async_setup() called -> self._calibration is None.
    await coordinator.async_reset_thermal_calibration()  # must not raise
