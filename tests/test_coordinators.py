"""Unit tests for coordinator logic that does not need a full setup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.heating_curve_optimizer.calibration import (
    RESULT_EXCLUDED_DHW,
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


def _calibrating_coordinator(hass: HomeAssistant, **config) -> OptimizationCoordinator:
    coordinator = _optimization_coordinator(hass, **config)
    calibration = ThermalCalibrationState(store=MagicMock())
    calibration.async_save = AsyncMock()
    coordinator._calibration = calibration
    return coordinator


CALIBRATION_KWARGS = {
    "indoor_is_measured": True,
    "outdoor_temp": 5.0,
    "solar_gain_kw": 0.0,
    "supply_temp": 35.0,
    "power_kw": 1.5,
}


async def test_calibration_window_accumulates_until_temperature_moves(
    hass: HomeAssistant,
) -> None:
    coordinator = _calibrating_coordinator(hass)
    start = dt_util.utcnow()
    for i, temp in enumerate([20.0, 20.05, 20.1, 20.2, 20.35]):
        await coordinator._maybe_record_calibration_sample(
            indoor_temp=temp,
            now=start + timedelta(minutes=15 * i),
            **CALIBRATION_KWARGS,
        )
    # 0.35 K over one hour: exactly one sample, window restarted.
    assert coordinator.thermal_calibration.sample_count == 1
    assert coordinator._calibration_snapshot is None


async def test_calibration_skipped_without_measured_indoor_temperature(
    hass: HomeAssistant,
) -> None:
    coordinator = _calibrating_coordinator(hass)
    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.0,
        now=dt_util.utcnow(),
        **(CALIBRATION_KWARGS | {"indoor_is_measured": False}),
    )
    assert coordinator.thermal_calibration.last_result == RESULT_NO_INDOOR_SENSOR


async def test_tap_water_run_discards_the_window(hass: HomeAssistant) -> None:
    coordinator = _calibrating_coordinator(hass, dhw_active_sensor="binary_sensor.dhw")
    hass.states.async_set("binary_sensor.dhw", "off")
    start = dt_util.utcnow()
    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.0, now=start, **CALIBRATION_KWARGS
    )
    hass.states.async_set("binary_sensor.dhw", "on")
    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.1, now=start + timedelta(minutes=15), **CALIBRATION_KWARGS
    )
    calibration = coordinator.thermal_calibration
    assert calibration.last_result == RESULT_EXCLUDED_DHW
    assert calibration.exclusions[RESULT_EXCLUDED_DHW] == 1
    assert coordinator._calibration_snapshot is None


async def test_calibration_off_records_nothing(hass: HomeAssistant) -> None:
    coordinator = _calibrating_coordinator(hass, calibration_mode="off")
    assert coordinator.calibration_status() == "off"
    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.0, now=dt_util.utcnow(), **CALIBRATION_KWARGS
    )
    assert coordinator._calibration_snapshot is None


async def test_gas_meter_heat_enters_the_sample(hass: HomeAssistant) -> None:
    coordinator = _calibrating_coordinator(hass, gas_meter_sensor="sensor.gas")
    hass.states.async_set("sensor.gas", "100.0", {"unit_of_measurement": "m³"})
    start = dt_util.utcnow()
    temps = [20.0, 20.1, 20.2, 20.25, 20.3, 20.35, 20.4, 20.45, 20.5]
    for i, temp in enumerate(temps):
        if i == len(temps) - 1:
            hass.states.async_set("sensor.gas", "101.0", {"unit_of_measurement": "m³"})
        await coordinator._maybe_record_calibration_sample(
            indoor_temp=temp,
            now=start + timedelta(minutes=15 * i),
            **(CALIBRATION_KWARGS | {"power_kw": 0.0}),
        )
    sample = coordinator.thermal_calibration.samples[-1]
    # 1 m³ x 9.77 kWh/m³ x 0.90 over 2 hours.
    assert sample.gas_kw == pytest.approx(9.77 * 0.9 / 2.0, rel=0.01)


def test_status_unavailable_with_several_zones(hass: HomeAssistant) -> None:
    heat = MagicMock()
    heat.effective_config.return_value = {**main_config(), **zone_data()}
    coordinator = OptimizationCoordinator(
        hass,
        None,
        heat,
        {**main_config(), **zone_data(), "power_consumption": "sensor.p"},
        "zone",
        calibration_allowed=False,
    )
    coordinator._calibration = ThermalCalibrationState(store=MagicMock())
    assert coordinator.calibration_status() == "unavailable"


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


def _ready_calibration(coordinator: OptimizationCoordinator) -> None:
    """Fill the coordinator's calibration with a fit that passes all gates."""
    from custom_components.heating_curve_optimizer.calibration import (
        CopSample,
        EmitterSample,
        Sample,
    )

    calibration = coordinator.thermal_calibration
    prior = coordinator.calibration_prior()
    ua, mass = prior.ua_w_per_k * 0.8, prior.thermal_mass_kwh_per_k * 1.2
    for i in range(40):
        delta_t = 8.0 + (i % 10)
        heat = 2.0 + (i % 7)
        rate = (heat + prior.internal_gain_kw - ua / 1000 * delta_t) / mass
        calibration.record_sample(Sample(delta_t, heat, 0.0, rate), prior)
        calibration.record_cop_sample(
            CopSample(-5.0 + i % 12, 30.0 + i % 15, 1.0, 3.5 + 0.06 * (i % 12)),
            coordinator.cop_prior(),
        )
        calibration.record_emitter_sample(
            EmitterSample(6.0 + i % 18, 8.0 * ((6.0 + i % 18) / 25.0) ** 1.2),
            prior_nominal_kw=10.0,
            prior_exponent=1.3,
            nominal_delta_t=25.0,
        )


def test_applied_calibration_replaces_the_models(hass: HomeAssistant) -> None:
    coordinator = _calibrating_coordinator(
        hass,
        calibration_mode="apply",
        heat_pump_thermal_power_sensor="sensor.heat",
        power_consumption="sensor.p",
    )
    _ready_calibration(coordinator)
    assert coordinator.calibration_status() == "applied"
    config = coordinator.heat_coordinator.effective_config()
    building = coordinator.building_config(config)
    fit = coordinator.thermal_calibration.fit
    assert building.ua_w_per_k == pytest.approx(fit.ua_w_per_k)
    emitter = coordinator.emitter_config(config, building)
    assert emitter.exponent == pytest.approx(
        coordinator.thermal_calibration.emitter_fit.exponent
    )
    heatpump = coordinator.heatpump_config(config, emitter)
    assert heatpump.cop_compensation_factor == 1.0
    assert heatpump.base_cop_at_35 == pytest.approx(
        coordinator.thermal_calibration.cop_fit.base_cop
    )
    summary = coordinator.calibration_summary(config)
    assert summary["status"] == "applied"
    assert summary["heat_source"] == "measured"
    assert summary["heat_loss_vs_label_pct"] < 0
    assert "effective_energy_label" in summary


def test_observed_calibration_is_not_used(hass: HomeAssistant) -> None:
    coordinator = _calibrating_coordinator(hass, power_consumption="sensor.p")
    _ready_calibration(coordinator)
    assert coordinator.calibration_status() == "ready"
    config = coordinator.heat_coordinator.effective_config()
    building = coordinator.building_config(config)
    assert building.ua_w_per_k == pytest.approx(
        coordinator.calibration_prior().ua_w_per_k
    )
