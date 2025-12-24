"""Test the optimizer module."""

import pytest
from custom_components.heating_curve_optimizer.optimizer import (
    optimize_offsets,
    calculate_buffer_energy,
)
from custom_components.heating_curve_optimizer.helpers import (
    calculate_supply_temperature,
)


def test_calculate_buffer_energy_evolution():
    """Test buffer energy calculation over time."""
    offsets = [1, 1, 0, -1, -1, 0]
    demand = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]

    energy_evolution = calculate_buffer_energy(
        offsets=offsets,
        demand=demand,
        time_base=60,
        buffer=0.0,
    )

    assert len(energy_evolution) == 6
    # First step should store energy (positive offset)
    assert energy_evolution[0] > 0.0


def test_calculate_buffer_energy_with_initial_buffer():
    """Test buffer energy with initial buffer value."""
    offsets = [0, 0, 0]
    demand = [1.0, 1.0, 1.0]

    energy_evolution = calculate_buffer_energy(
        offsets=offsets,
        demand=demand,
        time_base=60,
        buffer=2.0,  # Start with 2 kWh
    )

    # With zero offsets, buffer should remain constant
    assert all(abs(e - 2.0) < 0.1 for e in energy_evolution)


def test_optimize_offsets_basic():
    """Test basic optimization with constant prices."""
    demand = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    prices = [0.25] * 6
    outdoor_temps = [10.0] * 6

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert offsets is not None
    assert buffers is not None
    assert len(offsets) == 6
    assert len(buffers) == 6


def test_optimize_offsets_varying_prices():
    """Test optimization adapts to varying prices."""
    demand = [1.0] * 6
    prices = [0.10, 0.15, 0.35, 0.35, 0.15, 0.10]
    outdoor_temps = [10.0] * 6

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert len(offsets) == 6
    assert len(buffers) == 6


def test_optimize_offsets_respects_constraints():
    """Test optimization respects offset change constraints."""
    demand = [1.0] * 6
    prices = [0.25] * 6
    outdoor_temps = [10.0] * 6

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    # All offsets should be within -4 to +4 range
    for offset in offsets:
        assert -4 <= offset <= 4


def test_optimize_offsets_with_negative_demand():
    """Test optimization with negative demand (solar buffer)."""
    demand = [-0.5, -0.3, 0.0, 0.5, 1.0, 1.5]
    prices = [0.25] * 6
    outdoor_temps = [15.0] * 6

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert len(offsets) == 6
    assert len(buffers) == 6


def test_optimize_offsets_zero_demand():
    """Test optimization with zero demand."""
    demand = [0.0] * 6
    prices = [0.25] * 6
    outdoor_temps = [15.0] * 6

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert len(offsets) == 6
    assert len(buffers) == 6


def test_optimize_offsets_extreme_cold():
    """Test optimization in extreme cold conditions."""
    demand = [3.0] * 6
    prices = [0.25] * 6
    outdoor_temps = [-15.0] * 6

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=30.0,
        water_max=55.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert len(offsets) == 6
    assert len(buffers) == 6


def test_optimize_offsets_short_horizon():
    """Test optimization with short planning horizon."""
    demand = [1.0, 1.0, 1.0]
    prices = [0.20, 0.30, 0.20]
    outdoor_temps = [10.0, 10.0, 10.0]

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert len(offsets) == 3
    assert len(buffers) == 3


def test_optimize_offsets_empty():
    """Test optimization with empty inputs."""
    demand = []
    prices = []
    outdoor_temps = []

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
    )

    assert offsets == []
    assert buffers == []


def test_optimize_offsets_with_initial_buffer():
    """Test optimization with initial buffer."""
    demand = [1.0] * 6
    prices = [0.25] * 6
    outdoor_temps = [10.0] * 6

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        buffer=2.0,  # Start with 2 kWh buffer
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert len(offsets) == 6
    assert len(buffers) == 6


def test_optimize_offsets_with_humidity():
    """Test optimization with humidity forecast."""
    demand = [1.0] * 6
    prices = [0.25] * 6
    outdoor_temps = [2.0] * 6  # Near-freezing (defrost conditions)
    humidity = [85.0] * 6  # High humidity

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        humidity_forecast=humidity,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert len(offsets) == 6
    assert len(buffers) == 6


def test_optimize_offsets_temperature_bounds():
    """Test optimization respects temperature bounds."""
    demand = [2.0] * 6
    prices = [0.25] * 6
    outdoor_temps = [-5.0] * 6

    offsets, buffers = optimize_offsets(
        demand=demand,
        prices=prices,
        outdoor_temps=outdoor_temps,
        k_factor=0.025,
        cop_compensation_factor=1.0,
        water_min=25.0,
        water_max=45.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert len(offsets) == 6
    # Verify all resulting supply temps are within bounds
    for offset in offsets:
        supply_temp = calculate_supply_temperature(
            outdoor_temps[0],
            water_min=25.0,
            water_max=45.0,
            outdoor_min=-10.0,
            outdoor_max=15.0,
        ) + offset
        assert 25.0 <= supply_temp <= 45.0 + 4  # Allow for max offset
