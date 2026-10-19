"""Tests for realtime_controller.py (phase 5b, docs/redesign/REDESIGN.md)."""

from __future__ import annotations

import itertools

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


def test_effective_offset_clamped_to_dp_curve_bound():
    """planned_offset already at the DP's absolute bound (+4) plus a
    same-direction adjustment at the ramp-rate limit must not report an
    effective_offset the heating curve was never configured to reach -
    max_adjustment alone only bounds the *ramp*, not the *result*."""
    controller = _controller(deadband_w=300.0)
    controller.reset(6)  # previous cycles already pushed adjustment to the ramp cap
    action = controller.get_control_action(
        current_grid_w=-500.0,
        shadow_price_eur_per_kwh=0.05,
        planned_offset=4,
        max_adjustment=6,
        offset_min=-4,
        offset_max=4,
    )
    assert action["adjustment"] == 6
    assert action["effective_offset"] == 4


def test_import_never_pushes_offset_below_plan():
    """Grid import is the normal state of a running heat pump: it may only
    unwind a surplus-driven increase, never lower the offset below the DP
    plan (the review found the old controller sliding to -max within
    minutes at night)."""
    controller = _controller(deadband_w=300.0)
    for _ in range(10):
        action = controller.get_control_action(
            current_grid_w=2000.0,
            shadow_price_eur_per_kwh=0.05,
            planned_offset=1,
            max_adjustment=6,
            offset_min=-4,
            offset_max=4,
        )
    assert action["adjustment"] == 0
    assert action["effective_offset"] == 1


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


def test_idle_heat_pump_unwinds_adjustment_without_export():
    """An adjustment the compressor is not absorbing must not linger until
    the next start, when it would be applied at the full grid price."""
    controller = _controller(deadband_w=300.0)
    controller.reset(2)
    results = [
        controller.calculate_adjustment(
            current_grid_w=-72.0,
            shadow_price_eur_per_kwh=0.05,
            max_adjustment=2,
            heat_pump_running=False,
        )
        for _ in range(3)
    ]
    assert results == [1, 0, 0]


def test_idle_heat_pump_still_raises_on_export():
    """Export beyond the deadband is what should start the heat pump."""
    controller = _controller(deadband_w=300.0)
    adjustment = controller.calculate_adjustment(
        current_grid_w=-500.0,
        shadow_price_eur_per_kwh=0.05,
        max_adjustment=2,
        heat_pump_running=False,
    )
    assert adjustment == 1


def test_running_heat_pump_holds_within_deadband():
    controller = _controller(deadband_w=300.0)
    controller.reset(2)
    adjustment = controller.calculate_adjustment(
        current_grid_w=-72.0,
        shadow_price_eur_per_kwh=0.05,
        max_adjustment=2,
        heat_pump_running=True,
    )
    assert adjustment == 2


def test_heat_pump_soaking_up_surplus_holds_the_adjustment():
    """Regression for the flapping offset: the import the heat pump itself
    causes while running on surplus must not step the adjustment down."""
    controller = _controller(deadband_w=300.0)
    controller.reset(2)
    # 500 W surplus without the heat pump, which draws 1400 W -> 900 W import.
    adjustment = controller.calculate_adjustment(
        current_grid_w=900.0,
        shadow_price_eur_per_kwh=0.05,
        max_adjustment=2,
        heat_pump_running=True,
        heat_pump_power_w=1400.0,
    )
    assert adjustment == 2


def test_import_beyond_the_heat_pump_steps_down():
    """Without the heat pump the house would still import: surplus gone."""
    controller = _controller(deadband_w=300.0)
    controller.reset(2)
    adjustment = controller.calculate_adjustment(
        current_grid_w=1200.0,
        shadow_price_eur_per_kwh=0.05,
        max_adjustment=2,
        heat_pump_running=True,
        heat_pump_power_w=1000.0,
    )
    assert adjustment == 1


def test_full_building_steps_down():
    controller = _controller(deadband_w=300.0)
    controller.reset(2)
    adjustment = controller.calculate_adjustment(
        current_grid_w=0.0, shadow_price_eur_per_kwh=0.0, max_adjustment=2
    )
    assert adjustment == 1


def test_changes_hold_for_the_minimum_hold_time():
    from datetime import UTC, datetime, timedelta

    controller = RealtimeController(
        RealtimeControllerConfig(deadband_w=300.0, min_hold_s=900)
    )
    t0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

    def step(minutes: float, grid_w: float) -> int:
        return controller.calculate_adjustment(
            current_grid_w=grid_w,
            shadow_price_eur_per_kwh=0.05,
            max_adjustment=2,
            now=t0 + timedelta(minutes=minutes),
        )

    assert step(0, -500.0) == 1
    assert step(1, -500.0) == 1  # held
    assert step(14, 500.0) == 1  # held, also downward
    assert step(15, -500.0) == 2
    assert step(31, 500.0) == 1


def test_export_import_cycle_with_heat_pump_does_not_flap():
    """The minute-by-minute pattern behind the 0/-2 dips: export raises,
    the heat pump starts on the surplus and the grid swings to import."""
    from datetime import UTC, datetime, timedelta

    controller = _controller(deadband_w=300.0)
    t0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    results = []
    for minute in range(60):
        running = minute % 4 >= 1
        results.append(
            controller.calculate_adjustment(
                current_grid_w=900.0 if running else -500.0,
                shadow_price_eur_per_kwh=0.05,
                max_adjustment=2,
                heat_pump_running=running,
                heat_pump_power_w=1400.0 if running else 0.0,
                now=t0 + timedelta(minutes=minute),
            )
        )
    assert results[0] == 1
    # Never steps back down while the heat pump runs on surplus.
    assert all(b >= a for a, b in itertools.pairwise(results))
    assert results[-1] == 2
