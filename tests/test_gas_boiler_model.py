"""Tests for the gas boiler cost model (gas_boiler_model.py)."""

import pytest

from custom_components.heating_curve_optimizer.gas_boiler_model import (
    GasBoilerConfig,
    compare_heat_pump_and_gas,
)


def test_cost_per_kwh_thermal_matches_formula():
    boiler = GasBoilerConfig(efficiency=0.9, calorific_value_kwh_per_m3=9.77)
    cost = boiler.cost_per_kwh_thermal(1.20)
    assert cost == pytest.approx((1.20 / 9.77) / 0.9, rel=1e-9)


def test_cost_per_kwh_thermal_zero_calorific_value_returns_inf():
    boiler = GasBoilerConfig(efficiency=0.9, calorific_value_kwh_per_m3=0.0)
    assert boiler.cost_per_kwh_thermal(1.20) == float("inf")


def test_cost_per_kwh_thermal_negative_calorific_value_returns_inf():
    boiler = GasBoilerConfig(efficiency=0.9, calorific_value_kwh_per_m3=-1.0)
    assert boiler.cost_per_kwh_thermal(1.20) == float("inf")


def test_cost_per_kwh_thermal_zero_efficiency_returns_inf():
    boiler = GasBoilerConfig(efficiency=0.0, calorific_value_kwh_per_m3=9.77)
    assert boiler.cost_per_kwh_thermal(1.20) == float("inf")


def test_cost_per_kwh_thermal_negative_efficiency_returns_inf():
    boiler = GasBoilerConfig(efficiency=-0.5, calorific_value_kwh_per_m3=9.77)
    assert boiler.cost_per_kwh_thermal(1.20) == float("inf")


def test_from_config_uses_defaults_when_keys_absent():
    boiler = GasBoilerConfig.from_config({})
    assert boiler.efficiency == 0.90
    assert boiler.calorific_value_kwh_per_m3 == 9.77


def test_from_config_reads_configured_values():
    config = {
        "gas_boiler_efficiency": 1.05,
        "gas_calorific_value_kwh_per_m3": 9.5,
    }
    boiler = GasBoilerConfig.from_config(config)
    assert boiler.efficiency == 1.05
    assert boiler.calorific_value_kwh_per_m3 == 9.5


def test_compare_prefers_gas_when_cheaper():
    result = compare_heat_pump_and_gas(
        heat_pump_cost_eur_per_kwh=0.30, gas_cost_eur_per_kwh=0.15
    )
    assert result.prefer_gas is True
    assert result.savings_eur_per_kwh == pytest.approx(0.15)
    assert result.savings_pct == pytest.approx(50.0)


def test_compare_prefers_heat_pump_when_cheaper():
    result = compare_heat_pump_and_gas(
        heat_pump_cost_eur_per_kwh=0.15, gas_cost_eur_per_kwh=0.30
    )
    assert result.prefer_gas is False
    assert result.savings_eur_per_kwh == pytest.approx(-0.15)


def test_compare_exact_tie_prefers_heat_pump():
    """A tie must deterministically prefer the heat pump so floating-point
    noise on an equal-cost boundary never flips the recommendation."""
    result = compare_heat_pump_and_gas(
        heat_pump_cost_eur_per_kwh=0.25, gas_cost_eur_per_kwh=0.25
    )
    assert result.prefer_gas is False
    assert result.savings_eur_per_kwh == 0.0


def test_compare_savings_pct_zero_when_heat_pump_cost_zero():
    """Avoid a ZeroDivisionError when the heat pump cost happens to be 0."""
    result = compare_heat_pump_and_gas(
        heat_pump_cost_eur_per_kwh=0.0, gas_cost_eur_per_kwh=0.10
    )
    assert result.savings_pct == 0.0
