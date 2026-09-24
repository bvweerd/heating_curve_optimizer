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
    under flat prices optimizing can never be worse than the baseline once
    the heat left in the building is valued."""
    building, heatpump, emitter = _make_system()
    horizon = 12
    result = optimize_thermal_schedule(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor_temps=[3.0] * horizon,
        prices=[0.10] * 6 + [0.40] * 6,
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

    def objective(sequence: tuple[int, ...]) -> float:
        t_in = initial_indoor_temp
        prev = 0
        total = 0.0
        for t, offset in enumerate(sequence):
            supply = min(
                kwargs["water_max"], max(kwargs["water_min"], base_supply[t] + offset)
            )
            q = min(
                emitter.available_power_kw(supply_temp=supply, indoor_temp=t_in),
                heatpump.max_thermal_power_kw,
            )
            cop = heatpump.cop_at(supply_temp=supply, outdoor_temp=outdoor[t])
            t_in = building.next_indoor_temp(
                t_in, outdoor_temp=outdoor[t], heat_input_kw=q, step_hours=1.0
            )
            total += (
                q / cop * prices[t]
                + comfort_penalty(
                    t_in,
                    comfort_min=building.comfort_min,
                    comfort_max=building.comfort_max,
                    weight=kwargs["comfort_penalty_weight"],
                    hard_floor=building.comfort_min - 1.5,
                )
                + kwargs["cycling_penalty_weight"] * (offset - prev) ** 2
            )
            prev = offset
        return total - max(0.0, t_in - building.comfort_min) * (
            building.thermal_mass_kwh_per_k * prices[-1] / terminal_cop
        )

    best = math.inf
    for sequence in itertools.product(range(-2, 3), repeat=horizon):
        steps = (0, *sequence)
        if all(abs(b - a) <= 1 for a, b in itertools.pairwise(steps)):
            best = min(best, objective(sequence))

    assert objective(tuple(result.offsets)) <= best + 0.02
