"""Test defrost-factor calculation.

Replaces the old repo-root `test_defrost_standalone.py`, which duplicated
`calculate_defrost_factor` in a module-level script with no assertions and
only printed a table for manual inspection. This exercises the real
`helpers.calculate_defrost_factor` with actual assertions instead.
"""

from custom_components.heating_curve_optimizer.helpers import (
    calculate_defrost_factor,
)


def test_no_frosting_above_six_degrees():
    """Above 6°C the heat pump runs at full efficiency, regardless of humidity."""
    assert calculate_defrost_factor(6.0, 80.0) == 1.0
    assert calculate_defrost_factor(10.0, 100.0) == 1.0


def test_no_frosting_below_minus_ten():
    """Below -10°C the air is too dry to frost, so no penalty applies."""
    assert calculate_defrost_factor(-10.0, 90.0) == 1.0
    assert calculate_defrost_factor(-15.0, 90.0) == 1.0


def test_worst_frosting_zone_has_largest_penalty():
    """0-3°C at high humidity is the documented worst case for defrost losses."""
    worst = calculate_defrost_factor(1.0, 90.0)
    mild = calculate_defrost_factor(5.0, 80.0)
    assert worst < mild
    assert worst < 1.0


def test_factor_stays_within_documented_bounds():
    """The multiplier never exceeds 1.0 (no bonus) or drops below 0.60 (40% cap)."""
    for temp in range(-15, 11):
        for humidity in (50, 70, 80, 90, 100):
            factor = calculate_defrost_factor(float(temp), float(humidity))
            assert 0.60 <= factor <= 1.0


def test_higher_humidity_never_improves_cop_in_frosting_zone():
    """More moisture in the air can only make frosting worse, never better."""
    low_rh = calculate_defrost_factor(2.0, 50.0)
    high_rh = calculate_defrost_factor(2.0, 100.0)
    assert high_rh <= low_rh
