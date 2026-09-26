"""Backward-induction DP optimizer over indoor temperature.

The state is the physical quantity being managed - indoor temperature, via
`building_model.BuildingConfig` - plus the previously applied offset (needed
for the ramp-rate limit):

- energy is conserved by construction: the heat delivered each step is what
  `EmitterConfig.available_power_kw` says the chosen supply temperature can
  push into the room, capped by the heat pump's thermal capacity;
- curve limits are a per-step physical clamp on the supply temperature;
- the terminal value prices heat left in the building above the comfort
  floor at the end-of-horizon price/COP, so the plan does not drain the
  building just because the horizon ends;
- PV-covered electricity is priced at the feed-in rate, never as free.

## DP formulation

State at the start of step t: `(T_in[t], offset[t-1])`.

    V[t](T_in, prev) = min over offset of
        stage_cost(T_in, prev, offset) + V[t+1](T_in', offset)

`V[t]` is stored on a regular temperature grid and **linearly interpolated**
between grid points. The transition `T_in -> T_in'` itself is never snapped
to the grid: with building time constants of tens of hours, the per-step
drift is often smaller than half a grid cell, and rounding it away would
make a slowly cooling house look perfectly stable to the optimizer. The
forward pass likewise simulates the continuous temperature and re-evaluates
the optimal action at the actual (off-grid) state each step.

Pure Python (no Home Assistant dependency) so it can be unit tested
directly, including against a brute-force reference on a short horizon.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from .building_model import BuildingConfig, EmitterConfig
from .heatpump_model import HeatPumpConfig
from .helpers import (
    calculate_supply_temperature,
)
from .helpers import (
    max_offset_change as _max_offset_change,
)

_LOGGER = logging.getLogger(__name__)

# Added once per step whenever indoor temperature falls below
# `comfort_min - state_margin`. Large enough that no realistic energy saving
# outweighs it, finite so an undersized system still gets a best-effort plan.
HARD_FLOOR_PENALTY_EUR = 1000.0

# Price used for self-consumed PV when no feed-in forecast is given - a
# missing feed-in price must never make PV-covered heating look free.
DEFAULT_FEED_IN_PRICE = 0.07

# Absolute heating-curve offset bounds, shared with the real-time controller.
DEFAULT_OFFSET_MIN = -4
DEFAULT_OFFSET_MAX = 4


def _pad(data: list[float] | None, length: int, default: float) -> list[float]:
    """Pad/truncate a forecast to `length`, holding the last value."""
    if not data:
        return [default] * length
    if len(data) >= length:
        return list(data[:length])
    return list(data) + [data[-1]] * (length - len(data))


def comfort_penalty(
    t_in: float,
    *,
    comfort_min: float,
    comfort_max: float,
    weight: float,
    hard_floor: float,
    step_hours: float = 1.0,
) -> float:
    """Quadratic comfort penalty outside the band, plus a hard-floor penalty.

    The quadratic part scales with ``step_hours`` so ``weight`` means the
    same thing (€ per K² per hour) regardless of the step length.
    """
    penalty = 0.0
    if t_in < comfort_min:
        penalty = weight * (comfort_min - t_in) ** 2 * step_hours
    elif t_in > comfort_max:
        penalty = weight * (t_in - comfort_max) ** 2 * step_hours
    if t_in < hard_floor:
        penalty += HARD_FLOOR_PENALTY_EUR
    return penalty


@dataclass
class ThermalOptimizationResult:
    """Result of `optimize_thermal_schedule`.

    Per-step lists all have `horizon` entries; `indoor_temps[t]` is the
    indoor temperature at the *end* of step t.
    """

    offsets: list[int] = field(default_factory=list)
    supply_temps: list[float] = field(default_factory=list)
    indoor_temps: list[float] = field(default_factory=list)
    thermal_power_kw: list[float] = field(default_factory=list)
    electrical_power_kw: list[float] = field(default_factory=list)
    cop: list[float] = field(default_factory=list)
    cost_eur: list[float] = field(default_factory=list)
    total_cost_eur: float = 0.0
    # Plain heating curve (offset 0 every step), same physics and prices.
    baseline_supply_temps: list[float] = field(default_factory=list)
    baseline_indoor_temps: list[float] = field(default_factory=list)
    baseline_cop: list[float] = field(default_factory=list)
    baseline_cost_eur: list[float] = field(default_factory=list)
    baseline_total_cost_eur: float = 0.0
    # Energy cost difference corrected for the value of heat left in the
    # building at the end of the horizon (pre-heating is not a loss).
    cost_savings_eur: float = 0.0
    # Marginal value of one more kWh of stored heat at t=0, in €/kWh.
    shadow_price_eur_per_kwh: float = 0.0


@dataclass
class _StepOutcome:
    supply_temp: float
    q_hp: float
    p_elec: float
    cop: float
    energy_cost: float
    next_t_in: float


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
    step_durations_hours: list[float] | None = None,
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

    Units: power in kW, prices in currency per kWh, `time_base` in minutes
    per step, temperatures in °C.
    """
    horizon = min(len(outdoor_temps), len(prices))
    if horizon == 0:
        return ThermalOptimizationResult()
    if offset_min > offset_max:
        raise ValueError(
            f"offset_min ({offset_min}) must be <= offset_max ({offset_max})"
        )

    step_hours = time_base / 60.0 if time_base > 0 else 1.0
    step_durations: list[float] = (
        list(step_durations_hours[:horizon])
        if step_durations_hours and len(step_durations_hours) >= horizon
        else [step_hours] * horizon
    )
    max_change = _max_offset_change(time_base, offset_delta_t)

    outdoor = _pad(outdoor_temps, horizon, outdoor_temps[-1])
    price = _pad(prices, horizon, prices[-1])
    solar = [v * building.solar_factor for v in _pad(solar_gain_kw, horizon, 0.0)]
    pv = [max(0.0, v) for v in _pad(pv_surplus_kw, horizon, 0.0)]
    feed_in = _pad(feed_in_prices, horizon, feed_in_price_fallback)
    humidity = _pad(humidity_forecast, horizon, 80.0)
    internal_gain = building.internal_gain_kw

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
    cell = (state_hi - state_lo) / (n_states - 1) if n_states > 1 else 1.0
    hard_floor = building.comfort_min - state_margin

    offsets_range = list(range(offset_min, offset_max + 1))
    n_offsets = len(offsets_range)

    def interp(row: list[float], t_in: float) -> float:
        """Linear interpolation of a value row on the state grid (clamped)."""
        if n_states == 1:
            return row[0]
        x = (min(max(t_in, state_lo), state_hi) - state_lo) / cell
        i = min(int(x), n_states - 2)
        frac = x - i
        return row[i] + (row[i + 1] - row[i]) * frac

    def simulate_step(t: int, t_in: float, offset: int) -> _StepOutcome:
        supply_temp = min(water_max, max(water_min, base_supply[t] + offset))
        q_available = emitter.available_power_kw(
            supply_temp=supply_temp, indoor_temp=t_in
        )
        q_hp = max(0.0, min(q_available, heatpump.max_thermal_power_kw))
        # Thermostat: heat pump output ramps down between target and
        # comfort_max, off above comfort_max.  This models realistic
        # thermostatic control where the emitter is fully available below
        # the target but tapers off as the room overshoots.
        if t_in >= building.comfort_max:
            q_hp = 0.0
        elif t_in > building.target_temp:
            band = building.comfort_max - building.target_temp
            q_hp *= (building.comfort_max - t_in) / band if band > 0 else 0.0
        cop = heatpump.cop_at(
            supply_temp=supply_temp, outdoor_temp=outdoor[t], humidity=humidity[t]
        )
        p_elec = q_hp / cop if q_hp > 0 else 0.0
        pv_covered = min(p_elec, pv[t])
        energy_cost = (
            (p_elec - pv_covered) * price[t] + pv_covered * feed_in[t]
        ) * step_durations[t]
        next_t_in = building.next_indoor_temp(
            t_in,
            outdoor_temp=outdoor[t],
            heat_input_kw=q_hp,
            solar_gain_kw=solar[t],
            internal_gain_kw=internal_gain,
            step_hours=step_durations[t],
        )
        return _StepOutcome(supply_temp, q_hp, p_elec, cop, energy_cost, next_t_in)

    def stage_cost(t: int, outcome: _StepOutcome) -> float:
        return outcome.energy_cost + comfort_penalty(
            outcome.next_t_in,
            comfort_min=building.comfort_min,
            comfort_max=building.comfort_max,
            weight=comfort_penalty_weight,
            hard_floor=hard_floor,
            step_hours=step_durations[t],
        )

    # --- terminal value ------------------------------------------------
    terminal_cop = heatpump.cop_at(
        supply_temp=base_supply[-1],
        outdoor_temp=outdoor[-1],
        humidity=humidity[-1],
    )
    terminal_eur_per_k = building.thermal_mass_kwh_per_k * price[-1] / terminal_cop

    def terminal_value(t_in: float) -> float:
        return -max(0.0, t_in - building.comfort_min) * terminal_eur_per_k

    # v_tables[t][offset_idx][state_idx] = V[t](state, prev_offset=offset).
    terminal_row = [terminal_value(s) for s in states]
    v_tables: list[list[list[float]]] = [[] for _ in range(horizon + 1)]
    v_tables[horizon] = [terminal_row] * n_offsets

    def allowed(prev_offset: int) -> range:
        return range(
            max(offset_min, prev_offset - max_change),
            min(offset_max, prev_offset + max_change) + 1,
        )

    for t in reversed(range(horizon)):
        v_next = v_tables[t + 1]
        # Cost-to-go of choosing `offset` from each grid state; independent
        # of prev_offset, so computed once and shared by every prev row.
        q_values: list[list[float]] = []
        for oi, offset in enumerate(offsets_range):
            row = []
            for t_in in states:
                outcome = simulate_step(t, t_in, offset)
                row.append(
                    stage_cost(t, outcome) + interp(v_next[oi], outcome.next_t_in)
                )
            q_values.append(row)

        v_cur: list[list[float]] = []
        for prev_offset in offsets_range:
            candidates = [offset - offset_min for offset in allowed(prev_offset)]
            row = []
            for s in range(n_states):
                row.append(
                    min(
                        q_values[oi][s]
                        + cycling_penalty_weight
                        * (offsets_range[oi] - prev_offset) ** 2
                        for oi in candidates
                    )
                )
            v_cur.append(row)
        v_tables[t] = v_cur

    # --- forward pass on the continuous state --------------------------
    safe_current_offset = min(offset_max, max(offset_min, current_offset))
    result = ThermalOptimizationResult()
    t_in = initial_indoor_temp
    prev_offset = safe_current_offset

    for t in range(horizon):
        best_total = math.inf
        best: tuple[int, _StepOutcome] | None = None
        for offset in allowed(prev_offset):
            outcome = simulate_step(t, t_in, offset)
            total = (
                stage_cost(t, outcome)
                + cycling_penalty_weight * (offset - prev_offset) ** 2
                + interp(v_tables[t + 1][offset - offset_min], outcome.next_t_in)
            )
            if total < best_total:
                best_total = total
                best = (offset, outcome)
        assert best is not None
        offset, outcome = best
        result.offsets.append(offset)
        result.supply_temps.append(round(outcome.supply_temp, 2))
        result.indoor_temps.append(round(outcome.next_t_in, 3))
        result.thermal_power_kw.append(round(outcome.q_hp, 4))
        result.electrical_power_kw.append(round(outcome.p_elec, 4))
        result.cop.append(round(outcome.cop, 3))
        result.cost_eur.append(round(outcome.energy_cost, 5))
        t_in = outcome.next_t_in
        prev_offset = offset
    optimized_end_temp = t_in
    result.total_cost_eur = round(sum(result.cost_eur), 5)

    # --- baseline: plain heating curve, same physics -------------------
    t_in = initial_indoor_temp
    baseline_energy = 0.0
    for t in range(horizon):
        outcome = simulate_step(t, t_in, 0)
        result.baseline_supply_temps.append(round(outcome.supply_temp, 2))
        result.baseline_indoor_temps.append(round(outcome.next_t_in, 3))
        result.baseline_cop.append(round(outcome.cop, 3))
        result.baseline_cost_eur.append(round(outcome.energy_cost, 5))
        baseline_energy += outcome.energy_cost
        t_in = outcome.next_t_in
    result.baseline_total_cost_eur = round(baseline_energy, 5)
    result.cost_savings_eur = round(
        (baseline_energy + terminal_value(t_in))
        - (sum(result.cost_eur) + terminal_value(optimized_end_temp)),
        5,
    )

    # --- shadow price at t=0 -------------------------------------------
    if n_states > 1 and building.thermal_mass_kwh_per_k > 0:
        row = v_tables[0][safe_current_offset - offset_min]
        t0 = min(max(initial_indoor_temp, state_lo + cell), state_hi - cell)
        slope = (interp(row, t0 + cell) - interp(row, t0 - cell)) / (2 * cell)
        result.shadow_price_eur_per_kwh = round(
            -slope / building.thermal_mass_kwh_per_k, 5
        )

    _LOGGER.debug(
        "Thermal optimization: horizon=%d states=%d cost=%.4f baseline=%.4f "
        "shadow_price=%.4f",
        horizon,
        n_states,
        result.total_cost_eur,
        result.baseline_total_cost_eur,
        result.shadow_price_eur_per_kwh,
    )
    return result
