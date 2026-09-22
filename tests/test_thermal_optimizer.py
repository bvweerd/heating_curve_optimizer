"""Tests for the redesigned thermal DP optimizer.

Covers the specific failure modes diagnosed in docs/redesign/REDESIGN.md §2.1
and the test strategy laid out in §3.8: energy conservation end-to-end, a
brute-force cross-check of the DP search itself, comfort never breached,
no horizon-end drain, and price monotonicity.
"""

from __future__ import annotations

import itertools
import math

import pytest

from custom_components.heating_curve_optimizer.building_model import (
    BuildingConfig,
    EmitterConfig,
)
from custom_components.heating_curve_optimizer.heatpump_model import HeatPumpConfig
from custom_components.heating_curve_optimizer.helpers import (
    calculate_supply_temperature,
)
from custom_components.heating_curve_optimizer.thermal_optimizer import (
    optimize_thermal_schedule,
)


def _make_system(**overrides):
    building = BuildingConfig(
        area_m2=overrides.pop("area_m2", 150),
        energy_label=overrides.pop("energy_label", "C"),
        comfort_min=overrides.pop("comfort_min", 19.5),
        comfort_max=overrides.pop("comfort_max", 20.5),
    )
    emitter = EmitterConfig.sized_to_building(
        building, design_outdoor_temp=-10.0, design_supply_temp=45.0
    )
    heatpump = HeatPumpConfig(max_thermal_power_kw=emitter.nominal_power_kw * 1.5)
    return building, heatpump, emitter


def test_optimizer_conserves_energy_along_chosen_path():
    """Reconstruct the indoor-temperature trajectory independently from the
    returned thermal power and compare it to the returned indoor_temps -
    they must match to DP snapping resolution. This is the end-to-end
    version of the energy-conservation property in test_building_model.py."""
    building, heatpump, emitter = _make_system()
    horizon = 12
    outdoor = [3.0 - 0.3 * t for t in range(horizon)]
    prices = [0.10 + 0.02 * (t % 5) for t in range(horizon)]

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=20.0,
        time_base=60,
    )

    t_in = 20.0
    for t in range(horizon):
        next_t = building.next_indoor_temp(
            t_in,
            outdoor_temp=outdoor[t],
            heat_input_kw=result.thermal_power_kw[t],
            step_hours=1.0,
        )
        # Snapped to the same 0.1°C ladder the optimizer used internally.
        assert next_t == pytest.approx(result.indoor_temps[t], abs=0.06)
        t_in = result.indoor_temps[t]


def test_comfort_band_never_breached_in_normal_conditions():
    """Under conditions the emitter/heat pump were sized for, the optimizer
    must never let indoor temperature leave the comfort band."""
    building, heatpump, emitter = _make_system(comfort_min=19.0, comfort_max=21.0)
    horizon = 24
    outdoor = [-5.0 + 3 * math.sin(t / 12 * math.pi) for t in range(horizon)]
    prices = [0.05, 0.40] * (horizon // 2)

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=20.0,
        time_base=60,
    )

    assert all(19.0 - 1e-6 <= t <= 21.0 + 1e-6 for t in result.indoor_temps)


def test_hard_floor_never_breached_even_when_undersized():
    """Even when the heat pump is deliberately too small for a cold snap,
    the DP must never report an indoor temperature below the discretized
    ladder's hard floor - REDESIGN.md §2.1.D/E's silent-all-zero failure
    mode must not resurface as a silent floor breach either."""
    building = BuildingConfig(
        area_m2=200, energy_label="G", comfort_min=19.5, comfort_max=20.5
    )
    emitter = EmitterConfig.sized_to_building(
        building, design_outdoor_temp=-10.0, design_supply_temp=45.0
    )
    undersized_heatpump = HeatPumpConfig(max_thermal_power_kw=0.5)
    horizon = 12
    outdoor = [-15.0] * horizon
    prices = [0.20] * horizon

    result = optimize_thermal_schedule(
        building=building,
        heatpump=undersized_heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=19.5,
        time_base=60,
        state_margin=1.5,
    )

    hard_floor = building.comfort_min - 1.5
    assert all(t >= hard_floor - 1e-6 for t in result.indoor_temps)


def test_no_horizon_end_drain():
    """The terminal value function must stop the optimizer from dumping
    stored heat in the final steps just because the horizon ends -
    REDESIGN.md §2.1.E. Under flat prices, indoor temp at the end of the
    horizon should stay within the comfort band, not crash to the floor."""
    building, heatpump, emitter = _make_system()
    horizon = 12
    outdoor = [5.0] * horizon
    prices = [0.20] * horizon  # flat: no price incentive either way

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=20.0,
        time_base=60,
    )

    assert result.indoor_temps[-1] >= building.comfort_min - 0.15


def test_preheats_before_expensive_period_and_coasts_through_it():
    """The core capability the legacy optimizer could not express at all:
    real load-shifting. Given a clear cheap-then-expensive price step, the
    optimizer should raise indoor temperature during the cheap period and
    let it drift down (not add heat) during the expensive one."""
    building, heatpump, emitter = _make_system(comfort_min=19.5, comfort_max=20.5)
    horizon = 8
    outdoor = [3.0] * horizon
    # Cheap for the first half, expensive for the second.
    prices = [0.10] * 4 + [0.50] * 4

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=19.5,
        time_base=60,
    )

    # Rising (or at the ceiling) during the cheap window.
    assert result.indoor_temps[3] > result.indoor_temps[0]
    # No heat added (offset <= 0) at some point during the expensive window.
    assert min(result.offsets[4:]) <= 0
    # Total delivered thermal power in the expensive window must be lower
    # than in the cheap window given equal outdoor conditions.
    assert sum(result.thermal_power_kw[4:]) < sum(result.thermal_power_kw[:4])


def test_higher_price_never_increases_thermal_power_same_step():
    """Monotonicity: raising the price at a single step, all else equal,
    must never make the optimizer draw *more* heat at that step."""
    building, heatpump, emitter = _make_system()
    horizon = 6
    outdoor = [2.0] * horizon
    base_prices = [0.20] * horizon
    high_prices = list(base_prices)
    high_prices[3] = 1.0

    base_result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=base_prices,
        initial_indoor_temp=20.0,
        time_base=60,
    )
    high_result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=high_prices,
        initial_indoor_temp=20.0,
        time_base=60,
    )

    assert high_result.thermal_power_kw[3] <= base_result.thermal_power_kw[3] + 1e-9


def test_dp_matches_brute_force_on_small_horizon():
    """Cross-check the DP search itself (not the physics, which is tested
    independently) against exhaustive enumeration of every rate-limit-
    respecting offset sequence, on a horizon small enough to enumerate.
    Mirrors battery_controller's brute_force_step0.py cross-check
    (REDESIGN.md §3.8 item 2)."""
    building = BuildingConfig(
        area_m2=100, energy_label="C", comfort_min=19.5, comfort_max=20.5
    )
    emitter = EmitterConfig.sized_to_building(
        building, design_outdoor_temp=-10.0, design_supply_temp=45.0
    )
    heatpump = HeatPumpConfig(max_thermal_power_kw=emitter.nominal_power_kw * 1.5)

    horizon = 3
    offset_choices = (-1, 0, 1)
    outdoor = [2.0, 0.0, 4.0]
    prices = [0.10, 0.35, 0.15]
    time_base = 60
    offset_delta_t = 60  # max_offset_change = 1, so offset_choices already cover it
    water_min, water_max = 20.0, 45.0
    outdoor_min, outdoor_max = -20.0, 15.0
    initial_indoor_temp = 20.0
    current_offset = 0
    comfort_penalty_weight = 50.0
    cycling_penalty_weight = 0.01
    state_resolution = 0.1
    state_margin = 1.5

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=initial_indoor_temp,
        time_base=time_base,
        offset_delta_t=offset_delta_t,
        offset_min=-1,
        offset_max=1,
        water_min=water_min,
        water_max=water_max,
        outdoor_min=outdoor_min,
        outdoor_max=outdoor_max,
        current_offset=current_offset,
        comfort_penalty_weight=comfort_penalty_weight,
        cycling_penalty_weight=cycling_penalty_weight,
        state_resolution=state_resolution,
        state_margin=state_margin,
    )

    # --- independent brute-force reference over the same discretization ---
    states = building.discretize_states(
        resolution=state_resolution, margin=state_margin
    )
    state_lo, state_hi = states[0], states[-1]
    n_states = len(states)

    def snap(t_in: float) -> int:
        clamped = min(max(t_in, state_lo), state_hi)
        return round((clamped - state_lo) / (state_hi - state_lo) * (n_states - 1))

    def comfort_penalty(t_in: float) -> float:
        if t_in < state_lo:
            return comfort_penalty_weight * (building.comfort_min - t_in) ** 2 + 1000.0
        if t_in < building.comfort_min:
            return comfort_penalty_weight * (building.comfort_min - t_in) ** 2
        if t_in > building.comfort_max:
            return comfort_penalty_weight * (t_in - building.comfort_max) ** 2
        return 0.0

    base_supply = [
        calculate_supply_temperature(
            outdoor[t],
            water_min=water_min,
            water_max=water_max,
            outdoor_min=outdoor_min,
            outdoor_max=outdoor_max,
        )
        for t in range(horizon)
    ]

    step_hours = 1.0
    # The DP's search objective includes comfort/cycling penalties (soft
    # constraints that shape the search) and the terminal value, but
    # `total_cost_eur` deliberately reports only real currency spent - see
    # thermal_optimizer.py's forward pass. So here: pick the sequence that
    # minimizes the *full* objective (matching what the DP actually
    # searches over), then compare its *energy-only* cost against
    # `result.total_cost_eur`.
    best_objective = math.inf
    best_energy_cost = math.inf

    for sequence in itertools.product(offset_choices, repeat=horizon):
        # Rate limit: max change of 1 per step (matches offset_delta_t=60).
        prev = current_offset
        feasible = True
        for off in sequence:
            if abs(off - prev) > 1:
                feasible = False
                break
            prev = off
        if not feasible:
            continue

        t_in = initial_indoor_temp
        prev_offset = current_offset
        objective = 0.0
        energy_cost_total = 0.0
        for t, offset in enumerate(sequence):
            supply_temp = min(water_max, max(water_min, base_supply[t] + offset))
            q_available = emitter.available_power_kw(
                supply_temp=supply_temp, indoor_temp=states[snap(t_in)]
            )
            q_hp = max(0.0, min(q_available, heatpump.max_thermal_power_kw))
            p_elec = heatpump.electrical_power_kw(
                thermal_power_kw=q_hp,
                supply_temp=supply_temp,
                outdoor_temp=outdoor[t],
            )
            energy_cost = p_elec * prices[t] * step_hours

            next_t = building.next_indoor_temp(
                states[snap(t_in)],
                outdoor_temp=outdoor[t],
                heat_input_kw=q_hp,
                step_hours=step_hours,
            )
            next_idx = snap(next_t)
            objective += (
                energy_cost
                + comfort_penalty(states[next_idx])
                + cycling_penalty_weight * (offset - prev_offset) ** 2
            )
            energy_cost_total += energy_cost
            t_in = states[next_idx]
            prev_offset = offset

        # Match the DP's terminal value.
        terminal_cop = heatpump.cop_at(
            supply_temp=base_supply[-1], outdoor_temp=outdoor[-1]
        )
        stored_above_min = max(0.0, t_in - building.comfort_min)
        terminal_value = (
            -stored_above_min
            * building.thermal_mass_kwh_per_k
            * prices[-1]
            / terminal_cop
        )
        objective += terminal_value

        if objective < best_objective:
            best_objective = objective
            best_energy_cost = energy_cost_total

    assert result.total_cost_eur == pytest.approx(best_energy_cost, abs=1e-3)
