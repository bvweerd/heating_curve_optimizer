"""Unit tests for coordinator logic that does not need a full setup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.heating_curve_optimizer.building_model import BuildingConfig
from custom_components.heating_curve_optimizer.calibration import (
    RESULT_NO_INDOOR_SENSOR,
    ThermalCalibrationState,
)
from custom_components.heating_curve_optimizer.coordinator_optimization import (
    OptimizationCoordinator,
    price_change_is_significant,
)
from custom_components.heating_curve_optimizer.sensor import TotalCostSavingsSensor

from .conftest import (
    OPEN_METEO_URL,
    main_config,
    make_entry,
    open_meteo_payload,
    zone_data,
)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (None, 0.30, False),
        (0.0, 0.0, False),
        (0.0, 0.02, True),  # previously a ZeroDivisionError
        (-0.05, -0.04, True),  # previously never triggered for negative prices
        (0.30, 0.31, False),
        (0.30, 0.34, True),
    ],
)
def test_price_change_is_significant(old, new, expected) -> None:
    assert price_change_is_significant(old, new) is expected


def _optimization_coordinator(hass: HomeAssistant, **config) -> OptimizationCoordinator:
    heat = MagicMock()
    heat.effective_config.return_value = {**main_config(), **zone_data(), **config}
    coordinator = OptimizationCoordinator(
        hass, None, heat, {**main_config(), **zone_data(), **config}, "zone"
    )
    return coordinator


def test_steps_follow_price_periods_and_planning_window(hass: HomeAssistant) -> None:
    coordinator = _optimization_coordinator(hass, planning_window=6)
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    starts = [base + timedelta(minutes=15 * i) for i in range(96)]
    now = base + timedelta(minutes=5)
    step_starts, durations = coordinator._build_steps(starts, 15, now)
    assert step_starts[0] == now
    assert durations[0] == pytest.approx(10 / 60)
    assert 6.0 <= sum(durations) < 6.25
    assert all(d == pytest.approx(0.25) for d in durations[1:])


def test_steps_extend_a_single_current_price(hass: HomeAssistant) -> None:
    coordinator = _optimization_coordinator(hass, planning_window=12)
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    step_starts, durations = coordinator._build_steps([base], 60, base)
    assert len(step_starts) == 12
    assert sum(durations) == pytest.approx(12.0)


async def test_calibration_window_accumulates_until_temperature_moves(
    hass: HomeAssistant,
) -> None:
    coordinator = _optimization_coordinator(hass)
    calibration = ThermalCalibrationState(store=MagicMock())
    calibration.async_save = MagicMock(side_effect=lambda: _noop())
    coordinator._calibration = calibration
    building = BuildingConfig.from_config(
        coordinator.heat_coordinator.effective_config()
    )

    start = dt_util.utcnow()
    kwargs = {
        "building": building,
        "indoor_is_measured": True,
        "outdoor_temp": 5.0,
        "solar_gain_kw": 0.0,
        "supply_temp": 35.0,
        "power_kw": 1.5,
    }
    temps = [20.0, 20.05, 20.1, 20.2, 20.35]
    for i, temp in enumerate(temps):
        await coordinator._maybe_record_calibration_sample(
            indoor_temp=temp, now=start + timedelta(minutes=15 * i), **kwargs
        )
    # 0.35 K over one hour: exactly one sample, window restarted.
    assert calibration.sample_count == 1
    assert coordinator._calibration_snapshot is None


async def test_calibration_skipped_without_measured_indoor_temperature(
    hass: HomeAssistant,
) -> None:
    coordinator = _optimization_coordinator(hass)
    calibration = ThermalCalibrationState(store=MagicMock())
    coordinator._calibration = calibration
    await coordinator._maybe_record_calibration_sample(
        building=BuildingConfig.from_config(zone_data()),
        indoor_temp=20.0,
        indoor_is_measured=False,
        outdoor_temp=5.0,
        solar_gain_kw=0.0,
        supply_temp=35.0,
        power_kw=1.0,
        now=dt_util.utcnow(),
    )
    assert calibration.last_result == RESULT_NO_INDOOR_SENSOR


async def _noop() -> None:
    return None


async def test_weather_radiation_is_shifted_to_the_hour_it_describes(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """open-meteo radiation is a preceding-hour mean: index i must be the
    value stamped at the end of hour i."""
    from custom_components.heating_curve_optimizer.coordinator_weather import (
        WeatherDataCoordinator,
    )

    payload = open_meteo_payload()
    aioclient_mock.get(OPEN_METEO_URL, json=payload)
    entry = make_entry()
    entry.add_to_hass(hass)
    coordinator = WeatherDataCoordinator(hass, entry)
    data = await coordinator._async_update_data()
    hour = dt_util.utcnow().hour
    assert (
        data["radiation_forecast"][0]
        == payload["hourly"]["shortwave_radiation"][hour + 1]
    )
    assert data["temperature_forecast"][0] == payload["hourly"]["temperature_2m"][hour]
    assert len(data["temperature_forecast"]) == 48


def test_total_cost_savings_books_losses_too() -> None:
    """Pre-heating costs money now and is booked as negative savings; the
    old sensor only ever added positive amounts."""
    coordinator = MagicMock()
    sensor = TotalCostSavingsSensor(coordinator, "entry", MagicMock())
    sensor.async_write_ha_state = MagicMock()
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    def update(ts: datetime, baseline: float, cost: float) -> None:
        coordinator.data = {
            "timestamp": ts,
            "step_durations_hours": [1.0],
            "cost_eur": [cost],
            "baseline_cost_eur": [baseline],
        }
        sensor._handle_coordinator_update()

    update(t0, baseline=0.20, cost=0.30)  # pre-heating: 0.10 €/h more
    update(t0 + timedelta(minutes=30), baseline=0.50, cost=0.10)
    assert sensor.native_value == pytest.approx(-0.05)
    update(t0 + timedelta(minutes=60), baseline=0.50, cost=0.10)
    assert sensor.native_value == pytest.approx(0.15)
