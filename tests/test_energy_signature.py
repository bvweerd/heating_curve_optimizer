"""Tests for the daily energy signature (energy_signature.py)."""

import math
import random
from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.heating_curve_optimizer.energy_signature import (
    MIN_DAYS,
    REJECT_COVERAGE,
    REJECT_EXCLUDED,
    DaySample,
    EnergySignature,
    SignaturePrior,
    fit_signature,
)

LABEL = SignaturePrior(
    ua_w_per_k=226.0,
    internal_gain_kw=0.32,
    solar_factor=0.75,
    thermal_mass_kwh_per_k=26.0,
    area_m2=159.0,
)


def _days(n: int, *, ua=130.0, internal=0.40, solar=0.6, spread=True, seed=1):
    """Daily balances of a house with this UA, internal gain and solar
    factor, with a little scatter (meters, weather)."""
    rnd = random.Random(seed)
    days = []
    for i in range(n):
        delta_t = 14.0 + (4.0 * math.sin(i) if spread else 0.2 * math.sin(i))
        solar_kwh = 4.0 + 6.0 * abs(math.cos(1.7 * i))
        change = rnd.uniform(-0.3, 0.3)
        heat = (
            ua / 1000.0 * 24.0 * delta_t
            - internal * 24.0
            - solar * solar_kwh
            + LABEL.thermal_mass_kwh_per_k * change
            + rnd.gauss(0.0, 1.5)
        )
        days.append(DaySample(f"d{i}", delta_t, heat, solar_kwh, change))
    return days


def test_recovers_the_heat_loss_from_daily_balances():
    fit = fit_signature(_days(21), LABEL)
    assert fit is not None and fit.usable, fit
    assert fit.ua_w_per_k == pytest.approx(130.0, rel=0.12)
    assert fit.r_squared > 0.8


def test_few_days_stay_near_the_label_and_are_not_used():
    fit = fit_signature(_days(4), LABEL)
    assert fit is not None and not fit.usable
    assert fit.reason == "too_few_days"


def test_too_little_temperature_spread_is_not_used():
    fit = fit_signature(_days(MIN_DAYS + 4, spread=False), LABEL)
    assert fit is not None and not fit.usable
    assert fit.reason == "too_little_temperature_spread"


def test_no_days_no_fit():
    assert fit_signature([], LABEL) is None


T0 = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)


def _run_day(sig, day: date, *, minutes=1440, excluded_minutes=0, start=T0):
    closed = None
    for m in range(minutes):
        closed = (
            sig.tick(
                start + timedelta(minutes=m),
                day,
                heat_kw=1.5,
                indoor=20.0 + m / 1440 * 0.2,
                outdoor=6.0,
                solar_kw=0.5 if 600 <= m < 900 else 0.0,
                excluded=m < excluded_minutes,
            )
            or closed
        )
    return closed


def test_a_full_day_is_closed_as_a_24h_balance():
    sig = EnergySignature()
    _run_day(sig, date(2026, 11, 16))
    closed = sig.tick(
        T0 + timedelta(days=1),
        date(2026, 11, 17),
        heat_kw=1.5,
        indoor=20.2,
        outdoor=6.0,
        solar_kw=0.0,
        excluded=False,
    )
    assert closed is not None
    assert closed.heat_kwh == pytest.approx(36.0, rel=0.01)
    assert closed.delta_t == pytest.approx(14.1, abs=0.05)
    assert closed.solar_kwh == pytest.approx(2.5, rel=0.02)
    assert closed.indoor_change == pytest.approx(0.2, abs=0.01)
    assert len(sig.days) == 1


def test_days_with_little_left_are_rejected():
    sig = EnergySignature()
    _run_day(sig, date(2026, 11, 16), excluded_minutes=660)
    _run_day(sig, date(2026, 11, 17), start=T0 + timedelta(days=1), minutes=600)
    sig.tick(
        T0 + timedelta(days=2),
        date(2026, 11, 18),
        heat_kw=0.0,
        indoor=20.0,
        outdoor=6.0,
        solar_kw=0.0,
        excluded=False,
    )
    assert len(sig.days) == 0
    assert sig.rejected[REJECT_EXCLUDED] == 1
    assert sig.rejected[REJECT_COVERAGE] == 1


def test_a_fireplace_costs_its_hours_not_the_day():
    sig = EnergySignature()
    _run_day(sig, date(2026, 11, 16), excluded_minutes=120)
    closed = sig.tick(
        T0 + timedelta(days=1),
        date(2026, 11, 17),
        heat_kw=1.5,
        indoor=20.2,
        outdoor=6.0,
        solar_kw=0.0,
        excluded=False,
    )
    assert closed is not None
    # 2 h of fire and 2 h of settling are left out; the rest is scaled.
    assert closed.hours == pytest.approx(20.0, abs=0.1)
    assert closed.heat_kwh == pytest.approx(36.0, rel=0.01)


def test_a_short_exclusion_does_not_settle():
    sig = EnergySignature()
    _run_day(sig, date(2026, 11, 16), excluded_minutes=10)
    assert sig.as_dict()["current"]["excluded_hours"] == pytest.approx(9 / 60)


def test_part_days_weigh_less():
    days = _days(14)
    off = days[0]
    off.heat_kwh += 40.0
    full = fit_signature(days, LABEL)
    off.hours = 12.0
    part = fit_signature(days, LABEL)
    clean = fit_signature(_days(14), LABEL)
    assert full and part and clean
    assert abs(part.ua_w_per_k - clean.ua_w_per_k) < abs(
        full.ua_w_per_k - clean.ua_w_per_k
    )


def test_a_day_in_progress_from_the_old_layout_is_dropped():
    old = {"current": {"day": "2026-11-16", "hours": 9.0, "heat_kwh": 5.0}}
    assert EnergySignature.from_dict(old)._current is None


def test_the_day_in_progress_survives_a_restart():
    sig = EnergySignature()
    _run_day(sig, date(2026, 11, 16), minutes=720)
    restored = EnergySignature.from_dict(sig.as_dict())
    _run_day(
        restored,
        date(2026, 11, 16),
        minutes=720,
        start=T0 + timedelta(minutes=721),
    )
    closed = restored.tick(
        T0 + timedelta(days=1),
        date(2026, 11, 17),
        heat_kw=1.5,
        indoor=20.2,
        outdoor=6.0,
        solar_kw=0.0,
        excluded=False,
    )
    assert closed is not None  # 23.97 h covered: the restart gap is tiny
    assert EnergySignature.from_dict(None).days == EnergySignature().days
