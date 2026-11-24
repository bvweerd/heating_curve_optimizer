"""Unit tests for coordinator logic that does not need a full setup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.heating_curve_optimizer.calibration import (
    RESULT_EXCLUDED_DHW,
    RESULT_EXCLUDED_WINDOW,
    RESULT_NO_INDOOR_SENSOR,
    OpenTime,
    ThermalCalibrationState,
)
from custom_components.heating_curve_optimizer.coordinator_optimization import (
    OptimizationCoordinator,
    _plan_run_advice,
    hourly_means,
    price_change_is_significant,
    pv_surplus,
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


def test_plan_run_advice_empty_plan_is_unavailable() -> None:
    on, change_at = _plan_run_advice([], [])
    assert on is None
    assert change_at is None


def test_plan_run_advice_stays_on_reports_no_change() -> None:
    now = dt_util.utcnow()
    starts = [now + timedelta(minutes=15 * i) for i in range(4)]
    on, change_at = _plan_run_advice([0.5, 0.6, 0.4, 0.3], starts)
    assert on is True
    assert change_at is None


def test_plan_run_advice_finds_the_next_transition() -> None:
    """A sunny afternoon should show up as an ``off`` advice with a lead
    time, well before the current step actually goes idle."""
    now = dt_util.utcnow()
    starts = [now + timedelta(minutes=15 * i) for i in range(4)]
    on, change_at = _plan_run_advice([0.5, 0.5, 0.0, 0.0], starts)
    assert on is True
    assert change_at == starts[2]


def test_plan_run_advice_currently_off_reports_when_heat_resumes() -> None:
    now = dt_util.utcnow()
    starts = [now + timedelta(minutes=15 * i) for i in range(4)]
    on, change_at = _plan_run_advice([0.0, 0.0, 0.5, 0.5], starts)
    assert on is False
    assert change_at == starts[2]


def test_plan_run_advice_trickle_below_idle_threshold_is_off() -> None:
    """The threshold is electrical: 0.1 kW electric (0.5 kW heat at COP 5)
    is standby-level trickle, not a running heat pump."""
    now = dt_util.utcnow()
    starts = [now + timedelta(minutes=15 * i) for i in range(3)]
    on, change_at = _plan_run_advice([0.1, 0.1, 0.8], starts, 0.15)
    assert on is False
    assert change_at == starts[2]


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


def test_steps_end_with_the_last_known_price(hass: HomeAssistant) -> None:
    """Beyond the known prices the horizon stops, not a flat made-up price."""
    coordinator = _optimization_coordinator(hass, planning_window=24)
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    starts = [base + timedelta(minutes=15 * i) for i in range(56)]  # until 0:00
    _, durations = coordinator._build_steps(starts, 15, base)
    assert sum(durations) == pytest.approx(14.0)


def test_steps_hold_the_last_price_for_a_minimum_horizon(hass: HomeAssistant) -> None:
    """Late in the evening, before tomorrow's prices, at least six hours."""
    coordinator = _optimization_coordinator(hass, planning_window=24)
    base = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
    starts = [base + timedelta(minutes=15 * i) for i in range(8)]  # until 0:00
    _, durations = coordinator._build_steps(starts, 15, base)
    assert sum(durations) == pytest.approx(6.0)


def test_hourly_means_turn_readings_into_interval_means() -> None:
    assert hourly_means([0.0, 2.0, 4.0]) == [1.0, 3.0, 4.0]
    assert hourly_means([]) == []


def test_pv_surplus_takes_off_base_load_and_shares_zones() -> None:
    assert pv_surplus([0.1, 1.3, 2.3], 0.3, 1) == pytest.approx([0.0, 1.0, 2.0])
    assert pv_surplus([2.3], 0.3, 2) == pytest.approx([1.0])


def test_ramp_steps_count_whole_periods_since_the_change(
    hass: HomeAssistant,
) -> None:
    coordinator = _optimization_coordinator(hass)
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert coordinator._ramp_steps(15, now) is None
    coordinator._offset_changed_at = now - timedelta(minutes=20)
    assert coordinator._ramp_steps(15, now) == 1


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


def _contact_event(hass: HomeAssistant, entity_id: str, state: str) -> MagicMock:
    hass.states.async_set(entity_id, state)
    event = MagicMock()
    event.data = {"entity_id": entity_id, "new_state": hass.states.get(entity_id)}
    return event


async def test_short_door_opening_keeps_the_window(hass: HomeAssistant) -> None:
    """Letting the dog out must not throw away hours of observation."""
    coordinator = _calibrating_coordinator(
        hass, window_sensors=["binary_sensor.front_door"]
    )
    start = dt_util.utcnow()
    temps = [20.0, 20.05, 20.1, 20.2, 20.35]
    for i, temp in enumerate(temps):
        if i == 2:
            await coordinator._handle_exclusion_sensor(
                _contact_event(hass, "binary_sensor.front_door", "on")
            )
            # Open right now: within the grace, not an exclusion.
            assert coordinator._active_exclusion() is None
            await coordinator._handle_exclusion_sensor(
                _contact_event(hass, "binary_sensor.front_door", "off")
            )
        await coordinator._maybe_record_calibration_sample(
            indoor_temp=temp,
            now=start + timedelta(minutes=15 * i),
            **CALIBRATION_KWARGS,
        )
    calibration = coordinator.thermal_calibration
    assert calibration.sample_count == 1
    assert calibration.exclusions[RESULT_EXCLUDED_WINDOW] == 0


async def test_long_opening_discards_the_window(hass: HomeAssistant) -> None:
    coordinator = _calibrating_coordinator(
        hass, window_sensors=["binary_sensor.front_door"]
    )
    start = dt_util.utcnow()
    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.0, now=start, **CALIBRATION_KWARGS
    )
    # Several openings, 12 minutes in total within the window.
    coordinator._open_time.total_s = 12 * 60
    await coordinator._maybe_record_calibration_sample(
        indoor_temp=20.1, now=start + timedelta(minutes=15), **CALIBRATION_KWARGS
    )
    calibration = coordinator.thermal_calibration
    assert calibration.last_result == RESULT_EXCLUDED_WINDOW
    assert coordinator._calibration_snapshot is None


async def test_window_open_past_the_grace_is_an_exclusion(
    hass: HomeAssistant,
) -> None:
    coordinator = _calibrating_coordinator(
        hass, window_sensors=["binary_sensor.fireplace"]
    )
    hass.states.async_set("binary_sensor.fireplace", "on")
    assert coordinator._active_exclusion() is None
    state = hass.states.get("binary_sensor.fireplace")
    state.last_changed = dt_util.utcnow() - timedelta(minutes=6)
    assert coordinator._active_exclusion() == RESULT_EXCLUDED_WINDOW


def test_open_time_counts_open_contacts_and_resets() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    open_time = OpenTime()
    open_time.opened("a", t0)
    open_time.closed("a", t0 + timedelta(minutes=2))
    open_time.opened("b", t0 + timedelta(minutes=5))
    assert open_time.seconds(t0 + timedelta(minutes=8)) == pytest.approx(300)
    open_time.reset(t0 + timedelta(minutes=8))
    # "b" is still open and counts from the reset on.
    assert open_time.seconds(t0 + timedelta(minutes=9)) == pytest.approx(60)
    open_time.closed("unknown", t0)  # a close without an open is ignored
    assert open_time.total_s == 0.0


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


async def test_local_outdoor_sensor_corrects_current_and_forecast(
    hass: HomeAssistant, aioclient_mock
) -> None:
    from custom_components.heating_curve_optimizer.coordinator_weather import (
        WeatherDataCoordinator,
    )

    payload = open_meteo_payload()
    aioclient_mock.get(OPEN_METEO_URL, json=payload)
    entry = make_entry(outdoor_temperature_sensor="sensor.station")
    entry.add_to_hass(hass)
    coordinator = WeatherDataCoordinator(hass, entry)
    plain = await coordinator._async_update_data()  # no reading yet: forecast
    assert plain["outdoor_temperature_source"] == "forecast"
    assert plain["current_temperature"] == plain["forecast_current_temperature"]

    local = plain["forecast_current_temperature"] + 2.4
    hass.states.async_set("sensor.station", str(local))
    data = await coordinator._async_update_data()
    assert data["outdoor_temperature_source"] == "sensor"
    assert data["current_temperature"] == pytest.approx(local, abs=0.01)
    corrected = data["temperature_forecast"]
    original = plain["temperature_forecast"]
    assert corrected[0] - original[0] == pytest.approx(2.4, abs=0.02)
    assert corrected[3] - original[3] == pytest.approx(1.2, abs=0.02)
    assert corrected[6:] == original[6:]

    # A sensor in the sun: the plan's correction is bounded.
    hass.states.async_set("sensor.station", str(plain["current_temperature"] + 12))
    data = await coordinator._async_update_data()
    assert data["temperature_forecast"][0] - original[0] == pytest.approx(5.0, abs=0.02)

    # A faulty reading falls back to the weather service.
    hass.states.async_set("sensor.station", "-127")
    data = await coordinator._async_update_data()
    assert data["outdoor_temperature_source"] == "forecast"
    assert data["temperature_forecast"] == original


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
            "plan_followed_fraction": 1.0,
        }
        sensor._handle_coordinator_update()

    update(t0, baseline=0.20, cost=0.30)  # pre-heating: 0.10 €/h more
    update(t0 + timedelta(minutes=30), baseline=0.50, cost=0.10)
    assert sensor.native_value == pytest.approx(-0.05)
    update(t0 + timedelta(minutes=60), baseline=0.50, cost=0.10)
    assert sensor.native_value == pytest.approx(0.15)


def test_total_cost_savings_books_only_the_followed_share() -> None:
    """The integration only advises: savings of a plan the heat pump did not
    follow are not booked, only kept as what it would have saved."""
    coordinator = MagicMock()
    sensor = TotalCostSavingsSensor(coordinator, "entry", MagicMock())
    sensor.async_write_ha_state = MagicMock()
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    def update(ts: datetime, followed: float | None) -> None:
        coordinator.data = {
            "timestamp": ts,
            "step_durations_hours": [1.0],
            "cost_eur": [0.10],
            "baseline_cost_eur": [0.50],
            "plan_followed_fraction": followed,
        }
        sensor._handle_coordinator_update()

    update(t0, followed=None)
    update(t0 + timedelta(hours=1), followed=0.25)  # 0.40 €/h x 1 h x 25 %
    assert sensor.native_value == pytest.approx(0.10)
    update(t0 + timedelta(hours=1, minutes=30), followed=None)  # not observed
    assert sensor.native_value == pytest.approx(0.10)
    attrs = sensor.extra_state_attributes
    assert attrs["savings_if_followed_eur"] == pytest.approx(0.60)
    assert attrs["observed_hours"] == pytest.approx(1.0)
    assert attrs["plan_followed_share"] == pytest.approx(0.25)


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
            CopSample(-5.0 + i % 12, 30.0 + i % 15, 1.0, 3.5 + 0.06 * (i % 12), 1.0),
            coordinator.cop_prior(),
        )
        calibration.record_emitter_sample(
            EmitterSample(6.0 + i % 18, 8.0 * ((6.0 + i % 18) / 25.0) ** 1.2, 7.0),
            prior_nominal_kw=10.0,
            prior_exponent=1.3,
            nominal_delta_t=25.0,
        )


@pytest.mark.parametrize(
    ("return_temp", "expected_spread"),
    [("31.0", 7.0), ("unavailable", 5.0), ("39.0", 5.0)],
)
def test_emitter_sample_uses_measured_return_temperature(
    hass: HomeAssistant, return_temp: str, expected_spread: float
) -> None:
    coordinator = _calibrating_coordinator(
        hass,
        heat_pump_thermal_power_sensor="sensor.heat",
        supply_temperature_sensor="sensor.supply",
        return_temperature_sensor="sensor.return",
    )
    hass.states.async_set("sensor.heat", "5.0", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.supply", "38.0")
    hass.states.async_set("sensor.return", return_temp)
    coordinator._record_operating_point(
        indoor_temp=20.0,
        indoor_is_measured=True,
        outdoor_temp=5.0,
        humidity=80.0,
        power_kw=1.5,
    )
    (sample,) = coordinator.thermal_calibration.emitter_samples
    assert sample.water_delta_t == pytest.approx(expected_spread)
    assert sample.delta_t == pytest.approx(38.0 - expected_spread / 2 - 20.0)


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
    assert emitter.water_delta_t == pytest.approx(7.0)
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


def test_building_fit_not_used_with_mass_air_model(hass: HomeAssistant) -> None:
    """The one-node fit of the room air is not the mass the heat pump heats;
    the COP and emitter fits still apply."""
    coordinator = _calibrating_coordinator(
        hass,
        calibration_mode="apply",
        building_model="mass_air",
        heat_pump_thermal_power_sensor="sensor.heat",
        power_consumption="sensor.p",
    )
    _ready_calibration(coordinator)
    config = coordinator.heat_coordinator.effective_config()
    building = coordinator.building_config(config)
    fit = coordinator.thermal_calibration.fit
    assert building.ua_w_per_k != pytest.approx(fit.ua_w_per_k)
    assert building.air_coupling_kw_per_k > 0
    emitter = coordinator.emitter_config(config, building)
    assert emitter.exponent == pytest.approx(
        coordinator.thermal_calibration.emitter_fit.exponent
    )
    assert coordinator.calibration_summary(config)["building_fit_used"] is False


def test_observed_calibration_is_not_used(hass: HomeAssistant) -> None:
    coordinator = _calibrating_coordinator(hass, power_consumption="sensor.p")
    _ready_calibration(coordinator)
    assert coordinator.calibration_status() == "ready"
    config = coordinator.heat_coordinator.effective_config()
    building = coordinator.building_config(config)
    assert building.ua_w_per_k == pytest.approx(
        coordinator.calibration_prior().ua_w_per_k
    )


async def test_realtime_update_keeps_the_poll_scheduled(hass: HomeAssistant) -> None:
    """The 60 s realtime loop must not push the 15-minute re-plan back:
    async_set_updated_data reschedules the poll, so it never fired."""
    from custom_components.heating_curve_optimizer.realtime_controller import (
        create_realtime_controller,
    )

    coordinator = _optimization_coordinator(hass, grid_import_sensor="sensor.grid")
    coordinator._realtime_controller = create_realtime_controller(coordinator.config)
    hass.states.async_set("sensor.grid", "500", {"unit_of_measurement": "W"})
    coordinator.data = {"offsets": [0], "shadow_price_eur_per_kwh": 0.1}
    listener = MagicMock()
    unsub = coordinator.async_add_listener(listener)
    scheduled = coordinator._unsub_refresh
    assert scheduled is not None

    await coordinator._handle_realtime_update(dt_util.utcnow())

    assert coordinator._unsub_refresh is scheduled
    assert "realtime" in coordinator.data
    listener.assert_called_once()
    unsub()


async def test_replans_at_the_next_price_boundary(hass: HomeAssistant) -> None:
    """The first step ends at the price boundary: re-plan there, even when
    the price does not change enough to trigger a re-plan by itself."""
    from unittest.mock import AsyncMock

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    coordinator = _optimization_coordinator(hass)
    coordinator.async_request_refresh = AsyncMock()
    boundary = dt_util.utcnow() + timedelta(minutes=10)
    coordinator._schedule_boundary_refresh(boundary - timedelta(minutes=5))
    coordinator._schedule_boundary_refresh(boundary)  # replaces the first

    async_fire_time_changed(hass, boundary - timedelta(minutes=4))
    await hass.async_block_till_done()
    coordinator.async_request_refresh.assert_not_called()

    async_fire_time_changed(hass, boundary + timedelta(seconds=1))
    await hass.async_block_till_done()
    coordinator.async_request_refresh.assert_called_once()
    assert coordinator._unsub_boundary is None


async def _gas_window(hass: HomeAssistant, coordinator, heating: str) -> float:
    """Run a 2 h window in which the gas meter rises 1 m³; return its gas_kw."""
    hass.states.async_set("sensor.gas", "100.0", {"unit_of_measurement": "m³"})
    hass.states.async_set("binary_sensor.cv_heating", heating)
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
    return coordinator.thermal_calibration.samples[-1].gas_kw


async def test_gas_without_heating_signal_is_not_space_heat(
    hass: HomeAssistant,
) -> None:
    """With a heating signal the meter's gas (a shower, cooking) does not
    count while the boiler did not heat the house."""
    coordinator = _calibrating_coordinator(
        hass,
        gas_meter_sensor="sensor.gas",
        gas_boiler_heating_sensor="binary_sensor.cv_heating",
        gas_boiler_heat_output_kw=6.0,
    )
    assert await _gas_window(hass, coordinator, "off") == 0.0


async def test_gas_with_heating_signal_is_signal_times_output(
    hass: HomeAssistant,
) -> None:
    coordinator = _calibrating_coordinator(
        hass,
        gas_meter_sensor="sensor.gas",
        gas_boiler_heating_sensor="binary_sensor.cv_heating",
        gas_boiler_heat_output_kw=6.0,
    )
    assert await _gas_window(hass, coordinator, "on") == pytest.approx(6.0)


def test_a_usable_energy_signature_carries_the_building_model(
    hass: HomeAssistant,
) -> None:
    from custom_components.heating_curve_optimizer.energy_signature import (
        SignatureFit,
    )

    coordinator = _calibrating_coordinator(
        hass,
        calibration_mode="apply",
        indoor_temperature_sensor="sensor.indoor",
        power_consumption="sensor.power",
    )
    config = coordinator.heat_coordinator.effective_config()
    label_ua = coordinator.building_config(config).ua_w_per_k
    coordinator._signature.fit = SignatureFit(
        ua_w_per_k=130.0,
        internal_gain_kw=0.4,
        solar_factor=0.6,
        r_squared=0.9,
        days=14,
        delta_t_spread=3.0,
        mean_abs_error_kwh=2.0,
        usable=True,
        reason=None,
    )
    building = coordinator.building_config(config)
    assert label_ua != 130.0
    assert building.ua_w_per_k == 130.0
    assert building.internal_gain_kw == 0.4
    assert coordinator.calibration_status() == "applied"
    summary = coordinator.calibration_summary(config)
    assert summary["signature_used"] is True
    assert summary["signature_ua_w_per_k"] == 130.0


def test_underfloor_calibration_waits_for_the_signature(hass: HomeAssistant) -> None:
    """Short windows do not decide the status with underfloor heating."""
    coordinator = _calibrating_coordinator(
        hass,
        calibration_mode="apply",
        emitter_type="underfloor",
        indoor_temperature_sensor="sensor.indoor",
        power_consumption="sensor.power",
    )
    assert coordinator._short_windows_apply() is False
    assert coordinator.calibration_status() == "collecting"
    summary = coordinator.calibration_summary(
        coordinator.heat_coordinator.effective_config()
    )
    assert summary["short_windows_used"] is False
    assert summary["progress_pct"] == 0


async def test_reset_clears_the_signature_and_the_mass_model(
    hass: HomeAssistant,
) -> None:
    coordinator = _calibrating_coordinator(hass)
    coordinator._signature.rejected["coverage"] = 3
    coordinator._mass = MagicMock()
    coordinator._mass.async_reset = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    await coordinator.async_reset_thermal_calibration()
    assert coordinator._signature.rejected["coverage"] == 0
    coordinator._mass.async_reset.assert_awaited_once()
