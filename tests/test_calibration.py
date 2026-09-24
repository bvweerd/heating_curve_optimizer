"""Tests for calibration.py: building, COP and emitter fits."""

from __future__ import annotations

import math
import random
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_curve_optimizer.calibration import (
    MIN_SAMPLES_TO_APPLY,
    RESULT_EXCLUDED_DHW,
    CopSample,
    EmitterSample,
    Prior,
    Sample,
    ThermalCalibrationState,
    effective_energy_label,
    fit_building,
    fit_cop,
    fit_emitter,
    fit_passes_quality_gates,
    residual_diagnosis,
)

PRIOR = Prior(
    ua_w_per_k=400.0, thermal_mass_kwh_per_k=12.0, internal_gain_kw=0.45, area_m2=150
)


def _samples(
    *,
    ua: float,
    mass: float,
    solar_factor: float = 1.0,
    internal_kw: float = 0.45,
    cop_scale: float = 1.0,
    gas_share: float = 0.0,
    n: int = 60,
    noise: float = 0.0,
    seed: int = 1,
) -> list[Sample]:
    """Windows that satisfy the 1R1C balance for known parameters.

    ``heat_kw`` is what the integration believes the heat pump delivered;
    the true heat pump heat is ``cop_scale`` times that.
    """
    rng = random.Random(seed)
    samples = []
    for i in range(n):
        delta_t = rng.uniform(5.0, 22.0)
        solar = rng.uniform(0.0, 1.5)
        on_gas = i < n * gas_share
        believed_hp = 0.0 if on_gas else rng.uniform(0.0, 8.0)
        gas = rng.uniform(2.0, 8.0) if on_gas else 0.0
        true_heat = believed_hp * cop_scale + gas
        rate = (
            true_heat + solar_factor * solar + internal_kw - ua / 1000.0 * delta_t
        ) / mass + rng.uniform(-noise, noise)
        samples.append(Sample(delta_t, believed_hp, solar, rate, gas))
    return samples


def test_fit_recovers_building_parameters() -> None:
    fit = fit_building(
        _samples(ua=320.0, mass=15.0, solar_factor=0.7, internal_kw=0.3, n=200), PRIOR
    )
    assert fit is not None
    assert fit.ua_w_per_k == pytest.approx(320.0, rel=0.05)
    assert fit.thermal_mass_kwh_per_k == pytest.approx(15.0, rel=0.05)
    assert fit.solar_factor == pytest.approx(0.7, abs=0.1)
    assert fit.r_squared > 0.99
    assert fit.cop_scale == 1.0


def test_few_samples_stay_close_to_prior() -> None:
    fit = fit_building(_samples(ua=800.0, mass=30.0, n=3), PRIOR)
    assert fit is not None
    assert 400.0 <= fit.ua_w_per_k < 800.0


def test_gas_windows_calibrate_the_cop_scale() -> None:
    """Heat pump heat modelled 20 % too high; gas-meter windows reveal it."""
    samples = _samples(ua=400.0, mass=12.0, cop_scale=0.8, gas_share=0.3, n=200)
    fit = fit_building(samples, PRIOR)
    assert fit is not None
    assert fit.cop_scale == pytest.approx(0.8, abs=0.05)
    assert fit.ua_w_per_k == pytest.approx(400.0, rel=0.08)


def test_without_gas_the_scale_is_absorbed_but_time_constant_holds() -> None:
    samples = _samples(ua=400.0, mass=12.0, internal_kw=0.0, cop_scale=0.8, n=200)
    fit = fit_building(samples, PRIOR)
    assert fit is not None
    assert fit.cop_scale == 1.0
    assert fit.time_constant_hours == pytest.approx(12.0 / 0.4, rel=0.1)


def test_quality_gates() -> None:
    good = fit_building(_samples(ua=400.0, mass=12.0, n=MIN_SAMPLES_TO_APPLY), PRIOR)
    assert fit_passes_quality_gates(good)
    too_few = fit_building(_samples(ua=400.0, mass=12.0, n=10), PRIOR)
    assert not fit_passes_quality_gates(too_few)
    implausible = fit_building(_samples(ua=4000.0, mass=12.0, n=200), PRIOR)
    assert not fit_passes_quality_gates(implausible)


def test_cop_fit_recovers_curve() -> None:
    rng = random.Random(3)
    samples = []
    for _ in range(80):
        outdoor = rng.uniform(-8, 12)
        supply = rng.uniform(28, 48)
        cop = 3.8 + 0.07 * outdoor - 0.09 * (supply - 35)
        samples.append(CopSample(outdoor, supply, 1.0, cop))
    fit = fit_cop(samples, (4.2, 0.08, 0.11))
    assert fit is not None and fit.usable
    assert fit.base_cop == pytest.approx(3.8, abs=0.05)
    assert fit.k_factor == pytest.approx(0.09, abs=0.01)


def test_emitter_fit_recovers_curve() -> None:
    samples = [
        EmitterSample(dt, 9.0 * (dt / 25.0) ** 1.25)
        for dt in [6, 8, 10, 12, 15, 18, 20, 24] * 12
    ]
    fit = fit_emitter(
        samples, prior_nominal_kw=12.0, prior_exponent=1.3, nominal_delta_t=25.0
    )
    assert fit is not None and fit.usable
    assert fit.nominal_power_kw == pytest.approx(9.0, rel=0.05)
    assert fit.exponent == pytest.approx(1.25, abs=0.05)


def test_residual_diagnosis_flags_correlated_errors() -> None:
    wave = [math.sin(i / 8) for i in range(96)]
    noise = [random.Random(i).uniform(-1, 1) for i in range(96)]
    assert residual_diagnosis(wave)["two_mass_suspected"] is True
    assert residual_diagnosis(noise)["two_mass_suspected"] is False
    assert residual_diagnosis(wave[:10])["two_mass_suspected"] is None


def test_effective_energy_label_matches_label_estimate() -> None:
    from custom_components.heating_curve_optimizer.const import (
        calculate_htc_from_energy_label,
    )

    ua = calculate_htc_from_energy_label("B", 150, ventilation_type="natural_standard")
    assert effective_energy_label(ua, 150, "natural_standard", 2.5) == "B"


async def test_state_persists_and_resets() -> None:
    store = MagicMock()
    store.async_save = AsyncMock()
    state = ThermalCalibrationState(store=store)
    for sample in _samples(ua=400.0, mass=12.0, n=5):
        state.record_sample(sample, PRIOR)
    state.record_exclusion(RESULT_EXCLUDED_DHW)
    await state.async_save()
    saved = store.async_save.call_args[0][0]
    assert len(saved["samples"]) == 5
    assert saved["exclusions"][RESULT_EXCLUDED_DHW] == 1

    store.async_load = AsyncMock(return_value=saved)
    restored = ThermalCalibrationState(store=store)
    await restored.async_load()
    restored.refit(PRIOR)
    assert restored.sample_count == 5
    assert restored.fit is not None

    await restored.async_reset()
    assert restored.sample_count == 0 and restored.fit is None
