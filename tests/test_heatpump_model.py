"""Tests for the heat pump performance model."""

import pytest

from custom_components.heating_curve_optimizer.heatpump_model import HeatPumpConfig


def test_cop_decreases_with_higher_supply_temp():
    """The whole point of a heating curve: a lower supply temperature is
    more efficient."""
    hp = HeatPumpConfig()
    cop_low = hp.cop_at(supply_temp=30.0, outdoor_temp=5.0)
    cop_high = hp.cop_at(supply_temp=50.0, outdoor_temp=5.0)
    assert cop_high < cop_low


def test_cop_increases_with_higher_outdoor_temp():
    hp = HeatPumpConfig()
    cop_cold = hp.cop_at(supply_temp=40.0, outdoor_temp=-5.0)
    cop_mild = hp.cop_at(supply_temp=40.0, outdoor_temp=10.0)
    assert cop_mild > cop_cold


def test_cop_never_below_configured_minimum():
    hp = HeatPumpConfig(min_cop=0.5)
    cop = hp.cop_at(supply_temp=90.0, outdoor_temp=-25.0)
    assert cop >= 0.5


def test_electrical_power_zero_for_zero_thermal_power():
    hp = HeatPumpConfig()
    assert (
        hp.electrical_power_kw(thermal_power_kw=0.0, supply_temp=40.0, outdoor_temp=5.0)
        == 0.0
    )


def test_electrical_power_equals_thermal_over_cop():
    hp = HeatPumpConfig()
    thermal = 3.0
    supply = 40.0
    outdoor = 5.0
    cop = hp.cop_at(supply_temp=supply, outdoor_temp=outdoor)
    electrical = hp.electrical_power_kw(
        thermal_power_kw=thermal, supply_temp=supply, outdoor_temp=outdoor
    )
    assert electrical == pytest.approx(thermal / cop, rel=1e-9)


def test_electrical_power_increases_for_same_heat_at_higher_supply_temp():
    """Delivering the same amount of heat at a higher supply temperature
    must cost more electricity (worse COP) - directly exercises the
    coupling that was missing before this redesign."""
    hp = HeatPumpConfig()
    thermal = 3.0
    outdoor = 0.0
    low = hp.electrical_power_kw(
        thermal_power_kw=thermal, supply_temp=32.0, outdoor_temp=outdoor
    )
    high = hp.electrical_power_kw(
        thermal_power_kw=thermal, supply_temp=48.0, outdoor_temp=outdoor
    )
    assert high > low


def test_cop_clamped_by_carnot_limit_at_large_lift():
    """An aggressively-tuned linear fit (high base COP, no temperature
    decline) must not be allowed to exceed the Carnot COP for the actual
    outdoor-to-supply lift - no real heat pump can beat the second-law
    limit, regardless of what the linear approximation says."""
    hp = HeatPumpConfig(
        base_cop_at_35=8.0,
        k_factor=0.0,
        outdoor_temp_coefficient=0.0,
        cop_compensation_factor=1.0,
        min_cop=0.5,
    )
    cop = hp.cop_at(supply_temp=40.0, outdoor_temp=-30.0)
    carnot_cop = (40.0 + 273.15) / (40.0 - (-30.0))
    assert cop <= carnot_cop + 1e-9
    assert cop < 8.0  # would be exactly 8.0 unclamped (k_factor=0, no outdoor effect)


def test_cop_unaffected_by_carnot_limit_in_typical_operating_range():
    """Default parameters at realistic operating points must stay well
    under the Carnot limit - the ceiling should not engage in normal
    operation."""
    hp = HeatPumpConfig()
    cop = hp.cop_at(supply_temp=45.0, outdoor_temp=-10.0)
    carnot_cop = (45.0 + 273.15) / (45.0 - (-10.0))
    assert cop < carnot_cop


def test_from_config_reads_expected_keys():
    config = {
        "base_cop": 4.5,
        "k_factor": 0.12,
        "outdoor_temp_coefficient": 0.09,
        "cop_compensation_factor": 0.95,
    }
    hp = HeatPumpConfig.from_config(config, max_thermal_power_kw=10.0)
    assert hp.base_cop_at_35 == 4.5
    assert hp.k_factor == 0.12
    assert hp.outdoor_temp_coefficient == 0.09
    assert hp.cop_compensation_factor == 0.95
    assert hp.max_thermal_power_kw == 10.0
