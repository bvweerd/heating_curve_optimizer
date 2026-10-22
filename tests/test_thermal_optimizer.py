"""Tests for the thermal DP optimizer.

Energy conservation end-to-end, a brute-force cross-check of the DP search,
comfort never breached, no horizon-end drain, price monotonicity, and slow
drift not being lost to state discretization.
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
from custom_components.heating_curve_optimizer.helpers import offset_ramp
from custom_components.heating_curve_optimizer.thermal_optimizer import (
    ThermalModel,
    optimize_thermal_schedule,
)


def _make_system(**overrides):
    comfort_min = overrides.pop("comfort_min", 19.5)
    comfort_max = overrides.pop("comfort_max", 20.5)
    building = BuildingConfig(
        area_m2=overrides.pop("area_m2", 150),
        energy_label=overrides.pop("energy_label", "C"),
        comfort_min=comfort_min,
        comfort_max=comfort_max,
        target_temp=overrides.pop("target_temp", (comfort_min + comfort_max) / 2),
    )
    emitter = EmitterConfig.sized_to_building(
        building, design_outdoor_temp=-10.0, design_supply_temp=45.0
    )
    heatpump = HeatPumpConfig(max_thermal_power_kw=emitter.nominal_power_kw * 1.5)
    return building, heatpump, emitter


def test_optimizer_conserves_energy_along_chosen_path():
    """Reconstruct the indoor-temperature trajectory independently from the
    returned thermal power: the reported temperatures are the continuous
    physics, not grid-snapped values."""
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
        # The optimizer integrates in quarter-hour substeps.
        next_t = t_in
        for _ in range(4):
            next_t = building.next_indoor_temp(
                next_t,
                outdoor_temp=outdoor[t],
                heat_input_kw=result.thermal_power_kw[t],
                step_hours=0.25,
            )
        assert next_t == pytest.approx(result.indoor_temps[t], abs=2e-3)
        t_in = next_t


def test_comfort_band_never_breached_in_normal_conditions():
    """Under conditions the emitter/heat pump were sized for, the optimizer
    must keep indoor temperature within a tight tolerance of the comfort band.
    A small excursion (<0.1 K) is acceptable — the quadratic comfort penalty
    makes larger violations very expensive, but does not hard-forbid them.
    Here an off hour during an 8x price spike just touches the floor."""
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

    assert all(19.0 - 0.1 <= t <= 21.0 + 0.1 for t in result.indoor_temps)


def test_out_of_range_current_offset_does_not_raise():
    """`current_offset` (the coordinator's persisted last offset) is only
    ever written back from this function's own offsets[0], so it should
    stay within [offset_min, offset_max] in practice - but the DP's action
    table is keyed only on that range, so a stale/out-of-range value
    (e.g. left over from a config change that narrowed the offset bounds)
    must degrade gracefully to the nearest valid offset instead of
    KeyError-ing."""
    building, heatpump, emitter = _make_system()
    horizon = 6
    outdoor = [0.0] * horizon
    prices = [0.20] * horizon

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=20.0,
        time_base=60,
        current_offset=99,
    )

    assert len(result.offsets) == horizon


def test_own_thermostat_stops_heat_above_target():
    """A heat pump with its own room thermostat delivers no heat while the
    room is above the middle of the thermostat's band (target + 0.15 K),
    whatever the curve could still give, and then holds it there; the plan
    has no "pump off" of its own."""
    building, heatpump, emitter = _make_system(comfort_min=19.5, comfort_max=20.5)
    building.target_temp = 20.0

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[5.0] * 16,
        prices=[0.25] * 16,
        initial_indoor_temp=20.4,
        water_min=25.0,
        water_max=45.0,
        outdoor_min=-20.0,
        outdoor_max=15.0,
        time_base=15,
        offset_delta_t=15,
        pump_follows_plan=False,
    )

    assert result.thermal_power_kw[0] == pytest.approx(0.0)
    # No heat delivered means the plan does not report the pump as on.
    assert not result.pump_on[0]
    # Once there, the thermostat holds the room at target + 0.15 K, even at
    # the highest offset.
    assert max(result.indoor_temps[8:]) <= 20.15 + 1e-3
    assert any(power > 0 for power in result.thermal_power_kw)
    # Without a plan-driven off, coasting is the thermostat's doing only.
    assert all(
        on == (power > 0)
        for on, power in zip(result.pump_on, result.thermal_power_kw, strict=True)
    )


def test_own_thermostat_full_output_below_its_band():
    """Below the thermostat's band the curve delivers its full output: the
    output is not tapered down near the target, so in cold weather the
    room can still be warmed up to the thermostat's hold point."""
    building, heatpump, emitter = _make_system(comfort_min=19.5, comfort_max=20.5)
    building.target_temp = 20.0
    common = {
        "building": building,
        "heatpump": heatpump,
        "emitter": emitter,
        "outdoor_temps": [-5.0] * 8,
        "prices": [0.10] * 4 + [0.50] * 4,
        "initial_indoor_temp": 19.9,
        "water_min": 25.0,
        "water_max": 45.0,
        "outdoor_min": -10.0,
        "outdoor_max": 15.0,
        "time_base": 60,
        "pump_follows_plan": False,
    }
    result = optimize_thermal_schedule(**common)
    # Cheap first: the plan warms the room up to the hold point.
    assert max(result.indoor_temps[:4]) == pytest.approx(20.15, abs=0.02)


def _a_plus_underfloor() -> tuple[BuildingConfig, HeatPumpConfig, EmitterConfig]:
    building = BuildingConfig(
        area_m2=159,
        energy_label="A+",
        comfort_min=19.7,
        comfort_max=20.7,
        target_temp=20.0,
    )
    emitter = EmitterConfig.sized_to_building(
        building,
        design_outdoor_temp=-10.0,
        design_supply_temp=33.0,
        emitter_type="underfloor",
    )
    return building, HeatPumpConfig(max_thermal_power_kw=9.0), emitter


def test_preheat_uses_the_whole_comfort_band():
    """Regression: output was cut at target + 0.3 K, so heat could only be
    stored in the lower part of the band ahead of a price peak."""
    building, heatpump, emitter = _a_plus_underfloor()
    prices = [0.15] * 8 + [0.45] * 8 + [0.15] * 8

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[2.0] * 24,
        prices=prices,
        initial_indoor_temp=20.0,
        water_min=27.0,
        water_max=33.0,
        outdoor_min=-10.0,
        outdoor_max=18.0,
        time_base=60,
    )

    assert max(result.indoor_temps[:8]) > building.target_temp + 0.4
    assert max(result.indoor_temps) <= building.comfort_max + 0.05


def test_pump_off_allowed_just_below_band_at_a_price_peak():
    """Regression: below comfort_min the off action was forbidden, so a room
    just under the band was heated at the day's peak price, although the
    price drops and the sun warms the house from the next step on. The
    comfort penalty decides instead."""
    building, heatpump, emitter = _a_plus_underfloor()
    horizon = 16

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[10.0] * horizon,
        prices=[0.60] + [0.20] * (horizon - 1),
        solar_gain_kw=[0.5] + [4.0] * 8 + [0.0] * (horizon - 9),
        initial_indoor_temp=19.68,
        water_min=27.0,
        water_max=33.0,
        outdoor_min=-10.0,
        outdoor_max=18.0,
        time_base=15,
        offset_delta_t=7,
    )

    assert not result.pump_on[0]
    assert result.thermal_power_kw[0] == 0.0
    assert any(result.pump_on[1:])
    assert min(result.indoor_temps) >= building.comfort_min - 0.1


def test_pump_off_action_cycles_instead_of_trickling_at_curve_floor():
    """With the curve floor above the room temperature, the emitter would
    otherwise always deliver heat. The explicit "pump off" action must let
    the optimizer switch the pump off while the room is inside the band, so
    the plan cycles instead of trickling heat indefinitely."""
    building = BuildingConfig(
        area_m2=159,
        energy_label="A+",
        comfort_min=19.7,
        comfort_max=20.5,
        target_temp=20.0,
    )
    emitter = EmitterConfig.sized_to_building(
        building,
        design_outdoor_temp=-10.0,
        design_supply_temp=37.0,
        emitter_type="underfloor",
    )
    heatpump = HeatPumpConfig(max_thermal_power_kw=emitter.nominal_power_kw * 1.3)

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[11.0] * 24,
        prices=[0.25] * 24,
        initial_indoor_temp=20.2,
        water_min=27.0,
        water_max=37.0,
        outdoor_min=-10.0,
        outdoor_max=18.0,
        time_base=60,
        offset_min=-4,
        offset_max=4,
        current_offset=-3,
    )

    assert any(p == 0.0 for p in result.thermal_power_kw)
    assert any(p > 0.0 for p in result.thermal_power_kw)
    assert any(not on for on in result.pump_on)
    assert min(result.indoor_temps) >= building.comfort_min - 0.05


def test_pump_off_coasts_below_target_through_expensive_hours():
    """Regression: with the off action gated at target the plan stayed "on"
    nearly all day, because any room below target had to be heated. The band
    below target must be usable for coasting through a price peak."""
    building = BuildingConfig(
        area_m2=159,
        energy_label="A+",
        comfort_min=19.7,
        comfort_max=20.5,
        target_temp=20.0,
    )
    emitter = EmitterConfig.sized_to_building(
        building,
        design_outdoor_temp=-10.0,
        design_supply_temp=37.0,
        emitter_type="underfloor",
    )
    heatpump = HeatPumpConfig(max_thermal_power_kw=emitter.nominal_power_kw * 1.3)
    peak = range(7, 10)
    prices = [0.35 if h in peak else 0.15 for h in range(24)]

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[11.0] * 24,
        prices=prices,
        initial_indoor_temp=20.0,
        water_min=27.0,
        water_max=37.0,
        outdoor_min=-10.0,
        outdoor_max=18.0,
        time_base=60,
        offset_min=-4,
        offset_max=4,
    )

    assert all(not result.pump_on[h] for h in peak)
    assert any(
        not result.pump_on[t] and result.indoor_temps[t - 1] < building.target_temp
        for t in range(1, 24)
    )
    assert min(result.indoor_temps) >= building.comfort_min - 0.05


def test_undersized_heat_pump_heats_at_maximum():
    """When the heat pump cannot keep up with a cold snap, the plan must be
    to heat as hard as the curve allows every step - not give up."""
    building = BuildingConfig(
        area_m2=200, energy_label="G", comfort_min=19.5, comfort_max=20.5
    )
    emitter = EmitterConfig.sized_to_building(
        building, design_outdoor_temp=-10.0, design_supply_temp=45.0
    )
    undersized_heatpump = HeatPumpConfig(
        max_thermal_power_kw=0.5, capacity_rating_temp=-15.0
    )
    horizon = 12

    result = optimize_thermal_schedule(
        building=building,
        heatpump=undersized_heatpump,
        emitter=emitter,
        outdoor_temps=[-15.0] * horizon,
        prices=[0.20] * horizon,
        initial_indoor_temp=19.5,
        time_base=60,
    )

    assert len(result.offsets) == horizon
    assert all(q == pytest.approx(0.5) for q in result.thermal_power_kw)
    assert result.indoor_temps[-1] < 19.5


def test_slow_drift_is_not_rounded_away():
    """A drift smaller than half a state-grid cell per step must still be
    seen by the optimizer. With flat prices the plan has to keep the
    building inside the comfort band over a long horizon - a DP that
    rounds each transition to the grid believes a slowly cooling house is
    stable and lets it sag below comfort."""
    building = BuildingConfig(
        area_m2=150, energy_label="C", comfort_min=19.7, comfort_max=20.5
    )
    emitter = EmitterConfig.sized_to_building(
        building, design_outdoor_temp=-20.0, design_supply_temp=45.0
    )
    heatpump = HeatPumpConfig(max_thermal_power_kw=emitter.nominal_power_kw * 1.3)
    horizon = 24

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[5.0] * horizon,
        prices=[0.25] * horizon,
        initial_indoor_temp=20.0,
        water_min=25.0,
        water_max=45.0,
        outdoor_min=-20.0,
        outdoor_max=20.0,
        time_base=60,
    )

    assert min(result.indoor_temps) >= building.comfort_min - 0.05


def test_baseline_uses_same_physics_and_savings_account_for_stored_heat():
    """The baseline is the plain curve (offset 0) through the same model;
    with a sufficiently large price difference the optimizer should save
    money by shifting heating toward cheap periods, once stored heat is
    valued via the terminal function."""
    building, heatpump, emitter = _make_system()
    horizon = 12
    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[3.0] * horizon,
        prices=[0.01] * 6 + [0.50] * 6,
        initial_indoor_temp=20.0,
        time_base=60,
    )
    assert len(result.baseline_cost_eur) == horizon
    assert len(result.baseline_indoor_temps) == horizon
    assert result.cost_savings_eur > 0


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


def _brute_force_best(model, *, horizon, offsets, max_change, hold, ramp0, cycling):
    """Cheapest action sequence by exhaustive enumeration (None = pump off).

    A change of offset needs `hold` full periods since the last change,
    as the DP's ramp mode does.
    """
    terminal = model.terminal_value_fn()

    def objective(sequence):
        t_in = model.mass_for_air(0, 20.0)
        prev, ramp, total = 0, ramp0, 0.0
        for t, action in enumerate(sequence):
            if action is None:
                outcome = model.step(t, t_in, prev, pump_on=False)
                ramp = min(hold, ramp + 1)
            else:
                if action != prev:
                    if ramp < hold or abs(action - prev) > max_change:
                        return math.inf
                    ramp = 1
                else:
                    ramp = min(hold, ramp + 1)
                outcome = model.step(t, t_in, action)
                total += cycling * (action - prev) ** 2
                prev = action
            total += model.stage_cost(t, outcome)
            t_in = outcome.next_t_in
        return total + terminal(t_in)

    best = min(
        objective(sequence)
        for sequence in itertools.product([None, *offsets], repeat=horizon)
    )
    return best, objective


@pytest.mark.parametrize(
    ("time_base", "offset_delta_t", "durations"),
    [(60, 60, None), (15, 30, None), (60, 30, [0.5, 1.0, 1.0, 1.0])],
)
def test_dp_matches_brute_force_on_small_horizon(time_base, offset_delta_t, durations):
    """Cross-check the DP search against exhaustive enumeration of every
    ramp-respecting action sequence, simulated with the same model and
    scored with the same objective. The DP works on an interpolated value
    function, so its plan may be marginally worse than the true optimum,
    but never by more than a small margin."""
    building = BuildingConfig(
        area_m2=100, energy_label="C", comfort_min=19.5, comfort_max=20.5
    )
    emitter = EmitterConfig.sized_to_building(
        building, design_outdoor_temp=-10.0, design_supply_temp=45.0
    )
    heatpump = HeatPumpConfig(max_thermal_power_kw=emitter.nominal_power_kw * 1.5)

    horizon = 4
    outdoor = [2.0, 0.0, 4.0, 1.0]
    prices = [0.10, 0.35, 0.15, 0.30]
    hours = durations or [time_base / 60] * horizon
    curve = {
        "water_min": 20.0,
        "water_max": 45.0,
        "outdoor_min": -20.0,
        "outdoor_max": 15.0,
    }
    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=20.0,
        step_durations_hours=hours,
        time_base=time_base,
        offset_delta_t=offset_delta_t,
        offset_min=-2,
        offset_max=2,
        current_offset=0,
        comfort_penalty_weight=50.0,
        cycling_penalty_weight=0.01,
        **curve,
    )

    model = ThermalModel(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor=outdoor,
        price=prices,
        solar=[0.0] * horizon,
        pv=[0.0] * horizon,
        feed_in=[0.07] * horizon,
        humidity=[80.0] * horizon,
        floors=[building.comfort_min] * horizon,
        durations=hours,
        comfort_penalty_weight=50.0,
        hard_floor_penalty=1000.0,
        state_margin=1.5,
        **curve,
    )
    max_change, hold = offset_ramp(time_base, offset_delta_t)
    # The brute force counts every step; the half-hour first step of the
    # last case must not count, so it is checked with that step held.
    best, objective = _brute_force_best(
        model,
        horizon=horizon,
        offsets=range(-2, 3),
        max_change=max_change,
        hold=hold,
        ramp0=hold,
        cycling=0.01,
    )
    dp_sequence = tuple(
        None if not result.pump_on[t] else result.offsets[t] for t in range(horizon)
    )
    assert objective(dp_sequence) <= best + 0.02


def test_ramp_limit_holds_in_real_time():
    """Quarter-hour periods at 30 min/K: one 1 K change per two periods
    (2 K per hour), not one per period; a change the previous run made
    still holds back the next one."""
    building, heatpump, emitter = _a_plus_underfloor()
    horizon = 16
    common = {
        "building": building,
        "heatpump": heatpump,
        "emitter": emitter,
        # Cheap, then very expensive: the plan wants to swing the offset.
        "outdoor_temps": [0.0] * horizon,
        "prices": [0.05] * 8 + [0.80] * 8,
        "initial_indoor_temp": 19.8,
        "water_min": 27.0,
        "water_max": 33.0,
        "outdoor_min": -10.0,
        "outdoor_max": 18.0,
        "time_base": 15,
        "offset_delta_t": 30,
        "current_offset": -4,
    }

    def changes(offsets, start):
        sequence = [start, *offsets]
        return [i for i in range(1, len(sequence)) if sequence[i] != sequence[i - 1]]

    result = optimize_thermal_schedule(**common)
    steps = changes(result.offsets, -4)
    assert steps
    assert all(b - a >= 2 for a, b in itertools.pairwise(steps))
    assert all(abs(b - a) <= 1 for a, b in itertools.pairwise([-4, *result.offsets]))

    held = optimize_thermal_schedule(**common, initial_ramp_steps=1)
    assert held.offsets[0] == -4


def test_partial_first_step_does_not_count_towards_minimums():
    """A first step shorter than a period (the rest of the current one)
    does not count: a run that has just started still lasts the full
    minimum of four periods after it, however expensive."""
    building, heatpump, emitter = _a_plus_underfloor()
    horizon = 12
    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[10.0] * horizon,
        prices=[1.00] * horizon,
        initial_indoor_temp=20.2,
        water_min=27.0,
        water_max=33.0,
        outdoor_min=-10.0,
        outdoor_max=18.0,
        time_base=15,
        offset_delta_t=7,
        step_durations_hours=[0.05] + [0.25] * (horizon - 1),
        min_on_steps=4,
        min_off_steps=4,
        initial_pump_on=True,
        initial_mode_steps=0,
    )
    assert result.pump_on[:5] == [True] * 5
    assert result.pump_on[5] is False


def test_terminal_value_is_concave_and_needs_heat_demand():
    """Stored heat is worth less per kelvin the more there is (it leaks
    while it is used up) and nothing when the house needs no heat."""
    building, heatpump, emitter = _make_system()

    def terminal(outdoor):
        model = ThermalModel(
            building=building,
            heatpump=heatpump,
            emitter=emitter,
            outdoor=[outdoor] * 4,
            price=[0.30] * 4,
            solar=[0.0] * 4,
            pv=[0.0] * 4,
            feed_in=[0.07] * 4,
            humidity=[80.0] * 4,
            floors=[building.comfort_min] * 4,
            durations=[1.0] * 4,
            water_min=25.0,
            water_max=45.0,
            outdoor_min=-20.0,
            outdoor_max=15.0,
            comfort_penalty_weight=50.0,
            hard_floor_penalty=1000.0,
            state_margin=1.5,
        )
        return model.terminal_value_fn()

    cold = terminal(0.0)
    floor = building.comfort_min
    first = cold(floor + 0.5) - cold(floor)
    second = cold(floor + 1.0) - cold(floor + 0.5)
    assert first < second < 0  # value, as a negative cost, with less gain
    # A deficit costs linearly.
    assert cold(floor) == pytest.approx(0.0)
    assert cold(floor - 1.0) == pytest.approx(2 * cold(floor - 0.5))
    assert cold(floor - 1.0) > 0
    warm = terminal(25.0)
    assert warm(floor + 1.0) == 0.0


def test_measured_air_offset_fades_out():
    """A measured room temperature off the model's air (mass-air) is the
    start of the plan, and the difference fades over a few hours."""
    building, heatpump, emitter = _make_system()
    building.air_coupling_kw_per_k = 8.0
    model = ThermalModel(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor=[5.0] * 4,
        price=[0.25] * 4,
        solar=[0.0] * 4,
        pv=[0.0] * 4,
        feed_in=[0.07] * 4,
        humidity=[80.0] * 4,
        floors=[building.comfort_min] * 4,
        durations=[3.0] * 4,
        water_min=25.0,
        water_max=45.0,
        outdoor_min=-20.0,
        outdoor_max=15.0,
        comfort_penalty_weight=50.0,
        hard_floor_penalty=1000.0,
        state_margin=1.5,
        initial_air_offset=-0.5,
    )
    mass = 20.5
    plain_air = building.air_temp(mass, outdoor_temp=5.0)
    assert model.air(0, mass, 0.0) == pytest.approx(plain_air - 0.5)
    assert model.air(0, mass, 3.0) == pytest.approx(plain_air - 0.5 / math.e)
    assert model.mass_for_air(0, plain_air - 0.5, 0.0) == pytest.approx(mass)

    common = {
        "building": building,
        "heatpump": heatpump,
        "emitter": emitter,
        "outdoor_temps": [5.0] * 12,
        "prices": [0.25] * 12,
        "initial_indoor_temp": 20.0,
        "initial_mass_temp": mass,
        "time_base": 60,
    }
    plain = optimize_thermal_schedule(**common)
    shifted = optimize_thermal_schedule(**common, initial_air_offset=-0.5)
    assert shifted.indoor_temps[0] < plain.indoor_temps[0] - 0.1


def test_away_setback_coasts_and_is_back_in_band_on_return():
    """A lower floor while away lets the plan coast through expensive away
    hours and reheat in the cheap hours before the return, cheaper than
    holding the full band."""
    building, heatpump, emitter = _a_plus_underfloor()
    horizon = 24
    away = range(2, 11)
    floors = [17.7 if t in away else building.comfort_min for t in range(horizon)]
    prices = [0.40 if 2 <= t < 9 else 0.20 for t in range(horizon)]
    common = {
        "building": building,
        "heatpump": heatpump,
        "emitter": emitter,
        "outdoor_temps": [0.0] * horizon,
        "prices": prices,
        "initial_indoor_temp": 20.0,
        "water_min": 27.0,
        "water_max": 33.0,
        "outdoor_min": -10.0,
        "outdoor_max": 18.0,
        "time_base": 60,
    }

    home = optimize_thermal_schedule(**common)
    setback = optimize_thermal_schedule(**common, comfort_min_per_step=floors)

    assert min(setback.indoor_temps[2:11]) < building.comfort_min - 0.3
    assert min(setback.indoor_temps[2:11]) >= 17.7 - 0.05
    assert setback.indoor_temps[11] >= building.comfort_min - 0.05
    assert setback.total_cost_eur < home.total_cost_eur
    # A floor equal to the band is the same as no schedule.
    same = optimize_thermal_schedule(
        **common, comfort_min_per_step=[building.comfort_min] * horizon
    )
    assert same.offsets == home.offsets


def _runs(pump_on: list[bool]) -> list[tuple[bool, int]]:
    runs: list[tuple[bool, int]] = []
    for on in pump_on:
        if runs and runs[-1][0] == on:
            runs[-1] = (on, runs[-1][1] + 1)
        else:
            runs.append((on, 1))
    return runs


def _cycling_case(**kwargs):
    """15-minute steps with prices alternating every step: an unconstrained
    plan chases them with short runs."""
    building, heatpump, emitter = _a_plus_underfloor()
    horizon = 32
    kwargs.setdefault("prices", [0.15 if t % 2 else 0.45 for t in range(horizon)])
    return optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[10.0] * horizon,
        initial_indoor_temp=20.2,
        water_min=27.0,
        water_max=33.0,
        outdoor_min=-10.0,
        outdoor_max=18.0,
        time_base=15,
        offset_delta_t=7,
        **kwargs,
    )


def test_minimum_run_and_pause_time():
    free = _cycling_case()
    assert min(length for _, length in _runs(free.pump_on)) == 1

    held = _cycling_case(min_on_steps=4, min_off_steps=4, initial_pump_on=False)
    runs = _runs(held.pump_on)
    # Every run and pause lasts at least an hour, except the one cut off
    # by the end of the horizon (the first may continue a long pause).
    assert all(length >= 4 for _, length in runs[1:-1])


def test_current_run_is_held_until_its_minimum():
    expensive = [1.00] * 32
    free = _cycling_case(prices=expensive, initial_pump_on=True)
    assert free.pump_on[0] is False
    held = _cycling_case(
        min_on_steps=4,
        min_off_steps=4,
        initial_pump_on=True,
        initial_mode_steps=1,
        prices=expensive,  # the plan would rather stop at once
    )
    assert held.pump_on[:3] == [True, True, True]
