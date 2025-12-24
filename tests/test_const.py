"""Test the const module."""

import pytest
from custom_components.heating_curve_optimizer.const import (
    calculate_ventilation_htc,
    calculate_htc_from_energy_label,
    DEFAULT_VENTILATION_TYPE,
    VENTILATION_TYPES,
)


def test_calculate_ventilation_htc_natural():
    """Test ventilation HTC calculation for natural ventilation."""
    htc = calculate_ventilation_htc(
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=2.5,
    )
    # Volume = 150 * 2.5 = 375 m³
    # ACH for natural_standard = 1.0
    # H_v = 1.2 * 1.005 * 375 * 1.0 / 3.6 ≈ 125.6 W/K
    assert htc > 0
    assert 120 < htc < 130


def test_calculate_ventilation_htc_mechanical_exhaust():
    """Test ventilation HTC for mechanical exhaust."""
    htc = calculate_ventilation_htc(
        area_m2=150,
        ventilation_type="mechanical_exhaust",
        ceiling_height=2.5,
    )
    assert htc > 0


def test_calculate_ventilation_htc_balanced():
    """Test ventilation HTC for balanced ventilation."""
    htc = calculate_ventilation_htc(
        area_m2=150,
        ventilation_type="balanced",
        ceiling_height=2.5,
    )
    assert htc > 0


def test_calculate_ventilation_htc_heat_recovery():
    """Test ventilation HTC for heat recovery ventilation."""
    htc = calculate_ventilation_htc(
        area_m2=150,
        ventilation_type="heat_recovery_70",
        ceiling_height=2.5,
    )
    # Heat recovery should have lower HTC due to efficiency
    assert htc > 0
    natural_htc = calculate_ventilation_htc(150, "natural_standard", 2.5)
    assert htc < natural_htc


def test_calculate_ventilation_htc_invalid_type():
    """Test ventilation HTC with invalid ventilation type."""
    # Should fallback to default (natural_standard)
    htc = calculate_ventilation_htc(
        area_m2=150,
        ventilation_type="invalid_type",
        ceiling_height=2.5,
    )
    # Should return same as natural_standard ventilation
    natural_htc = calculate_ventilation_htc(150, DEFAULT_VENTILATION_TYPE, 2.5)
    assert htc == natural_htc


def test_calculate_htc_from_energy_label_a_plus():
    """Test HTC calculation for A+ label."""
    htc = calculate_htc_from_energy_label(
        energy_label="A+",
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=2.5,
    )
    assert htc > 0
    # A+ should have lower HTC than C
    htc_c = calculate_htc_from_energy_label(
        energy_label="C",
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=2.5,
    )
    assert htc < htc_c


def test_calculate_htc_from_energy_label_c():
    """Test HTC calculation for C label."""
    htc = calculate_htc_from_energy_label(
        energy_label="C",
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=2.5,
    )
    assert htc > 0


def test_calculate_htc_from_energy_label_g():
    """Test HTC calculation for G label."""
    htc = calculate_htc_from_energy_label(
        energy_label="G",
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=2.5,
    )
    assert htc > 0
    # G should have poor insulation
    assert htc > 200  # High HTC value


def test_calculate_htc_with_different_ventilation():
    """Test that ventilation type affects HTC."""
    htc_natural = calculate_htc_from_energy_label(
        energy_label="C",
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=2.5,
    )
    htc_hr = calculate_htc_from_energy_label(
        energy_label="C",
        area_m2=150,
        ventilation_type="heat_recovery_70",
        ceiling_height=2.5,
    )
    # Heat recovery should result in lower total HTC
    assert htc_hr < htc_natural


def test_calculate_htc_with_zero_degree_days():
    """Test HTC calculation with zero heating degree days."""
    # Should fallback to default heating degree days
    htc = calculate_htc_from_energy_label(
        energy_label="C",
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=2.5,
        heating_degree_days=0,
    )
    assert htc > 0


def test_ventilation_types_all_have_ach():
    """Test that all ventilation types have ACH defined."""
    for vent_type, data in VENTILATION_TYPES.items():
        assert "ach" in data
        assert "name_en" in data
        assert "name_nl" in data
        assert data["ach"] > 0


def test_calculate_htc_different_ceiling_heights():
    """Test HTC calculation with different ceiling heights."""
    htc_low = calculate_htc_from_energy_label(
        energy_label="C",
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=2.0,
    )
    htc_high = calculate_htc_from_energy_label(
        energy_label="C",
        area_m2=150,
        ventilation_type="natural_standard",
        ceiling_height=3.0,
    )
    # Higher ceiling = larger volume = more ventilation heat loss
    assert htc_high > htc_low
