"""Tests for HeatCalculationCoordinator._calculate_pv_production - the
orientation-degrees generalization of the old fixed east/south/west Wp
buckets, now reading each PV-array subentry from `self.config["pv_arrays"]`
(see const.py's PV_SUBENTRY_TYPE comment and __init__.py's setup wiring).
"""

from __future__ import annotations

import pytest

from custom_components.heating_curve_optimizer.coordinator import (
    HeatCalculationCoordinator,
)


def _coordinator(pv_arrays: list[dict]) -> HeatCalculationCoordinator:
    coordinator = HeatCalculationCoordinator.__new__(HeatCalculationCoordinator)
    coordinator.config = {"pv_arrays": pv_arrays}
    return coordinator


def test_no_arrays_produces_zero_forecast():
    coordinator = _coordinator([])
    assert coordinator._calculate_pv_production([1000.0, 500.0]) == [0.0, 0.0]


def test_no_radiation_forecast_produces_empty_list():
    coordinator = _coordinator(
        [{"peak_power_kwp": 3.0, "orientation": 180.0, "tilt": 35.0}]
    )
    assert coordinator._calculate_pv_production([]) == []


def test_south_array_at_default_tilt_uses_full_orientation_factor():
    """orientation=180 (south) must reproduce the old fixed-bucket model's
    own factor of 1.0 exactly."""
    coordinator = _coordinator(
        [
            {
                "peak_power_kwp": 3.0,
                "orientation": 180.0,
                "tilt": 35.0,
                "efficiency_factor": 0.85,
            }
        ]
    )
    result = coordinator._calculate_pv_production([1000.0])
    # 3.0 kWp * 1000 W/m^2 * 1.0 (orientation) * 1.0 (tilt==DEFAULT) * 0.85 / 1000
    assert result[0] == pytest.approx(2.55)


@pytest.mark.parametrize("orientation", [90.0, 270.0])
def test_east_and_west_arrays_match_old_fixed_bucket_factor(orientation):
    """orientation=90/270 (east/west) must reproduce the old fixed-bucket
    model's own factor of 0.65 exactly - the whole point of the smooth
    cosine formula replacing the 3-bucket lookup is that it agrees with
    it at the three canonical angles."""
    coordinator = _coordinator(
        [
            {
                "peak_power_kwp": 2.0,
                "orientation": orientation,
                "tilt": 35.0,
                "efficiency_factor": 0.85,
            }
        ]
    )
    result = coordinator._calculate_pv_production([1000.0])
    # 2.0 kWp * 1000 * 0.65 * 1.0 * 0.85 / 1000
    assert result[0] == pytest.approx(1.105)


def test_north_facing_array_still_produces_something():
    """No 4th bucket existed for north in the old model (panels were
    assumed east/south/west only) - the new continuous formula must still
    return a sane, non-zero, lower-than-east/west value rather than
    silently producing nothing."""
    coordinator = _coordinator(
        [{"peak_power_kwp": 2.0, "orientation": 0.0, "tilt": 35.0}]
    )
    result = coordinator._calculate_pv_production([1000.0])
    assert 0.0 < result[0] < 1.105  # less than the east/west factor's own output


def test_multiple_arrays_sum_production():
    coordinator = _coordinator(
        [
            {
                "peak_power_kwp": 3.0,
                "orientation": 180.0,
                "tilt": 35.0,
                "efficiency_factor": 0.85,
            },
            {
                "peak_power_kwp": 2.0,
                "orientation": 90.0,
                "tilt": 35.0,
                "efficiency_factor": 0.85,
            },
        ]
    )
    result = coordinator._calculate_pv_production([1000.0])
    assert result[0] == pytest.approx(2.55 + 1.105)


def test_zero_peak_power_array_contributes_nothing():
    coordinator = _coordinator(
        [
            {"peak_power_kwp": 0.0, "orientation": 180.0, "tilt": 35.0},
            {
                "peak_power_kwp": 1.0,
                "orientation": 180.0,
                "tilt": 35.0,
                "efficiency_factor": 0.85,
            },
        ]
    )
    result = coordinator._calculate_pv_production([1000.0])
    assert result[0] == pytest.approx(0.85)


def test_missing_fields_fall_back_to_defaults():
    """An array dict with only peak_power_kwp set (schema always fills the
    rest, but defensive defaults matter for hand-built config dicts in
    other tests/migrations) must still produce a sensible result."""
    coordinator = _coordinator([{"peak_power_kwp": 3.0}])
    result = coordinator._calculate_pv_production([1000.0])
    # orientation defaults to 180 (south, factor 1.0), tilt defaults to
    # DEFAULT_PV_TILT (factor 1.0), efficiency_factor defaults to 0.85.
    assert result[0] == pytest.approx(2.55)
