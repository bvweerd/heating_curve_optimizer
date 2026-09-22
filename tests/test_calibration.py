"""Tests for calibration.py (phase 4, docs/redesign/REDESIGN.md)."""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_curve_optimizer.calibration import (
    MIN_SAMPLES_TO_APPLY,
    RESULT_IMPLAUSIBLE,
    ThermalCalibrationState,
    fit_ua_and_thermal_mass,
)


def _synthetic_samples(
    true_thermal_mass: float,
    true_ua: float,
    *,
    n: int = 50,
    step_hours: float = 0.25,
    noise: float = 0.0,
    seed: int = 1,
) -> list[tuple[float, float, float]]:
    """Generate samples that exactly satisfy the 1R1C model for known
    (true_thermal_mass, true_ua), optionally with Gaussian-ish noise added
    to the observed rate - the fit should recover the true values."""
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        delta_t = rng.uniform(2.0, 20.0)
        heat_and_solar_kw = rng.uniform(0.0, 6.0)
        true_rate = (heat_and_solar_kw - (true_ua / 1000.0) * delta_t) / (
            true_thermal_mass
        )
        observed_rate = true_rate + rng.uniform(-noise, noise)
        samples.append((delta_t, heat_and_solar_kw, observed_rate))
    return samples


def test_fit_recovers_exact_parameters_without_noise():
    samples = _synthetic_samples(true_thermal_mass=12.0, true_ua=400.0, noise=0.0)
    result = fit_ua_and_thermal_mass(samples)
    assert result is not None
    thermal_mass, ua = result
    assert thermal_mass == pytest.approx(12.0, rel=1e-6)
    assert ua == pytest.approx(400.0, rel=1e-6)


def test_fit_recovers_approximate_parameters_with_noise():
    samples = _synthetic_samples(
        true_thermal_mass=12.0, true_ua=400.0, n=200, noise=0.05
    )
    result = fit_ua_and_thermal_mass(samples)
    assert result is not None
    thermal_mass, ua = result
    assert thermal_mass == pytest.approx(12.0, rel=0.15)
    assert ua == pytest.approx(400.0, rel=0.15)


def test_fit_returns_none_for_fewer_than_two_samples():
    assert fit_ua_and_thermal_mass([]) is None
    assert fit_ua_and_thermal_mass([(5.0, 1.0, 0.1)]) is None


def test_fit_returns_none_for_collinear_samples():
    """Samples where delta_t and rate never vary independently can't
    separate the two unknowns - must fail closed (None), not silently
    return a wrong fit."""
    samples = [(5.0, 1.0, 0.1), (5.0, 1.0, 0.1), (5.0, 1.0, 0.1)]
    assert fit_ua_and_thermal_mass(samples) is None


def test_fit_rejects_unphysical_negative_result():
    # Construct samples implying a negative thermal mass (rate and heat
    # move in a way no positive-capacity building could produce).
    samples = [(5.0, 0.0, 1.0), (10.0, 0.0, 2.0), (2.0, 0.0, 0.4)]
    result = fit_ua_and_thermal_mass(samples)
    assert result is None or (result[0] > 0 and result[1] > 0)


def _make_state() -> ThermalCalibrationState:
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    store.async_save = AsyncMock()
    return ThermalCalibrationState(store=store)


def test_not_applied_below_minimum_sample_count():
    state = _make_state()
    samples = _synthetic_samples(12.0, 400.0, n=MIN_SAMPLES_TO_APPLY - 5, noise=0.0)
    for delta_t, heat_and_solar_kw, rate in samples:
        state.record_sample(
            delta_t=delta_t,
            heat_and_solar_kw=heat_and_solar_kw,
            rate_c_per_h=rate,
            prior_ua_w_per_k=400.0,
            prior_thermal_mass_kwh_per_k=12.0,
        )
    assert state.applied is False


def test_applied_once_enough_plausible_samples_recorded():
    state = _make_state()
    samples = _synthetic_samples(12.0, 400.0, n=MIN_SAMPLES_TO_APPLY + 10, noise=0.0)
    for delta_t, heat_and_solar_kw, rate in samples:
        state.record_sample(
            delta_t=delta_t,
            heat_and_solar_kw=heat_and_solar_kw,
            rate_c_per_h=rate,
            prior_ua_w_per_k=400.0,
            prior_thermal_mass_kwh_per_k=12.0,
        )
    assert state.applied is True
    assert state.learned_ua_w_per_k == pytest.approx(400.0, rel=1e-3)
    assert state.learned_thermal_mass_kwh_per_k == pytest.approx(12.0, rel=1e-3)


def test_implausible_fit_relative_to_prior_is_not_applied():
    """A fit wildly different from the label-based prior (e.g. from a burst
    of bad samples) must be rejected rather than silently adopted."""
    state = _make_state()
    # True values imply UA ~40 W/K, but prior says 400 W/K (10x off) -
    # outside PLAUSIBLE_RATIO_BOUNDS, so this must be rejected.
    samples = _synthetic_samples(12.0, 40.0, n=MIN_SAMPLES_TO_APPLY + 10, noise=0.0)
    moved_flags = []
    for delta_t, heat_and_solar_kw, rate in samples:
        moved = state.record_sample(
            delta_t=delta_t,
            heat_and_solar_kw=heat_and_solar_kw,
            rate_c_per_h=rate,
            prior_ua_w_per_k=400.0,
            prior_thermal_mass_kwh_per_k=12.0,
        )
        moved_flags.append(moved)
    assert state.applied is False
    assert state.last_result == RESULT_IMPLAUSIBLE
    assert not any(moved_flags)


@pytest.mark.asyncio
async def test_async_reset_clears_state_and_persists():
    state = _make_state()
    samples = _synthetic_samples(12.0, 400.0, n=MIN_SAMPLES_TO_APPLY + 5, noise=0.0)
    for delta_t, heat_and_solar_kw, rate in samples:
        state.record_sample(
            delta_t=delta_t,
            heat_and_solar_kw=heat_and_solar_kw,
            rate_c_per_h=rate,
            prior_ua_w_per_k=400.0,
            prior_thermal_mass_kwh_per_k=12.0,
        )
    assert state.applied is True

    await state.async_reset()

    assert state.sample_count == 0
    assert state.learned_ua_w_per_k is None
    assert state.applied is False
    state.store.async_save.assert_awaited()


@pytest.mark.asyncio
async def test_async_load_restores_persisted_fit():
    store = MagicMock()
    store.async_load = AsyncMock(
        return_value={
            "samples": [[5.0, 1.0, 0.1]] * (MIN_SAMPLES_TO_APPLY + 1),
            "learned_ua_w_per_k": 410.0,
            "learned_thermal_mass_kwh_per_k": 11.5,
        }
    )
    state = ThermalCalibrationState(store=store)

    await state.async_load()

    assert state.sample_count == MIN_SAMPLES_TO_APPLY + 1
    assert state.learned_ua_w_per_k == 410.0
    assert state.learned_thermal_mass_kwh_per_k == 11.5
    assert state.applied is True


@pytest.mark.asyncio
async def test_async_load_with_no_stored_data_leaves_defaults():
    state = _make_state()
    await state.async_load()
    assert state.sample_count == 0
    assert state.applied is False
