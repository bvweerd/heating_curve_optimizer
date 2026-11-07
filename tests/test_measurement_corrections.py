"""Measured COP windows, supply-return offset, plan adherence, UA learning
and accuracy against persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.calibration import (
    OFFSET_MIN_RUNS,
    CopSample,
    CopWindow,
    DeltaTOffsetLearner,
    OffsetRun,
    ThermalCalibrationState,
    fit_delta_t_offset,
)
from custom_components.heating_curve_optimizer.coordinator_optimization import (
    OptimizationCoordinator,
)
from custom_components.heating_curve_optimizer.helpers import measured_heat_kw
from custom_components.heating_curve_optimizer.mass_model import (
    HistorySample,
    fit_air_coupling,
)

from .conftest import main_config, zone_data
from .test_mass_model import _building, _synthetic_history

T0 = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def _minutes(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


# --- CopWindow ---------------------------------------------------------------


def _feed(window: CopWindow, minute: float, **kwargs):
    defaults = {
        "running": True,
        "heat_kw": 4.0,
        "power_kw": 1.0,
        "outdoor_temp": 5.0,
        "supply_temp": 30.0,
        "humidity": 85.0,
    }
    return window.update(_minutes(minute), **(defaults | kwargs))


def test_cop_window_sums_energy_including_defrost() -> None:
    """A defrost (negative heat) lowers the window COP: it is paid for."""
    window = CopWindow()
    assert _feed(window, 0) is None  # first reading only starts the clock
    for minute in range(1, 31):
        heat = -6.0 if minute in (10, 11) else 4.0
        assert _feed(window, minute, heat_kw=heat) is None
    result = _feed(window, 31, running=False)
    assert result is not None
    outdoor, supply, humidity, cop, hours = result
    assert hours == pytest.approx(0.5)
    assert cop == pytest.approx((28 * 4.0 - 2 * 6.0) / 30)
    assert (outdoor, supply, humidity) == pytest.approx((5.0, 30.0, 85.0))


def test_cop_window_closes_after_an_hour_of_running() -> None:
    window = CopWindow()
    _feed(window, 0)
    results = [_feed(window, minute) for minute in range(1, 61)]
    assert results[:-1] == [None] * 59
    assert results[-1] is not None and results[-1][4] == pytest.approx(1.0)
    # The next window starts at once.
    assert _feed(window, 61) is None


def test_cop_window_drops_short_runs_gaps_and_exclusions() -> None:
    window = CopWindow()
    _feed(window, 0)
    for minute in range(1, 10):
        _feed(window, minute)
    assert _feed(window, 10, running=False) is None  # 10 minutes: too short

    window = CopWindow()
    _feed(window, 0)
    for minute in range(1, 20):
        _feed(window, minute)
    _feed(window, 40)  # 21-minute gap: window dropped
    assert _feed(window, 41, running=False) is None

    window = CopWindow()
    _feed(window, 0)
    for minute in range(1, 30):
        _feed(window, minute)
    _feed(window, 30, excluded=True)  # DHW run: window dropped
    assert _feed(window, 31, running=False) is None


# --- Supply-return offset ----------------------------------------------------


def _pump_run(
    learner: DeltaTOffsetLearner,
    start: float,
    *,
    spread: float,
    water: float = 20.0,
    seconds: int = 120,
) -> OffsetRun | None:
    for second in range(0, seconds + 1, 10):
        learner.update(
            _minutes(start + second / 60),
            compressor_running=False,
            flow=14.0,
            supply=water + spread / 2,
            return_temp=water - spread / 2,
        )
    return learner.update(
        _minutes(start + seconds / 60 + 0.1),
        compressor_running=False,
        flow=5.0,  # placeholder flow: pump stopped
        supply=water,
        return_temp=water,
    )


def test_offset_learner_takes_settled_pump_runs_after_the_compressor() -> None:
    learner = DeltaTOffsetLearner(min_flow=6.0)
    learner.update(
        _minutes(0), compressor_running=True, flow=14.0, supply=30, return_temp=26
    )
    # Ten minutes after the compressor stopped: residual heat, not used.
    assert _pump_run(learner, 10, spread=-0.8) is None
    run = _pump_run(learner, 30, spread=-0.8)
    assert run == OffsetRun(water_temp=pytest.approx(20.0), spread=pytest.approx(-0.8))


def test_offset_learner_ignores_runs_too_short_to_settle() -> None:
    learner = DeltaTOffsetLearner(min_flow=6.0)
    learner.update(
        _minutes(0), compressor_running=False, flow=5.0, supply=20, return_temp=20
    )
    assert _pump_run(learner, 30, spread=-0.8, seconds=60) is None


def test_offset_fit_constant_and_temperature_dependent() -> None:
    assert fit_delta_t_offset([OffsetRun(20.0, -0.8)] * (OFFSET_MIN_RUNS - 1)) is None
    constant = fit_delta_t_offset(
        [OffsetRun(18.0 + i % 3, -0.8 + 0.05 * (i % 2)) for i in range(30)]
    )
    assert constant is not None
    assert constant.slope == 0.0
    assert constant.correction(30.0) == pytest.approx(0.75, abs=0.06)

    # The reading drifts with temperature: held at the edge of the data.
    runs = [OffsetRun(14.0 + i % 12, -2.5 + 0.09 * (14.0 + i % 12)) for i in range(48)]
    drifting = fit_delta_t_offset(runs)
    assert drifting is not None
    assert drifting.slope == pytest.approx(0.09, abs=0.01)
    assert drifting.correction(15.0) == pytest.approx(2.5 - 0.09 * 15.0, abs=0.05)
    assert drifting.correction(40.0) == pytest.approx(drifting.correction(25.0))


def test_measured_heat_applies_configured_before_learned_correction(
    hass: HomeAssistant,
) -> None:
    config = {
        "power_consumption": "sensor.power",
        "flow_sensor": "sensor.flow",
        "supply_temperature_sensor": "sensor.supply",
        "return_temperature_sensor": "sensor.return",
    }
    hass.states.async_set("sensor.power", "1000", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.flow", "14.0", {"unit_of_measurement": "L/min"})
    hass.states.async_set("sensor.supply", "29.0")
    hass.states.async_set("sensor.return", "25.0")
    per_k = 14.0 / 60.0 * 4.186
    plain = measured_heat_kw(hass, config)
    assert plain == pytest.approx(4.0 * per_k, rel=0.01)
    learned = measured_heat_kw(hass, config, learned_correction=lambda _t: 0.8)
    assert learned == pytest.approx(4.8 * per_k, rel=0.01)
    configured = measured_heat_kw(
        hass, config | {"delta_t_correction": 0.5}, learned_correction=lambda _t: 0.8
    )
    assert configured == pytest.approx(4.5 * per_k, rel=0.01)


async def test_calibration_store_keeps_offset_runs_and_drops_old_cop_rows() -> None:
    store = MagicMock()
    store.async_load = AsyncMock(
        return_value={
            # instantaneous samples of the old layout (no window length)
            "cop_samples": [[5.0, 30.0, 1.0, 9.9]],
            "offset_runs": [[20.0, -0.8]] * OFFSET_MIN_RUNS,
        }
    )
    state = ThermalCalibrationState(store=store)
    await state.async_load()
    assert not state.cop_samples
    assert len(state.offset_runs) == OFFSET_MIN_RUNS
    state.refit((1.0, 1.0, 1.0, 1.0, 1.0), cop_prior=(4.0, 0.1, 0.07))
    assert state.offset_fit is not None
    assert state.offset_fit.correction(25.0) == pytest.approx(0.8)
    state.record_cop_sample(CopSample(5.0, 30.0, 1.0, 3.8, 0.5), (4.0, 0.1, 0.07))
    assert len(state.cop_samples) == 1


# --- Coordinator wiring -------------------------------------------------------


def _coordinator(hass: HomeAssistant, **config) -> OptimizationCoordinator:
    full = {**main_config(), **zone_data(), **config}
    heat = MagicMock()
    heat.effective_config.return_value = full
    coordinator = OptimizationCoordinator(hass, None, heat, full, "zone")
    calibration = ThermalCalibrationState(store=MagicMock())
    calibration.async_save = AsyncMock()
    coordinator._calibration = calibration
    return coordinator


def test_learned_offset_is_applied_only_with_calibration_applied(
    hass: HomeAssistant,
) -> None:
    observe = _coordinator(hass, calibration_mode="observe")
    for _ in range(OFFSET_MIN_RUNS):
        observe.thermal_calibration.record_offset_run(OffsetRun(20.0, -0.8))
    assert observe.learned_delta_t_correction() is None
    applied = _coordinator(hass, calibration_mode="apply")
    for _ in range(OFFSET_MIN_RUNS):
        applied.thermal_calibration.record_offset_run(OffsetRun(20.0, -0.8))
    correction = applied.learned_delta_t_correction()
    assert correction is not None and correction(28.0) == pytest.approx(0.8)


def test_cop_samples_come_from_windows(hass: HomeAssistant) -> None:
    coordinator = _coordinator(
        hass,
        calibration_mode="observe",
        heat_pump_thermal_power_sensor="sensor.heat",
        supply_temperature_sensor="sensor.supply",
    )
    coordinator.heat_coordinator.weather_coordinator.data = {
        "current_temperature": 5.0,
        "humidity_forecast": [85.0],
    }
    hass.states.async_set("sensor.heat", "4.0", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.supply", "30.0")
    for minute in range(31):
        coordinator._record_cop_window(_minutes(minute), 1.0)
    assert not coordinator.thermal_calibration.cop_samples
    coordinator._record_cop_window(_minutes(31), 0.0)
    (sample,) = coordinator.thermal_calibration.cop_samples
    assert sample.cop == pytest.approx(4.0)
    assert sample.hours == pytest.approx(0.5)


def test_plan_adherence_follows_plan_on_off(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, heat_pump_control="follows_plan")
    coordinator.data = {"heat_pump_plan_on": False, "supply_temps": [28.0]}
    coordinator._track_plan_adherence(_minutes(0), 0.0)
    coordinator._track_plan_adherence(_minutes(1), 0.0)  # off, as planned
    coordinator._track_plan_adherence(_minutes(2), 1.2)  # on against the plan
    coordinator._track_plan_adherence(_minutes(3), 1.2)
    adherence = coordinator._take_plan_adherence()
    assert adherence["plan_followed_fraction"] == pytest.approx(1 / 3, abs=1e-3)
    assert coordinator._take_plan_adherence()["plan_followed_fraction"] is None


def test_plan_adherence_own_thermostat_checks_the_supply(hass: HomeAssistant) -> None:
    coordinator = _coordinator(
        hass, heat_pump_control="own_thermostat", supply_temperature_sensor="s.sup"
    )
    coordinator.data = {"offset": 0, "supply_temps": [28.0], "realtime": None}
    coordinator._track_plan_adherence(_minutes(0), 0.0)
    hass.states.async_set("s.sup", "28.8")
    coordinator._track_plan_adherence(_minutes(1), 1.2)  # within 1.5 K
    hass.states.async_set("s.sup", "33.0")
    coordinator._track_plan_adherence(_minutes(2), 1.2)  # far above the plan
    coordinator._track_plan_adherence(_minutes(3), 0.0)  # not running: fine
    assert coordinator._take_plan_adherence()[
        "plan_followed_fraction"
    ] == pytest.approx(2 / 3, abs=1e-3)


def test_accuracy_is_compared_with_persistence(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    for i in range(4):
        coordinator._accuracy.append((T0, 0.1, 0.3, 0.2 if i % 2 else -0.2))
    summary = coordinator.accuracy_summary(T0)
    assert summary["mae_k"] == pytest.approx(0.1)
    assert summary["mae_persistence_k"] == pytest.approx(0.2)
    assert summary["skill_vs_persistence"] == pytest.approx(0.5)


# --- UA scale in the mass model -----------------------------------------------


def test_mass_learning_finds_a_lower_heat_loss_than_the_label() -> None:
    truth = _building()
    truth.ua_w_per_k *= 0.7
    history = _synthetic_history(8.0, truth=truth)
    fit = fit_air_coupling(history, _building())
    assert fit is not None
    assert fit.ua_scale == pytest.approx(0.7, abs=0.1)
    assert fit.coupling_kw_per_k == pytest.approx(8.0, rel=0.4)


def test_mass_learning_keeps_the_label_heat_loss_when_it_is_right() -> None:
    fit = fit_air_coupling(_synthetic_history(8.0), _building())
    assert fit is not None and fit.ua_scale == pytest.approx(1.0)
    # History samples stay a plain 6-field layout (storage unchanged).
    assert len(vars(HistorySample(20.0, 5.0, 0.0, 0.0))) == 6


def test_cop_fit_keeps_the_prior_k_when_supply_follows_the_curve() -> None:
    """Supply set by the heating curve moves with the outdoor temperature:
    the slopes are not separable and k must not go negative."""
    from custom_components.heating_curve_optimizer.calibration import fit_cop

    samples = []
    for i in range(60):
        outdoor = -2.0 + 0.3 * i
        supply = 34.0 - 0.3 * outdoor + (0.3 if i % 2 else -0.3)
        cop = 2.6 + 0.2 * outdoor - 0.07 * (supply - 35.0)
        samples.append(CopSample(outdoor, supply, 1.0, cop, 1.0))
    fit = fit_cop(samples, (4.05, 0.107, 0.07))
    assert fit is not None
    assert fit.k_factor == pytest.approx(0.07)
    assert fit.usable
    assert fit.base_cop + fit.outdoor_coefficient * 10 - 0.07 * (31.0 - 35) == (
        pytest.approx(2.6 + 2.0 + 0.28, abs=0.15)
    )
