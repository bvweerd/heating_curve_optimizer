"""Backward-induction DP optimizer over indoor temperature.

Replaces the legacy `optimizer.optimize_offsets()` DP with one where the
state is the actual physical quantity being managed (indoor temperature,
via `building_model.BuildingConfig`) instead of a `buffer` value carried
as DP payload. See docs/redesign/REDESIGN.md §2.1 for the bugs this fixes
and §3.2 for the model this implements:

- energy is conserved by construction: the heat delivered each step is
  what `EmitterConfig.available_power_kw` says the chosen supply
  temperature can actually push into the room (fixes §2.1.A);
- the buffer is gone - indoor temperature IS the DP state dimension, so a
  cheaper-but-infeasible path can never silently win over a dearer-but-
  feasible one (fixes §2.1.B);
- there is no unused `cumulative_offset_sum` dimension (fixes §2.1.C);
- curve limits are enforced per time step, never globally across the whole
  horizon (fixes §2.1.D);
- the terminal condition is a real value function - heat left in the
  building above the comfort floor is worth something, valued at the
  price/COP prevailing at the end of the horizon, the same idea as
  battery_controller's `V[T][s] = -(soc_kwh * feed_in_price_T)` (fixes
  §2.1.E);
- PV surplus can be priced separately from grid import (fixes §2.1.G),
  falling back to a fixed feed-in price rather than `None` if no forecast
  is given - the same rule battery_controller's CLAUDE.md states for its
  optimizer, for the same reason: `None` silently makes PV arbitrage
  unprofitable instead of failing loudly.

## DP formulation

State at the start of step t: `(T_in[t], offset[t-1])`. The previous
offset is carried because the ramp-rate limit (`offset_delta_t`) bounds how
far `offset[t]` may be from `offset[t-1]`.

    V[t](T_in, prev_offset) = min over offset[t] of
        step_cost(T_in, prev_offset, offset[t]) + V[t+1](T_in', offset[t])

where `T_in'` is the indoor temperature after applying `offset[t]` for one
step. Note `offset[t]` becomes the *prev_offset* context for `V[t+1]` - the
value function at every step (including the terminal one) is therefore
always indexed by `(state, prev_offset)`, so the terminal value is simply
duplicated across every `prev_offset` row (it does not actually depend on
one, since there is no ramp constraint left to enforce after the horizon
ends).

This module is pure Python (no Home Assistant dependency) so the DP core
can be unit tested directly, including against a brute-force reference on
a short horizon (tests/test_thermal_optimizer.py).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from .building_model import BuildingConfig, EmitterConfig
from .heatpump_model import HeatPumpConfig
from .helpers import (
    calculate_supply_temperature,
    max_offset_change as _max_offset_change,
)

_LOGGER = logging.getLogger(__name__)

# A state landing below the discretized ladder's bottom edge is, under the
# configured comfort band and margin, meant to be unreachable in practice.
# If it is reached anyway (an undersized heat pump on an extreme cold snap),
# the DP must still be able to assign a finite cost to every state so the
# backward pass has no holes - so this is a very large but finite penalty,
# not a hard exclusion. No realistic alternative ever outweighs it; the
# optimizer no longer silently falls back to "do nothing" when some path
# turns out to be infeasible (see REDESIGN.md §2.1.D).
HARD_FLOOR_PENALTY_EUR = 1000.0

# When no feed_in_prices forecast is supplied but PV surplus is, this is the
# price used for self-consumed PV. Mirrors battery_controller's rule
# (CLAUDE.md "Critical Implementation Notes"): never let a missing feed-in
# price silently make solar-covered heating look free.
DEFAULT_FEED_IN_PRICE = 0.07

# The DP's absolute curve-offset bound (not the ramp-rate limit). Exposed as
# module constants, rather than only as this function's default arguments,
# so other callers that need to respect the same bound - the real-time
# PV-surplus controller's `effective_offset` clamp in coordinator.py, in
# particular - stay in sync with it instead of hardcoding their own copy.
DEFAULT_OFFSET_MIN = -4
DEFAULT_OFFSET_MAX = 4


def _pad(data: list[float] | None, length: int, default: float) -> list[float]:
    """Pad/truncate a forecast to `length`, holding the last value."""
    if not data:
        return [default] * length
    if len(data) >= length:
        return list(data[:length])
    last = data[-1]
    return list(data) + [last] * (length - len(data))


def _comfort_penalty(
    t_in: float,
    *,
    comfort_min: float,
    comfort_max: float,
    weight: float,
    hard_floor: float,
) -> float:
    """Quadratic comfort penalty, with a large added penalty below the floor."""
    if t_in < hard_floor:
        return weight * (comfort_min - t_in) ** 2 + HARD_FLOOR_PENALTY_EUR
    if t_in < comfort_min:
        return weight * (comfort_min - t_in) ** 2
    if t_in > comfort_max:
        return weight * (t_in - comfort_max) ** 2
    return 0.0


@dataclass
class ThermalOptimizationResult:
    """Result of `optimize_thermal_schedule`."""

    offsets: list[int] = field(default_factory=list)
    supply_temps: list[float] = field(default_factory=list)
    indoor_temps: list[float] = field(default_factory=list)
    thermal_power_kw: list[float] = field(default_factory=list)
    electrical_power_kw: list[float] = field(default_factory=list)
    cost_eur: list[float] = field(default_factory=list)
    total_cost_eur: float = 0.0
    # Marginal value of one more kWh of stored heat at t=0, in €/kWh -
    # positive means storing heat now is worth something (analogous to
    # battery_controller's shadow price lambda = -dV[0]/dSoC).
    shadow_price_eur_per_kwh: float = 0.0


def optimize_thermal_schedule(
    *,
    building: BuildingConfig,
    heatpump: HeatPumpConfig,
    emitter: EmitterConfig,
    outdoor_temps: list[float],
    prices: list[float],
    initial_indoor_temp: float,
    solar_gain_kw: list[float] | None = None,
    pv_surplus_kw: list[float] | None = None,
    feed_in_prices: list[float] | None = None,
    feed_in_price_fallback: float = DEFAULT_FEED_IN_PRICE,
    humidity_forecast: list[float] | None = None,
    time_base: int = 60,
    offset_delta_t: int = 10,
    offset_min: int = DEFAULT_OFFSET_MIN,
    offset_max: int = DEFAULT_OFFSET_MAX,
    water_min: float = 28.0,
    water_max: float = 45.0,
    outdoor_min: float = -20.0,
    outdoor_max: float = 15.0,
    state_resolution: float = 0.1,
    state_margin: float = 1.5,
    comfort_penalty_weight: float = 50.0,
    cycling_penalty_weight: float = 0.01,
    current_offset: int = 0,
) -> ThermalOptimizationResult:
    """Return a cost-optimal offset schedule over indoor temperature.

    Units: power in kW, `prices`/`feed_in_prices` in currency per kWh,
    `time_base` in minutes per step, temperatures in °C. See the module
    docstring for the DP formulation.
    """
    horizon = min(len(outdoor_temps), len(prices))
    if horizon == 0:
        return ThermalOptimizationResult()

    step_hours = time_base / 60.0 if time_base > 0 else 1.0
    max_offset_change = _max_offset_change(time_base, offset_delta_t)

    outdoor = _pad(outdoor_temps, horizon, outdoor_temps[-1] if outdoor_temps else 5.0)
    price = _pad(prices, horizon, prices[-1] if prices else 0.0)
    solar = _pad(solar_gain_kw, horizon, 0.0)
    pv = _pad(pv_surplus_kw, horizon, 0.0)
    feed_in = _pad(feed_in_prices, horizon, feed_in_price_fallback)
    humidity = _pad(humidity_forecast, horizon, 80.0)

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

    states = building.discretize_states(
        resolution=state_resolution, margin=state_margin
    )
    n_states = len(states)
    state_lo, state_hi = states[0], states[-1]
    state_span = state_hi - state_lo if n_states > 1 else 1.0

    def snap_state(t_in: float) -> int:
        clamped = min(max(t_in, state_lo), state_hi)
        if n_states <= 1:
            return 0
        return round((clamped - state_lo) / state_span * (n_states - 1))

    offsets_range = list(range(offset_min, offset_max + 1))

    # --- terminal value function ------------------------------------
    # V[horizon](state, prev_offset). Duplicated across every prev_offset
    # row: after the horizon ends there is no more ramp constraint to make
    # the value depend on how we got here, only on where we ended up.
    last_t = horizon - 1
    terminal_cop = heatpump.cop_at(
        supply_temp=base_supply[last_t],
        outdoor_temp=outdoor[last_t],
        humidity=humidity[last_t],
    )
    terminal_price = price[last_t]

    def terminal_value(state_idx: int) -> float:
        stored_above_min = max(0.0, states[state_idx] - building.comfort_min)
        return (
            -stored_above_min
            * building.thermal_mass_kwh_per_k
            * terminal_price
            / max(terminal_cop, heatpump.min_cop)
        )

    v_next: list[dict[int, float]] = [
        dict.fromkeys(offsets_range, terminal_value(s)) for s in range(n_states)
    ]

    # action_table[t][state_idx][prev_offset] = (offset, next_state_idx)
    action_table: list[list[dict[int, tuple[int, int]]]] = [
        [{} for _ in range(n_states)] for _ in range(horizon)
    ]

    for t in reversed(range(horizon)):
        v_cur: list[dict[int, float]] = [{} for _ in range(n_states)]
        supply_base = base_supply[t]
        out_t = outdoor[t]
        price_t = price[t]
        feed_in_t = feed_in[t]
        solar_t = solar[t]
        pv_t = max(0.0, pv[t])
        humidity_t = humidity[t]

        for state_idx in range(n_states):
            t_in = states[state_idx]

            for prev_offset in offsets_range:
                lo = max(offset_min, prev_offset - max_offset_change)
                hi = min(offset_max, prev_offset + max_offset_change)
                best_cost = math.inf
                best_action: tuple[int, int] | None = None

                for offset in range(lo, hi + 1):
                    # Curve limits are a physical clamp on the water
                    # temperature the installation can actually reach, not
                    # a filter that removes the offset for the whole
                    # horizon (REDESIGN.md §2.1.D) - a step that would ask
                    # for more than the system supports just gets what the
                    # system can give.
                    supply_temp = min(water_max, max(water_min, supply_base + offset))
                    q_available = emitter.available_power_kw(
                        supply_temp=supply_temp, indoor_temp=t_in
                    )
                    q_hp = max(0.0, min(q_available, heatpump.max_thermal_power_kw))
                    p_elec = heatpump.electrical_power_kw(
                        thermal_power_kw=q_hp,
                        supply_temp=supply_temp,
                        outdoor_temp=out_t,
                        humidity=humidity_t,
                    )
                    pv_covered = min(p_elec, pv_t)
                    grid_covered = p_elec - pv_covered
                    energy_cost = (
                        grid_covered * price_t + pv_covered * feed_in_t
                    ) * step_hours

                    next_t_in = building.next_indoor_temp(
                        t_in,
                        outdoor_temp=out_t,
                        heat_input_kw=q_hp,
                        solar_gain_kw=solar_t,
                        step_hours=step_hours,
                    )
                    next_idx = snap_state(next_t_in)
                    comfort_cost = _comfort_penalty(
                        states[next_idx],
                        comfort_min=building.comfort_min,
                        comfort_max=building.comfort_max,
                        weight=comfort_penalty_weight,
                        hard_floor=state_lo,
                    )
                    cycling_cost = cycling_penalty_weight * (offset - prev_offset) ** 2

                    step_cost = energy_cost + comfort_cost + cycling_cost
                    # offset chosen here becomes the *prev_offset* context
                    # for V[t+1] at next_idx - see module docstring.
                    total = step_cost + v_next[next_idx][offset]

                    if total < best_cost:
                        best_cost = total
                        best_action = (offset, next_idx)

                v_cur[state_idx][prev_offset] = best_cost
                assert best_action is not None
                action_table[t][state_idx][prev_offset] = best_action

        v_next = v_cur

    # v_next now holds V[0](state, prev_offset).
    v_zero = v_next

    # --- forward pass ------------------------------------------------
    # `current_offset` (the coordinator's persisted last offset) is only
    # ever written back from this function's own `offsets[0]`, so it stays
    # within `offsets_range` in practice - but action_table is keyed only
    # on that range, so an out-of-range value here would KeyError instead
    # of degrading gracefully. Guarded the same way the shadow-price `row`
    # lookup below already is, rather than trusting the caller.
    safe_current_offset = (
        current_offset if current_offset in offsets_range else offsets_range[0]
    )
    start_idx = snap_state(initial_indoor_temp)
    result = ThermalOptimizationResult()
    state_idx = start_idx
    prev_offset = safe_current_offset

    for t in range(horizon):
        offset, next_idx = action_table[t][state_idx][prev_offset]
        supply_temp = min(water_max, max(water_min, base_supply[t] + offset))
        q_available = emitter.available_power_kw(
            supply_temp=supply_temp, indoor_temp=states[state_idx]
        )
        q_hp = max(0.0, min(q_available, heatpump.max_thermal_power_kw))
        p_elec = heatpump.electrical_power_kw(
            thermal_power_kw=q_hp,
            supply_temp=supply_temp,
            outdoor_temp=outdoor[t],
            humidity=humidity[t],
        )
        pv_covered = min(p_elec, max(0.0, pv[t]))
        grid_covered = p_elec - pv_covered
        step_cost = (grid_covered * price[t] + pv_covered * feed_in[t]) * step_hours

        result.offsets.append(offset)
        result.supply_temps.append(round(supply_temp, 2))
        result.indoor_temps.append(round(states[next_idx], 3))
        result.thermal_power_kw.append(round(q_hp, 4))
        result.electrical_power_kw.append(round(p_elec, 4))
        result.cost_eur.append(round(step_cost, 5))

        state_idx = next_idx
        prev_offset = offset

    result.total_cost_eur = round(sum(result.cost_eur), 5)

    # --- shadow price at t=0 (central difference on V[0]) ----------------
    # lambda = -(dV[0]/dT_in) / thermal_mass, at the actual start state and
    # current_offset. More stored heat lowers future cost, so dV/dT_in <= 0
    # and lambda >= 0 - a positive price for the marginal kWh stored now.
    row = safe_current_offset
    if n_states > 1 and building.thermal_mass_kwh_per_k > 0:
        lo_idx = max(0, start_idx - 1)
        hi_idx = min(n_states - 1, start_idx + 1)
        if hi_idx > lo_idx:
            d_v = v_zero[hi_idx][row] - v_zero[lo_idx][row]
            d_t = states[hi_idx] - states[lo_idx]
            slope = d_v / d_t if d_t else 0.0
        else:
            slope = 0.0
        result.shadow_price_eur_per_kwh = round(
            -slope / building.thermal_mass_kwh_per_k, 5
        )

    _LOGGER.debug(
        "Thermal optimization: horizon=%d states=%d total_cost=%.4f shadow_price=%.4f",
        horizon,
        n_states,
        result.total_cost_eur,
        result.shadow_price_eur_per_kwh,
    )
    return result
