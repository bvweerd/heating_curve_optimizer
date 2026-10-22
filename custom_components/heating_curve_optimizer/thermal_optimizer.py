"""Backward-induction DP optimizer over indoor temperature.

The state is the physical quantity being managed - indoor temperature, via
`building_model.BuildingConfig` - plus the previously applied offset and a
small mode counter (periods since the offset last changed for the ramp
limit, and how long the current run or pause has lasted):

- energy is conserved by construction: the heat delivered each step is what
  `EmitterConfig.available_power_kw` says the chosen supply temperature can
  push into the room, capped by the heat pump's thermal capacity;
- curve limits are a per-step physical clamp on the supply temperature;
- the terminal value prices heat left in the building above the comfort
  floor at the horizon's mean price/COP, less what leaks away before it is
  used, so the plan does not drain the building just because the horizon
  ends;
- PV-covered electricity is priced at the feed-in rate, never as free.

## DP formulation

State at the start of step t: `(T_in[t], offset[t-1], mode[t])`.

    V[t](T_in, prev, mode) = min over allowed actions of
        stage_cost(T_in, prev, action) + V[t+1](T_in', offset', mode')

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
from collections.abc import Callable
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
from .helpers import calculate_supply_temperature, offset_ramp

_LOGGER = logging.getLogger(__name__)

# Longest integration substep: emitter output and the room thermostat are
# re-evaluated at least this often within a step.
MAX_SUBSTEP_HOURS = 0.25
# A heat pump with its own room thermostat switches between the target and
# 0.3 K above it; on average that holds the room at the middle of the band.
THERMOSTAT_HOLD_K = 0.15
# Time constant over which a measured-minus-modelled air difference fades.
AIR_OFFSET_DECAY_HOURS = 3.0


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
    # Degree-hours below the comfort floor; the savings compare like with
    # like only when the baseline's is (close to) zero too.
    comfort_deficit_kh: float = 0.0
    baseline_comfort_deficit_kh: float = 0.0
    # Marginal value of one more kWh of stored heat at t=0, in €/kWh.
    shadow_price_eur_per_kwh: float = 0.0


@dataclass
class _StepOutcome:
    supply_temp: float
    q_hp: float  # mean thermal power over the step
    p_elec: float
    cop: float
    energy_cost: float
    next_t_in: float  # mass temperature (the DP state) at the end of the step
    next_air: float  # room air temperature at the end of the step


class ThermalModel:
    """Physics and objective of one optimization, one step at a time.

    Shared by the DP, its forward pass and the baseline (and usable on its
    own, e.g. by a brute-force cross-check), so all of them see exactly the
    same building.

    A step is integrated in substeps of at most `MAX_SUBSTEP_HOURS`: the
    emitter output and the room thermostat follow the temperature within
    the step, so an hourly step behaves like four quarter-hour steps
    instead of committing to the output its start temperature allows.
    """

    def __init__(
        self,
        *,
        building: BuildingConfig,
        heatpump: HeatPumpConfig,
        emitter: EmitterConfig,
        outdoor: list[float],
        price: list[float],
        solar: list[float],
        pv: list[float],
        feed_in: list[float],
        humidity: list[float],
        floors: list[float],
        durations: list[float],
        water_min: float,
        water_max: float,
        outdoor_min: float,
        outdoor_max: float,
        comfort_penalty_weight: float,
        hard_floor_penalty: float,
        state_margin: float,
        initial_air_offset: float = 0.0,
    ) -> None:
        """Precompute the per-step inputs."""
        self.building = building
        self.heatpump = heatpump
        self.emitter = emitter
        self.outdoor = outdoor
        self.price = price
        self.solar = solar
        self.pv = pv
        self.feed_in = feed_in
        self.humidity = humidity
        self.floors = floors
        self.durations = durations
        self.water_min = water_min
        self.water_max = water_max
        self.comfort_penalty_weight = comfort_penalty_weight
        self.hard_floor_penalty = hard_floor_penalty
        self.state_margin = state_margin
        self.initial_air_offset = initial_air_offset
        self.horizon = len(outdoor)
        self.internal_gain = building.internal_gain_kw
        self.base_supply = [
            calculate_supply_temperature(
                temp,
                water_min=water_min,
                water_max=water_max,
                outdoor_min=outdoor_min,
                outdoor_max=outdoor_max,
            )
            for temp in outdoor
        ]
        self.max_power = [heatpump.max_thermal_power_at(temp) for temp in outdoor]
        self.start_hours: list[float] = []
        elapsed = 0.0
        for hours in durations:
            self.start_hours.append(elapsed)
            elapsed += hours
        self.substeps = [
            max(1, math.ceil(hours / MAX_SUBSTEP_HOURS - 1e-9)) for hours in durations
        ]
        self._cop: dict[tuple[int, float], float] = {}

    def air_offset(self, hours: float) -> float:
        """Measured minus modelled air at the start, fading out over time."""
        if not self.initial_air_offset:
            return 0.0
        return self.initial_air_offset * math.exp(-hours / AIR_OFFSET_DECAY_HOURS)

    def air(self, t: int, mass_temp: float, hours: float) -> float:
        """Room air temperature at `hours` from now (weather of step t)."""
        return self.building.air_temp(
            mass_temp,
            outdoor_temp=self.outdoor[t],
            solar_gain_kw=self.solar[t],
            internal_gain_kw=self.internal_gain,
        ) + self.air_offset(hours)

    def mass_for_air(self, t: int, air_temp: float, hours: float = 0.0) -> float:
        """The mass temperature at which the air settles at `air_temp`."""
        return self.building.mass_temp_for_air(
            air_temp - self.air_offset(hours),
            outdoor_temp=self.outdoor[t],
            solar_gain_kw=self.solar[t],
            internal_gain_kw=self.internal_gain,
        )

    def cop(self, t: int, supply_temp: float) -> float:
        """COP of step t at this supply temperature (cached)."""
        key = (t, supply_temp)
        if key not in self._cop:
            self._cop[key] = self.heatpump.cop_at(
                supply_temp=supply_temp,
                outdoor_temp=self.outdoor[t],
                humidity=self.humidity[t],
            )
        return self._cop[key]

    def step(
        self,
        t: int,
        t_in: float,
        offset: int,
        *,
        pump_on: bool = True,
        cutoff: float = math.inf,
    ) -> _StepOutcome:
        """Simulate step t from mass temperature `t_in`.

        ``pump_on=False`` is the explicit "heat pump off" action: the curve
        offset stays where it was, but the compressor delivers no heat.
        ``cutoff``: a room thermostat that stops the heat pump once the air
        reaches it. Within a substep the heat is limited to what brings the
        air exactly to the cutoff, which is what an on/off thermostat
        delivers on average; below the cutoff the curve's full output flows.
        """
        building = self.building
        supply_temp = min(
            self.water_max, max(self.water_min, self.base_supply[t] + offset)
        )
        cop = self.cop(t, supply_temp)
        duration = self.durations[t]
        n = self.substeps[t]
        dt = duration / n
        hours = self.start_hours[t]
        mass = t_in
        heat_kwh = 0.0
        capacity = building.thermal_mass_kwh_per_k
        for _ in range(n):
            q = 0.0
            if pump_on:
                air = self.air(t, mass, hours)
                q = self.emitter.available_power_kw(
                    supply_temp=supply_temp,
                    indoor_temp=mass if self.emitter.heats_mass else air,
                )
                q = max(0.0, min(q, self.max_power[t]))
                if cutoff < math.inf and capacity > 0 and q > 0:
                    coasting = building.next_mass_temp(
                        mass,
                        outdoor_temp=self.outdoor[t],
                        heat_input_kw=0.0,
                        solar_gain_kw=self.solar[t],
                        internal_gain_kw=self.internal_gain,
                        step_hours=dt,
                    )
                    at_cutoff = self.mass_for_air(t, cutoff, hours + dt)
                    q = max(0.0, min(q, (at_cutoff - coasting) * capacity / dt))
            mass = building.next_mass_temp(
                mass,
                outdoor_temp=self.outdoor[t],
                heat_input_kw=q,
                solar_gain_kw=self.solar[t],
                internal_gain_kw=self.internal_gain,
                step_hours=dt,
            )
            heat_kwh += q * dt
            hours += dt
        q_hp = heat_kwh / duration if duration > 0 else 0.0
        p_elec = q_hp / cop if q_hp > 0 else 0.0
        pv_covered = min(p_elec, self.pv[t])
        energy_cost = (
            (p_elec - pv_covered) * self.price[t] + pv_covered * self.feed_in[t]
        ) * duration
        next_air = self.air(min(t + 1, self.horizon - 1), mass, hours)
        return _StepOutcome(supply_temp, q_hp, p_elec, cop, energy_cost, mass, next_air)

    def stage_cost(self, t: int, outcome: _StepOutcome) -> float:
        """Energy cost plus the comfort penalty at the end of the step."""
        return outcome.energy_cost + comfort_penalty(
            outcome.next_air,
            comfort_min=self.floors[t],
            comfort_max=self.building.comfort_max,
            weight=self.comfort_penalty_weight,
            hard_floor=self.floors[t] - self.state_margin,
            hard_floor_penalty=self.hard_floor_penalty,
            step_hours=self.durations[t],
        )

    def comfort_deficit_kh(self, t: int, outcome: _StepOutcome) -> float:
        """Degree-hours below the floor in step t (judged at its end)."""
        return max(0.0, self.floors[t] - outcome.next_air) * self.durations[t]

    def terminal_value_fn(self) -> Callable[[float], float]:
        """Cost-to-go of the mass temperature left at the end of the horizon.

        Heat above the comfort floor saves heat the heat pump would
        otherwise make later, at the horizon's mean price and the final
        step's COP. It is used up at the net heat demand ``Q`` while it
        leaks away: an excess ``x`` K (stored ``C x``) lasts ``C x / Q``
        hours and loses ``UA x/2`` on average meanwhile, so it saves
        ``C (x - UA x² / 2Q)`` kWh, never more than at ``x = Q / UA``.
        With no heat demand stored heat saves nothing. A deficit (a
        setback running past the horizon) costs ``C x`` to make up.
        """
        building = self.building
        last = self.horizon - 1
        total_hours = sum(self.durations)
        mean_price = (
            sum(p * d for p, d in zip(self.price, self.durations, strict=True))
            / total_hours
            if total_hours > 0
            else self.price[last]
        )
        eur_per_kwh = mean_price / self.cop(last, self.base_supply[last])
        capacity = building.thermal_mass_kwh_per_k
        ua = building.ua_w_per_k / 1000.0
        floor = self.mass_for_air(last, building.comfort_min, math.inf)
        demand = max(
            0.0, ua * (building.comfort_min - self.outdoor[last]) - self.internal_gain
        )
        useful = demand / ua if ua > 0 else math.inf

        def terminal_value(t_in: float) -> float:
            excess = t_in - floor
            if excess <= 0:
                return -excess * capacity * eur_per_kwh
            if demand <= 0:
                return 0.0
            used = min(excess, useful)
            saved_kwh = capacity * (used - ua * used * used / (2 * demand))
            return -saved_kwh * eur_per_kwh

        return terminal_value


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
    initial_ramp_steps: int | None = None,
    comfort_min_per_step: list[float] | None = None,
    pump_follows_plan: bool = True,
    min_on_steps: int = 0,
    min_off_steps: int = 0,
    initial_pump_on: bool = True,
    initial_mode_steps: int | None = None,
    initial_mass_temp: float | None = None,
    initial_air_offset: float = 0.0,
) -> ThermalOptimizationResult:
    """Return a cost-optimal offset schedule over indoor temperature.

    Units: power in kW, prices in currency per kWh, `time_base` in minutes
    per price period, temperatures in °C.

    `comfort_min_per_step[t]` is the comfort floor at the *end* of step t
    (e.g. lowered while nobody is home); default `building.comfort_min`.
    The ceiling and the terminal value keep the building's own band.

    Ramp limit: see `helpers.offset_ramp`. `initial_ramp_steps` is the
    number of whole periods since the offset last changed (None = long
    enough to change now). Counters (ramp, run and pause minimums) only
    advance on full periods: a step shorter than `time_base` (the
    remainder of the current period) does not count, so every limit holds
    in real time.

    `pump_follows_plan`: True when the heat pump is switched on and off by
    the plan (and has no room thermostat of its own): the plan has an
    explicit "pump off" action and every run and every pause lasts at least
    `min_on_steps` / `min_off_steps` full periods. `initial_pump_on` and
    `initial_mode_steps` (periods already spent in that mode, None = long
    enough to switch) describe the current command. False: the heat pump
    runs on its own room thermostat, modelled as holding the room at the
    middle of its switching band (target + `THERMOSTAT_HOLD_K`) with the
    curve's full output below it, and the plan only moves the offset.

    With `building.air_coupling_kw_per_k` set, the DP state is the thermal
    mass temperature (`initial_mass_temp`, default: the mass temperature
    at which the air settles at `initial_indoor_temp`); comfort and the
    thermostat see the room air. `initial_air_offset` is the measured
    minus the modelled air temperature now; it fades out over
    `AIR_OFFSET_DECAY_HOURS`. Without a coupling both are the same single
    node.
    """
    horizon = min(len(outdoor_temps), len(prices))
    if horizon == 0:
        return ThermalOptimizationResult()
    if offset_min > offset_max:
        raise ValueError(
            f"offset_min ({offset_min}) must be <= offset_max ({offset_max})"
        )

    period_hours = time_base / 60.0 if time_base > 0 else 1.0
    durations: list[float] = (
        list(step_durations_hours[:horizon])
        if step_durations_hours and len(step_durations_hours) >= horizon
        else [period_hours] * horizon
    )
    # A step counts towards the ramp and run/pause minimums only when it
    # spans a whole period.
    increments = [1 if d >= period_hours * (1 - 1e-6) else 0 for d in durations]
    max_change, ramp_hold = offset_ramp(time_base, offset_delta_t)

    model = ThermalModel(
        building=building,
        heatpump=heatpump,
        emitter=emitter,
        outdoor=_pad(outdoor_temps, horizon, outdoor_temps[-1]),
        price=_pad(prices, horizon, prices[-1]),
        solar=[v * building.solar_factor for v in _pad(solar_gain_kw, horizon, 0.0)],
        pv=[max(0.0, v) for v in _pad(pv_surplus_kw, horizon, 0.0)],
        feed_in=_pad(feed_in_prices, horizon, feed_in_price_fallback),
        humidity=_pad(humidity_forecast, horizon, 80.0),
        floors=_pad(comfort_min_per_step, horizon, building.comfort_min),
        durations=durations,
        water_min=water_min,
        water_max=water_max,
        outdoor_min=outdoor_min,
        outdoor_max=outdoor_max,
        comfort_penalty_weight=comfort_penalty_weight,
        hard_floor_penalty=hard_floor_penalty,
        state_margin=state_margin,
        initial_air_offset=initial_air_offset,
    )
    floors = model.floors

    # The grid is in mass temperature: it spans the band as the mass sees
    # it under every step's weather (the same band with one node).
    states = building.discretize_states(
        resolution=state_resolution,
        margin=state_margin,
        lowest_comfort_min=min(
            model.mass_for_air(t, floors[t], math.inf) for t in range(horizon)
        ),
        highest_comfort_max=max(
            model.mass_for_air(t, building.comfort_max, math.inf)
            for t in range(horizon)
        ),
    )
    n_states = len(states)
    state_lo, state_hi = states[0], states[-1]
    cell = (state_hi - state_lo) / (n_states - 1) if n_states > 1 else 1.0
    # The baseline always has a room thermostat; the plan only when the
    # heat pump keeps its own - one that follows the plan heats on the
    # curve until the plan stops it.
    thermostat_cutoff = building.target_temp + THERMOSTAT_HOLD_K
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

    # Run/pause phases. A phase is (mode, full periods already done in it,
    # capped at the minimum); switching is allowed once the minimum is
    # reached. Without a plan-driven pump, or without minimums, one free
    # phase.
    n_on = max(0, min_on_steps) if pump_follows_plan else 0
    n_off = max(0, min_off_steps) if pump_follows_plan else 0
    constrained = n_on > 0 or n_off > 0

    def on_phase(done: int) -> int:
        return min(done, n_on)

    def off_phase(done: int) -> int:
        return n_on + 1 + min(done, n_off)

    def phase_after(phase: int, run: bool, inc: int) -> int:
        """The phase after a step (run or pause); -1 = not allowed."""
        if not constrained:
            return 0 if run or pump_follows_plan else -1
        if phase <= n_on:
            if run:
                return on_phase(phase + inc)
            return off_phase(inc) if phase >= n_on else -1
        done = phase - n_on - 1
        if run:
            return on_phase(inc) if done >= n_off else -1
        return off_phase(done + inc)

    if constrained:
        n_phases = n_on + n_off + 2
        if initial_mode_steps is None:
            phase0 = on_phase(n_on) if initial_pump_on else off_phase(n_off)
        elif initial_pump_on:
            phase0 = on_phase(max(0, initial_mode_steps))
        else:
            phase0 = off_phase(max(0, initial_mode_steps))
    else:
        n_phases = 1
        phase0 = 0

    # Mode = (phase, ramp): ramp counts full periods since the offset last
    # changed, capped at `ramp_hold`; a change needs ramp >= ramp_hold.
    n_ramp = ramp_hold + 1
    n_modes = n_phases * n_ramp

    def mode_of(phase: int, ramp: int) -> int:
        return phase * n_ramp + ramp

    ramp0 = (
        ramp_hold
        if initial_ramp_steps is None
        else min(ramp_hold, max(0, initial_ramp_steps))
    )
    mode0 = mode_of(phase0, ramp0)

    def successors(mode: int, inc: int) -> tuple[int, int, int]:
        """(after "off", after "on" at the same offset, after "on" with a
        changed offset); -1 = not allowed."""
        phase, ramp = divmod(mode, n_ramp)
        kept = min(ramp_hold, ramp + inc)
        off_phase_next = phase_after(phase, False, inc)
        on_phase_next = phase_after(phase, True, inc)
        off_next = mode_of(off_phase_next, kept) if off_phase_next >= 0 else -1
        same_next = mode_of(on_phase_next, kept) if on_phase_next >= 0 else -1
        change_next = (
            mode_of(on_phase_next, inc)
            if on_phase_next >= 0 and ramp >= ramp_hold
            else -1
        )
        return off_next, same_next, change_next

    terminal_value = model.terminal_value_fn()
    # v_tables[t][offset_idx][mode][state_idx]
    #   = V[t](state, prev_offset=offset, phase and ramp mode).
    terminal_row = [terminal_value(s) for s in states]
    v_tables: list[list[list[list[float]]]] = [[] for _ in range(horizon + 1)]
    v_tables[horizon] = [[terminal_row] * n_modes for _ in range(n_offsets)]

    def allowed(prev_offset: int) -> range:
        return range(
            max(offset_min, prev_offset - max_change),
            min(offset_max, prev_offset + max_change) + 1,
        )

    def continuation(
        stage: list[float], outcomes: list[_StepOutcome], row: list[float]
    ) -> list[float]:
        """Stage cost plus the interpolated value of where each state ends."""
        return [
            cost + interp(row, out.next_t_in)
            for cost, out in zip(stage, outcomes, strict=True)
        ]

    for t in reversed(range(horizon)):
        v_next = v_tables[t + 1]
        inc = increments[t]
        # Running with `offset` from each grid state: the outcome does not
        # depend on prev_offset or the mode, only the continuation does.
        on_outcomes = [
            [model.step(t, t_in, offset, cutoff=plan_cutoff) for t_in in states]
            for offset in offsets_range
        ]
        # Explicit "pump off": no heat, offset unchanged. Available at any
        # temperature: the comfort penalty, not a hard rule, decides
        # whether a short dip below the band is cheaper than heating at a
        # price peak (e.g. just before the sun warms the house for free).
        off_outcomes = [model.step(t, t_in, 0, pump_on=False) for t_in in states]
        on_stage = [[model.stage_cost(t, out) for out in row] for row in on_outcomes]
        off_stage = [model.stage_cost(t, out) for out in off_outcomes]
        on_cache: dict[tuple[int, int], list[float]] = {}
        off_cache: dict[tuple[int, int], list[float]] = {}

        def on_cont(
            oi: int,
            nxt: int,
            cache: dict[tuple[int, int], list[float]] = on_cache,
            stage: list[list[float]] = on_stage,
            outcomes: list[list[_StepOutcome]] = on_outcomes,
            v_next: list[list[list[float]]] = v_next,
        ) -> list[float]:
            if (oi, nxt) not in cache:
                cache[oi, nxt] = continuation(stage[oi], outcomes[oi], v_next[oi][nxt])
            return cache[oi, nxt]

        def off_cont(
            pi: int,
            nxt: int,
            cache: dict[tuple[int, int], list[float]] = off_cache,
            stage: list[float] = off_stage,
            outcomes: list[_StepOutcome] = off_outcomes,
            v_next: list[list[list[float]]] = v_next,
        ) -> list[float]:
            if (pi, nxt) not in cache:
                cache[pi, nxt] = continuation(stage, outcomes, v_next[pi][nxt])
            return cache[pi, nxt]

        v_cur: list[list[list[float]]] = []
        for pi, prev_offset in enumerate(offsets_range):
            mode_rows = []
            for mode in range(n_modes):
                off_next, same_next, change_next = successors(mode, inc)
                rows: list[list[float]] = []
                if off_next >= 0:
                    rows.append(off_cont(pi, off_next))
                if same_next >= 0:
                    rows.append(on_cont(pi, same_next))
                if change_next >= 0:
                    for offset in allowed(prev_offset):
                        if offset == prev_offset:
                            continue
                        oi = offset - offset_min
                        penalty = cycling_penalty_weight * (offset - prev_offset) ** 2
                        rows.append([v + penalty for v in on_cont(oi, change_next)])
                if not rows:
                    mode_rows.append([math.inf] * n_states)
                elif len(rows) == 1:
                    mode_rows.append(list(rows[0]))
                else:
                    mode_rows.append(list(map(min, *rows)))
            v_cur.append(mode_rows)
        v_tables[t] = v_cur

    # --- forward pass on the continuous state --------------------------
    safe_current_offset = min(offset_max, max(offset_min, current_offset))
    result = ThermalOptimizationResult()
    initial_mass = (
        initial_mass_temp
        if initial_mass_temp is not None
        else model.mass_for_air(0, initial_indoor_temp)
    )
    t_in = initial_mass
    prev_offset = safe_current_offset
    mode = mode0
    deficit = 0.0

    for t in range(horizon):
        off_next, same_next, change_next = successors(mode, increments[t])
        best_total = math.inf
        best: tuple[int, _StepOutcome, bool, int] | None = None
        # "Off" first: on equal cost the pump stays off.
        if off_next >= 0:
            off_outcome = model.step(t, t_in, prev_offset, pump_on=False)
            best_total = model.stage_cost(t, off_outcome) + interp(
                v_tables[t + 1][prev_offset - offset_min][off_next],
                off_outcome.next_t_in,
            )
            best = (prev_offset, off_outcome, False, off_next)
        for offset in allowed(prev_offset):
            nxt = same_next if offset == prev_offset else change_next
            if nxt < 0:
                continue
            outcome = model.step(t, t_in, offset, cutoff=plan_cutoff)
            total = (
                model.stage_cost(t, outcome)
                + cycling_penalty_weight * (offset - prev_offset) ** 2
                + interp(v_tables[t + 1][offset - offset_min][nxt], outcome.next_t_in)
            )
            if total < best_total:
                best_total = total
                best = (offset, outcome, True, nxt)
        assert best is not None
        offset, outcome, pump_on, mode = best
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
        deficit += model.comfort_deficit_kh(t, outcome)
        t_in = outcome.next_t_in
        prev_offset = offset
    optimized_end_temp = t_in
    result.total_cost_eur = round(sum(result.cost_eur), 5)
    result.comfort_deficit_kh = round(deficit, 3)

    # --- baseline: plain heating curve, same physics -------------------
    t_in = initial_mass
    baseline_energy = 0.0
    deficit = 0.0
    for t in range(horizon):
        outcome = model.step(t, t_in, 0, cutoff=thermostat_cutoff)
        result.baseline_supply_temps.append(round(outcome.supply_temp, 2))
        result.baseline_indoor_temps.append(round(outcome.next_air, 3))
        result.baseline_cop.append(round(outcome.cop, 3))
        result.baseline_cost_eur.append(round(outcome.energy_cost, 5))
        baseline_energy += outcome.energy_cost
        deficit += model.comfort_deficit_kh(t, outcome)
        t_in = outcome.next_t_in
    result.baseline_total_cost_eur = round(baseline_energy, 5)
    result.baseline_comfort_deficit_kh = round(deficit, 3)
    result.cost_savings_eur = round(
        (baseline_energy + terminal_value(t_in))
        - (sum(result.cost_eur) + terminal_value(optimized_end_temp)),
        5,
    )

    # --- shadow price at t=0 -------------------------------------------
    if n_states > 1 and building.thermal_mass_kwh_per_k > 0:
        row = v_tables[0][safe_current_offset - offset_min][mode0]
        t0 = min(max(initial_mass, state_lo + cell), state_hi - cell)
        slope = (interp(row, t0 + cell) - interp(row, t0 - cell)) / (2 * cell)
        result.shadow_price_eur_per_kwh = round(
            -slope / building.thermal_mass_kwh_per_k, 5
        )

    _LOGGER.debug(
        "Thermal optimization: horizon=%d states=%d modes=%d cost=%.4f "
        "baseline=%.4f shadow_price=%.4f",
        horizon,
        n_states,
        n_modes,
        result.total_cost_eur,
        result.baseline_total_cost_eur,
        result.shadow_price_eur_per_kwh,
    )
    return result
