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
from custom_components.heating_curve_optimizer.helpers import (
    calculate_supply_temperature,
)
from custom_components.heating_curve_optimizer.thermal_optimizer import (
    comfort_penalty,
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
        next_t = building.next_indoor_temp(
            t_in,
            outdoor_temp=outdoor[t],
            heat_input_kw=result.thermal_power_kw[t],
            step_hours=1.0,
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


def test_no_heat_delivery_at_comfort_ceiling():
    """The comfort ceiling must cut heat delivery to zero at comfort_max,
    even when the emitter curve still has capacity to give."""
    building, heatpump, emitter = _make_system(comfort_min=19.5, comfort_max=20.5)
    building.target_temp = 20.0

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[5.0] * 4,
        prices=[0.25] * 4,
        initial_indoor_temp=20.5,  # = comfort_max
        water_min=25.0,
        water_max=45.0,
        outdoor_min=-20.0,
        outdoor_max=15.0,
        time_base=60,
    )

    assert result.thermal_power_kw[0] == pytest.approx(0.0)
    # No heat delivered means the plan does not report the pump as on.
    assert not result.pump_on[0]


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
    undersized_heatpump = HeatPumpConfig(max_thermal_power_kw=0.5)
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


def test_dp_matches_brute_force_on_small_horizon():
    """Cross-check the DP search against exhaustive enumeration of every
    rate-limit-respecting offset sequence, simulated with the same
    continuous physics and scored with the same objective. The DP works
    on an interpolated value function, so its plan may be marginally
    worse than the true optimum, but never by more than a small margin."""
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
    kwargs = {
        "water_min": 20.0,
        "water_max": 45.0,
        "outdoor_min": -20.0,
        "outdoor_max": 15.0,
        "comfort_penalty_weight": 50.0,
        "cycling_penalty_weight": 0.01,
    }
    initial_indoor_temp = 20.0

    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=outdoor,
        prices=prices,
        initial_indoor_temp=initial_indoor_temp,
        time_base=60,
        offset_delta_t=60,
        offset_min=-2,
        offset_max=2,
        current_offset=0,
        **kwargs,
    )

    base_supply = [
        calculate_supply_temperature(
            outdoor[t],
            water_min=kwargs["water_min"],
            water_max=kwargs["water_max"],
            outdoor_min=kwargs["outdoor_min"],
            outdoor_max=kwargs["outdoor_max"],
        )
        for t in range(horizon)
    ]
    terminal_cop = heatpump.cop_at(
        supply_temp=base_supply[-1], outdoor_temp=outdoor[-1]
    )

    def objective(sequence: tuple[int | None, ...]) -> float:
        t_in = initial_indoor_temp
        prev = 0
        total = 0.0
        for t, action in enumerate(sequence):
            if action is None:
                # Explicit "pump off": offset unchanged.
                offset = prev
                q = 0.0
                supply = min(
                    kwargs["water_max"],
                    max(kwargs["water_min"], base_supply[t] + offset),
                )
            else:
                offset = action
                supply = min(
                    kwargs["water_max"],
                    max(kwargs["water_min"], base_supply[t] + offset),
                )
                q = min(
                    emitter.available_power_kw(supply_temp=supply, indoor_temp=t_in),
                    heatpump.max_thermal_power_kw,
                )
                # Comfort ceiling mirrored from optimize_thermal_schedule.
                if t_in >= building.comfort_max:
                    q = 0.0
                elif t_in > building.comfort_max - 0.3:
                    q *= (building.comfort_max - t_in) / 0.3
            cop = heatpump.cop_at(supply_temp=supply, outdoor_temp=outdoor[t])
            p_elec = q / cop if q > 0 else 0.0
            t_in = building.next_indoor_temp(
                t_in, outdoor_temp=outdoor[t], heat_input_kw=q, step_hours=1.0
            )
            total += (
                p_elec * prices[t]
                + comfort_penalty(
                    t_in,
                    comfort_min=building.comfort_min,
                    comfort_max=building.comfort_max,
                    weight=kwargs["comfort_penalty_weight"],
                    hard_floor=building.comfort_min - 1.5,
                )
                + (
                    0.0
                    if action is None
                    else kwargs["cycling_penalty_weight"] * (offset - prev) ** 2
                )
            )
            if action is not None:
                prev = offset
        return total - (t_in - building.comfort_min) * (
            building.thermal_mass_kwh_per_k * prices[-1] / terminal_cop
        )

    actions = [None, -2, -1, 0, 1, 2]
    best = math.inf
    for sequence in itertools.product(actions, repeat=horizon):
        prev = 0
        valid = True
        for action in sequence:
            if action is not None and abs(action - prev) > 1:
                valid = False
                break
            if action is not None:
                prev = action
        if valid:
            best = min(best, objective(sequence))

    dp_sequence = tuple(
        None if not result.pump_on[t] else result.offsets[t] for t in range(horizon)
    )
    assert objective(dp_sequence) <= best + 0.02


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
