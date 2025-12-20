"""Tests for buffer energy calculation with initial buffer."""

import pytest
from custom_components.heating_curve_optimizer.optimizer import calculate_buffer_energy


def test_calculate_buffer_energy_starts_at_zero():
    """Test that buffer energy calculation starts at 0 when no initial buffer."""
    offsets = [-2, -2, -1, 0, 1]
    demand = [5.0, 5.0, 5.0, 5.0, 5.0]
    time_base = 60  # 1 hour steps

    result = calculate_buffer_energy(offsets, demand, time_base=time_base, buffer=0.0)

    # Step 0: 0 + (-2) × 5.0 × 0.15 × 1.0 = -1.5
    # Step 1: -1.5 + (-2) × 5.0 × 0.15 × 1.0 = -3.0
    # Step 2: -3.0 + (-1) × 5.0 × 0.15 × 1.0 = -3.75
    # Step 3: -3.75 + (0) × 5.0 × 0.15 × 1.0 = -3.75
    # Step 4: -3.75 + (1) × 5.0 × 0.15 × 1.0 = -3.0

    assert len(result) == 5
    assert result[0] == -1.5
    assert result[1] == -3.0
    assert result[2] == -3.75
    assert result[3] == -3.75  # No change when offset is 0
    assert result[4] == -3.0


def test_calculate_buffer_energy_with_initial_buffer():
    """Test that buffer energy calculation respects initial buffer value."""
    offsets = [-2, -2, -1, 0, 1]
    demand = [5.0, 5.0, 5.0, 5.0, 5.0]
    time_base = 60
    initial_buffer = 3.0

    result = calculate_buffer_energy(
        offsets, demand, time_base=time_base, buffer=initial_buffer
    )

    # Step 0: 3.0 + (-2) × 5.0 × 0.15 × 1.0 = 3.0 - 1.5 = 1.5
    # Step 1: 1.5 + (-2) × 5.0 × 0.15 × 1.0 = 1.5 - 1.5 = 0.0
    # Step 2: 0.0 + (-1) × 5.0 × 0.15 × 1.0 = 0.0 - 0.75 = -0.75
    # Step 3: -0.75 + (0) × 5.0 × 0.15 × 1.0 = -0.75
    # Step 4: -0.75 + (1) × 5.0 × 0.15 × 1.0 = -0.75 + 0.75 = 0.0

    assert len(result) == 5
    assert result[0] == 1.5
    assert result[1] == 0.0
    assert result[2] == -0.75
    assert result[3] == -0.75  # No change when offset is 0
    assert result[4] == 0.0


def test_calculate_buffer_energy_offset_zero_maintains_buffer():
    """Test that offset of 0 maintains buffer level (doesn't drain it)."""
    offsets = [2, 0, 0, 0, -2]
    demand = [4.0, 4.0, 4.0, 4.0, 4.0]
    time_base = 60

    result = calculate_buffer_energy(offsets, demand, time_base=time_base, buffer=0.0)

    # Step 0: 0 + 2 × 4.0 × 0.15 × 1.0 = 1.2
    # Step 1: 1.2 + 0 × 4.0 × 0.15 × 1.0 = 1.2 (STAYS THE SAME)
    # Step 2: 1.2 + 0 × 4.0 × 0.15 × 1.0 = 1.2 (STAYS THE SAME)
    # Step 3: 1.2 + 0 × 4.0 × 0.15 × 1.0 = 1.2 (STAYS THE SAME)
    # Step 4: 1.2 + (-2) × 4.0 × 0.15 × 1.0 = 1.2 - 1.2 = 0.0

    assert result[0] == 1.2
    assert result[1] == 1.2
    assert result[2] == 1.2
    assert result[3] == 1.2
    assert result[4] == 0.0


def test_calculate_buffer_energy_gradual_depletion():
    """Test that buffer depletes gradually based on offset magnitude and demand."""
    offsets = [-1, -1, -1, -1]
    demand = [3.0, 4.0, 5.0, 6.0]  # Varying demand
    time_base = 60
    initial_buffer = 2.0

    result = calculate_buffer_energy(
        offsets, demand, time_base=time_base, buffer=initial_buffer
    )

    # Step 0: 2.0 + (-1) × 3.0 × 0.15 × 1.0 = 2.0 - 0.45 = 1.55
    # Step 1: 1.55 + (-1) × 4.0 × 0.15 × 1.0 = 1.55 - 0.6 = 0.95
    # Step 2: 0.95 + (-1) × 5.0 × 0.15 × 1.0 = 0.95 - 0.75 = 0.2
    # Step 3: 0.2 + (-1) × 6.0 × 0.15 × 1.0 = 0.2 - 0.9 = -0.7

    assert result[0] == 1.55
    assert result[1] == 0.95
    assert result[2] == 0.2
    assert result[3] == -0.7

    # Buffer depletes at different rates based on demand
    depletion_0_1 = result[0] - result[1]  # 0.6
    depletion_1_2 = result[1] - result[2]  # 0.75
    depletion_2_3 = result[2] - result[3]  # 0.9

    assert depletion_0_1 < depletion_1_2 < depletion_2_3  # Increasing depletion


def test_calculate_buffer_energy_matches_user_diagnostics():
    """Test using real values from user diagnostics to verify the fix."""
    # From user's diagnostics:
    # "future_offsets": [-2, -2, -2, -1, 0, 0, 0, 0, 0, 0, 1, 1]
    # "current_buffer": 3.0
    # "raw_net_heat_forecast": [4.91, 4.809, 4.588, 4.404, 4.26, 4.123, ...]

    offsets = [-2, -2, -2, -1, 0, 0]
    demand = [4.91, 4.809, 4.588, 4.404, 4.26, 4.123]
    time_base = 60
    initial_buffer = 3.0

    result = calculate_buffer_energy(
        offsets, demand, time_base=time_base, buffer=initial_buffer
    )

    # With initial buffer of 3.0:
    # Step 0: 3.0 + (-2) × 4.91 × 0.15 × 1.0 = 3.0 - 1.473 = 1.527
    # Step 1: 1.527 + (-2) × 4.809 × 0.15 × 1.0 = 1.527 - 1.443 = 0.084
    # Step 2: 0.084 + (-2) × 4.588 × 0.15 × 1.0 = 0.084 - 1.376 = -1.292
    # Step 3: -1.292 + (-1) × 4.404 × 0.15 × 1.0 = -1.292 - 0.661 = -1.953
    # Step 4: -1.953 + (0) × 4.26 × 0.15 × 1.0 = -1.953 (NO CHANGE!)
    # Step 5: -1.953 + (0) × 4.123 × 0.15 × 1.0 = -1.953 (NO CHANGE!)

    assert abs(result[0] - 1.527) < 0.01
    assert abs(result[1] - 0.084) < 0.01
    assert abs(result[2] - (-1.292)) < 0.01
    assert abs(result[3] - (-1.953)) < 0.01
    assert abs(result[4] - (-1.953)) < 0.01  # Should stay the same!
    assert abs(result[5] - (-1.953)) < 0.01  # Should stay the same!
