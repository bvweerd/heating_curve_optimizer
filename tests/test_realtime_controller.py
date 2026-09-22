"""Tests for realtime_controller.py (phase 5b, docs/redesign/REDESIGN.md)."""

from __future__ import annotations

from custom_components.heating_curve_optimizer.realtime_controller import (
    RealtimeController,
    RealtimeControllerConfig,
    create_realtime_controller,
)


def _controller(deadband_w: float = 300.0) -> RealtimeController:
    return RealtimeController(RealtimeControllerConfig(deadband_w=deadband_w))


def test_holds_within_deadband():
    controller = _controller(deadband_w=300.0)
    adjustment = controller.calculate_adjustment(
        current_grid_w=100.0, shadow_price_eur_per_kwh=0.05, max_adjustment=4
    )
    assert adjustment == 0


def test_exporting_with_positive_shadow_price_increases_offset():
    controller = _controller(deadband_w=300.0)
    adjustment = controller.calculate_adjustment(
        current_grid_w=-500.0, shadow_price_eur_per_kwh=0.05, max_adjustment=4
    )
    assert adjustment == 1


def test_exporting_with_zero_shadow_price_does_not_increase_offset():
    """No point soaking up surplus once the building is already at/above
    the comfort ceiling - the shadow price says so via <= 0."""
    controller = _controller(deadband_w=300.0)
    adjustment = controller.calculate_adjustment(
        current_grid_w=-500.0, shadow_price_eur_per_kwh=0.0, max_adjustment=4
    )
    assert adjustment == 0


def test_importing_decreases_offset():
    controller = _controller(deadband_w=300.0)
    controller.reset(2)
    adjustment = controller.calculate_adjustment(
        current_grid_w=500.0, shadow_price_eur_per_kwh=0.05, max_adjustment=4
    )
    assert adjustment == 1


def test_adjustment_clamped_to_max_adjustment():
    controller = _controller(deadband_w=300.0)
    for _ in range(10):
        adjustment = controller.calculate_adjustment(
            current_grid_w=-500.0, shadow_price_eur_per_kwh=0.05, max_adjustment=2
        )
    assert adjustment == 2


def test_zero_max_adjustment_always_holds_at_zero():
    """When the DP's own ramp-rate limit leaves no room this step, the
    real-time loop must never bypass it."""
    controller = _controller(deadband_w=300.0)
    controller.reset(2)  # pretend a previous cycle had room
    adjustment = controller.calculate_adjustment(
        current_grid_w=-500.0, shadow_price_eur_per_kwh=0.05, max_adjustment=0
    )
    assert adjustment == 0


def test_reset_clears_memory():
    controller = _controller()
    controller.calculate_adjustment(
        current_grid_w=-500.0, shadow_price_eur_per_kwh=0.05, max_adjustment=4
    )
    assert controller.last_adjustment != 0
    controller.reset()
    assert controller.last_adjustment == 0


def test_get_control_action_reports_effective_offset():
    controller = _controller(deadband_w=300.0)
    action = controller.get_control_action(
        current_grid_w=-500.0,
        shadow_price_eur_per_kwh=0.05,
        planned_offset=1,
        max_adjustment=4,
    )
    assert action["adjustment"] == 1
    assert action["effective_offset"] == 2
    assert action["planned_offset"] == 1


def test_create_realtime_controller_reads_config():
    controller = create_realtime_controller({"realtime_deadband_w": 150.0})
    assert controller.config.deadband_w == 150.0


def test_create_realtime_controller_uses_default_without_config():
    controller = create_realtime_controller({})
    assert controller.config.deadband_w == 300.0


def test_no_oscillation_across_a_steady_export_sequence():
    """A steady export just above the deadband should ramp up once and then
    hold, not oscillate, once max_adjustment is reached."""
    controller = _controller(deadband_w=300.0)
    results = [
        controller.calculate_adjustment(
            current_grid_w=-400.0, shadow_price_eur_per_kwh=0.05, max_adjustment=3
        )
        for _ in range(6)
    ]
    assert results == [1, 2, 3, 3, 3, 3]
