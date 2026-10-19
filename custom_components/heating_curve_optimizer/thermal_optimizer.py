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
from .const import (
    DEFAULT_COMFORT_PENALTY_WEIGHT,
    DEFAULT_CYCLING_PENALTY_WEIGHT,
    DEFAULT_FEED_IN_PRICE_FALLBACK,
    DEFAULT_HARD_FLOOR_PENALTY,
    DEFAULT_OFFSET_MAX,
    DEFAULT_OFFSET_MIN,
)
from .heatpump_model import HeatPumpConfig
from .helpers import (
    calculate_supply_temperature,
)
from .helpers import (
    max_offset_change as _max_offset_change,
)

_LOGGER = logging.getLogger(__name__)


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
    hard_floor_penalty: float = DEFAULT_HARD_FLOOR_PENALTY,
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
        penalty += hard_floor_penalty
    return penalty


@dataclass
class ThermalOptimizationResult:
    """Result of `optimize_thermal_schedule`.

    Per-step lists all have `horizon` entries; `indoor_temps[t]` is the
    indoor temperature at the *end* of step t.
    """

    offsets: list[int] = field(default_factory=list)
    # Plan-driven pump: the plan's on/off command for the step. Pump with
    # its own thermostat: whether that thermostat lets it deliver heat.
    pump_on: list[bool] = field(default_factory=list)
    supply_temps: list[float] = field(default_factory=list)
    indoor_temps: list[float] = field(default_factory=list)
    # Thermal mass temperature (the DP state); equals indoor_temps with one
    # node.
    mass_temps: list[float] = field(default_factory=list)
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
    next_t_in: float  # mass temperature (the DP state) at the end of the step
    next_air: float  # room air temperature at the end of the step


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
    feed_in_price_fallback: float = DEFAULT_FEED_IN_PRICE_FALLBACK,
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
    comfort_penalty_weight: float = DEFAULT_COMFORT_PENALTY_WEIGHT,
    cycling_penalty_weight: float = DEFAULT_CYCLING_PENALTY_WEIGHT,
    hard_floor_penalty: float = DEFAULT_HARD_FLOOR_PENALTY,
    current_offset: int = 0,
    comfort_min_per_step: list[float] | None = None,
    pump_follows_plan: bool = True,
    min_on_steps: int = 0,
    min_off_steps: int = 0,
    initial_pump_on: bool = True,
    initial_mode_steps: int | None = None,
    initial_mass_temp: float | None = None,
) -> ThermalOptimizationResult:
    """Return a cost-optimal offset schedule over indoor temperature.

    Units: power in kW, prices in currency per kWh, `time_base` in minutes
    per step, temperatures in °C.

    `comfort_min_per_step[t]` is the comfort floor at the *end* of step t
    (e.g. lowered while nobody is home); default `building.comfort_min`.
    The ceiling and the terminal value keep the building's own band.

    `pump_follows_plan`: True when the heat pump is switched on and off by
    the plan (and has no room thermostat of its own): the plan has an
    explicit "pump off" action and every run and every pause lasts at least
    `min_on_steps` / `min_off_steps` steps. `initial_pump_on` and
    `initial_mode_steps` (steps already spent in that mode, None = long
    enough to switch) describe the current command. False: the heat pump
    stops on its own room thermostat at the target temperature, and the
    plan only moves the offset.

    With `building.air_coupling_kw_per_k` set, the DP state is the thermal
    mass temperature (`initial_mass_temp`, default: the mass temperature
    at which the air settles at `initial_indoor_temp`); comfort, the
    thermostat and the emitter see the room air. Without it both are the
    same single node.
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
    floors = _pad(comfort_min_per_step, horizon, building.comfort_min)
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

    def air_at(t: int, mass_temp: float) -> float:
        return building.air_temp(
            mass_temp,
            outdoor_temp=outdoor[t],
            solar_gain_kw=solar[t],
            internal_gain_kw=internal_gain,
        )

    def mass_at(t: int, air_temp: float) -> float:
        return building.mass_temp_for_air(
            air_temp,
            outdoor_temp=outdoor[t],
            solar_gain_kw=solar[t],
            internal_gain_kw=internal_gain,
        )

    # The grid is in mass temperature: it spans the band as the mass sees
    # it under every step's weather (the same band with one node).
    states = building.discretize_states(
        resolution=state_resolution,
        margin=state_margin,
        lowest_comfort_min=min(mass_at(t, floors[t]) for t in range(horizon)),
        highest_comfort_max=max(
            mass_at(t, building.comfort_max) for t in range(horizon)
        ),
    )
    n_states = len(states)
    state_lo, state_hi = states[0], states[-1]
    cell = (state_hi - state_lo) / (n_states - 1) if n_states > 1 else 1.0
    # A room thermostat: output tapers to zero over 0.3 K above the target
    # (the taper keeps the value function smooth). The baseline always has
    # one; the plan only when the heat pump keeps its own thermostat - one
    # that follows the plan heats on the curve until the plan stops it.
    ramp_width = 0.3
    thermostat_cutoff = building.target_temp + ramp_width
    plan_cutoff = math.inf if pump_follows_plan else thermostat_cutoff

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

    def simulate_step(
        t: int,
        t_in: float,
        offset: int,
        *,
        pump_on: bool = True,
        cutoff: float = plan_cutoff,
    ) -> _StepOutcome:
        """Simulate one step.

        ``pump_on=False`` is the explicit "heat pump off" action: the curve
        offset stays where it was, but the compressor delivers no heat.  This
        is how the DP expresses what a real heat pump does when the house
        needs less than the minimum output its curve can deliver - it cycles
        instead of trickling heat indefinitely.
        """
        supply_temp = min(water_max, max(water_min, base_supply[t] + offset))
        cop = heatpump.cop_at(
            supply_temp=supply_temp, outdoor_temp=outdoor[t], humidity=humidity[t]
        )
        if pump_on:
            air = air_at(t, t_in)
            q_available = emitter.available_power_kw(
                supply_temp=supply_temp, indoor_temp=air
            )
            q_hp = max(0.0, min(q_available, heatpump.max_thermal_power_kw))
            # Stopping *inside* the band is the explicit "pump off" action,
            # an economic choice of the DP; the taper (rather than a hard
            # cutoff) keeps the value function smooth.
            if air >= cutoff:
                q_hp = 0.0
            elif air > cutoff - ramp_width:
                q_hp *= (cutoff - air) / ramp_width
        else:
            q_hp = 0.0
        p_elec = q_hp / cop if q_hp > 0 else 0.0
        pv_covered = min(p_elec, pv[t])
        energy_cost = (
            (p_elec - pv_covered) * price[t] + pv_covered * feed_in[t]
        ) * step_durations[t]
        next_t_in = building.next_mass_temp(
            t_in,
            outdoor_temp=outdoor[t],
            heat_input_kw=q_hp,
            solar_gain_kw=solar[t],
            internal_gain_kw=internal_gain,
            step_hours=step_durations[t],
        )
        next_air = air_at(min(t + 1, horizon - 1), next_t_in)
        return _StepOutcome(
            supply_temp, q_hp, p_elec, cop, energy_cost, next_t_in, next_air
        )

    def stage_cost(t: int, outcome: _StepOutcome) -> float:
        return outcome.energy_cost + comfort_penalty(
            outcome.next_air,
            comfort_min=floors[t],
            comfort_max=building.comfort_max,
            weight=comfort_penalty_weight,
            hard_floor=floors[t] - state_margin,
            hard_floor_penalty=hard_floor_penalty,
            step_hours=step_durations[t],
        )

    # --- terminal value ------------------------------------------------
    terminal_cop = heatpump.cop_at(
        supply_temp=base_supply[-1],
        outdoor_temp=outdoor[-1],
        humidity=humidity[-1],
    )
    terminal_eur_per_k = building.thermal_mass_kwh_per_k * price[-1] / terminal_cop

    terminal_floor = mass_at(horizon - 1, building.comfort_min)

    def terminal_value(t_in: float) -> float:
        # Heat above the comfort floor is worth what it costs to produce;
        # a deficit (a setback running past the horizon) costs the same to
        # make up.
        return -(t_in - terminal_floor) * terminal_eur_per_k

    # Run/pause phases. A phase is (mode, steps already done in it, capped
    # at the minimum); switching is allowed once the minimum is reached.
    # Without a plan-driven pump, or without minimums, one free phase.
    n_on = max(0, min_on_steps) if pump_follows_plan else 0
    n_off = max(0, min_off_steps) if pump_follows_plan else 0
    constrained = n_on > 0 or n_off > 0

    def on_phase(done: int) -> int:
        return min(done, n_on)

    def off_phase(done: int) -> int:
        return n_on + 1 + min(done, n_off)

    if constrained:
        n_phases = n_on + n_off + 2
        # (next phase after "on", next phase after "off"); -1 = not allowed.
        transitions = [
            (on_phase(c + 1), off_phase(1) if c >= n_on else -1)
            for c in range(n_on + 1)
        ] + [
            (on_phase(1) if c >= n_off else -1, off_phase(c + 1))
            for c in range(n_off + 1)
        ]
        if initial_mode_steps is None:
            phase0 = on_phase(n_on) if initial_pump_on else off_phase(n_off)
        elif initial_pump_on:
            phase0 = on_phase(max(0, initial_mode_steps))
        else:
            phase0 = off_phase(max(0, initial_mode_steps))
    else:
        n_phases = 1
        transitions = [(0, 0 if pump_follows_plan else -1)]
        phase0 = 0

    # v_tables[t][offset_idx][phase][state_idx]
    #   = V[t](state, prev_offset=offset, run/pause phase).
    terminal_row = [terminal_value(s) for s in states]
    v_tables: list[list[list[list[float]]]] = [[] for _ in range(horizon + 1)]
    v_tables[horizon] = [[terminal_row] * n_phases for _ in range(n_offsets)]

    def allowed(prev_offset: int) -> range:
        return range(
            max(offset_min, prev_offset - max_change),
            min(offset_max, prev_offset + max_change) + 1,
        )

    on_targets = sorted({nxt for nxt, _ in transitions if nxt >= 0})
    off_targets = sorted({nxt for _, nxt in transitions if nxt >= 0})

    for t in reversed(range(horizon)):
        v_next = v_tables[t + 1]
        # Running with `offset` from each grid state: the outcome does not
        # depend on prev_offset or the phase, only the continuation does.
        on_outcomes = [
            [simulate_step(t, t_in, offset) for t_in in states]
            for offset in offsets_range
        ]
        on_cont = {
            q: [
                [
                    stage_cost(t, out) + interp(v_next[oi][q], out.next_t_in)
                    for out in on_outcomes[oi]
                ]
                for oi in range(n_offsets)
            ]
            for q in on_targets
        }
        # Explicit "pump off" action: no heat, offset unchanged. Available
        # at any temperature: the comfort penalty, not a hard rule, decides
        # whether a short dip below the band is cheaper than heating at a
        # price peak (e.g. just before the sun warms the house for free).
        off_outcomes = [simulate_step(t, t_in, 0, pump_on=False) for t_in in states]
        off_cont = {
            q: [
                [
                    stage_cost(t, out) + interp(v_next[pi][q], out.next_t_in)
                    for out in off_outcomes
                ]
                for pi in range(n_offsets)
            ]
            for q in off_targets
        }

        v_cur: list[list[list[float]]] = []
        for pi, prev_offset in enumerate(offsets_range):
            candidates = [
                (
                    offset - offset_min,
                    cycling_penalty_weight * (offset - prev_offset) ** 2,
                )
                for offset in allowed(prev_offset)
            ]
            phase_rows = []
            for on_next, off_next in transitions:
                on_rows = on_cont[on_next] if on_next >= 0 else None
                off_row = off_cont[off_next][pi] if off_next >= 0 else None
                row = []
                for s in range(n_states):
                    lowest = off_row[s] if off_row is not None else math.inf
                    if on_rows is not None:
                        for oi, penalty in candidates:
                            lowest = min(lowest, on_rows[oi][s] + penalty)
                    row.append(lowest)
                phase_rows.append(row)
            v_cur.append(phase_rows)
        v_tables[t] = v_cur

    # --- forward pass on the continuous state --------------------------
    safe_current_offset = min(offset_max, max(offset_min, current_offset))
    result = ThermalOptimizationResult()
    initial_mass = (
        initial_mass_temp
        if initial_mass_temp is not None
        else mass_at(0, initial_indoor_temp)
    )
    t_in = initial_mass
    prev_offset = safe_current_offset
    phase = phase0

    for t in range(horizon):
        on_next, off_next = transitions[phase]
        best_total = math.inf
        best: tuple[int, _StepOutcome, bool, int] | None = None
        # "Off" first: on equal cost the pump stays off.
        if off_next >= 0:
            off_outcome = simulate_step(t, t_in, prev_offset, pump_on=False)
            best_total = stage_cost(t, off_outcome) + interp(
                v_tables[t + 1][prev_offset - offset_min][off_next],
                off_outcome.next_t_in,
            )
            best = (prev_offset, off_outcome, False, off_next)
        if on_next >= 0:
            for offset in allowed(prev_offset):
                outcome = simulate_step(t, t_in, offset)
                total = (
                    stage_cost(t, outcome)
                    + cycling_penalty_weight * (offset - prev_offset) ** 2
                    + interp(
                        v_tables[t + 1][offset - offset_min][on_next],
                        outcome.next_t_in,
                    )
                )
                if total < best_total:
                    best_total = total
                    best = (offset, outcome, True, on_next)
        assert best is not None
        offset, outcome, pump_on, phase = best
        result.offsets.append(offset)
        # A plan-driven pump runs whenever the plan says so; one with its
        # own thermostat only while that thermostat calls for heat.
        result.pump_on.append(pump_on if pump_follows_plan else outcome.q_hp > 0)
        result.supply_temps.append(round(outcome.supply_temp, 2))
        result.indoor_temps.append(round(outcome.next_air, 3))
        result.mass_temps.append(round(outcome.next_t_in, 3))
        result.thermal_power_kw.append(round(outcome.q_hp, 4))
        result.electrical_power_kw.append(round(outcome.p_elec, 4))
        result.cop.append(round(outcome.cop, 3))
        result.cost_eur.append(round(outcome.energy_cost, 5))
        t_in = outcome.next_t_in
        prev_offset = offset
    optimized_end_temp = t_in
    result.total_cost_eur = round(sum(result.cost_eur), 5)

    # --- baseline: plain heating curve, same physics -------------------
    t_in = initial_mass
    baseline_energy = 0.0
    for t in range(horizon):
        outcome = simulate_step(t, t_in, 0, cutoff=thermostat_cutoff)
        result.baseline_supply_temps.append(round(outcome.supply_temp, 2))
        result.baseline_indoor_temps.append(round(outcome.next_air, 3))
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
        row = v_tables[0][safe_current_offset - offset_min][phase0]
        t0 = min(max(initial_mass, state_lo + cell), state_hi - cell)
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
