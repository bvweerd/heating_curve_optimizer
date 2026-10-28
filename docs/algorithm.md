# How it works

## Coordinators

```
Weather (30 min, open-meteo)
  └─ Heat calculation, per zone (5 min): heat loss, solar gain, internal gains, PV
       └─ Optimization, per zone (15 min, on price change, on setpoint change)
            └─ Gas boiler comparison (optional)
```

All forecasts are put on one time grid: one step per price period, the
first step starting *now* and ending at the next price boundary. The
horizon is the planning window, but it ends with the last known price
(holding the last price only to reach at least 6 hours, e.g. late in the
evening before tomorrow's prices, or when the sensor has just the current
price). Hourly weather values are averaged onto that grid by overlap.
open-meteo reports temperature and humidity *at* each hour and radiation as
the mean over the *preceding* hour; the integration turns the first into
hourly means (the midpoint of two readings) and shifts the second, so every
value describes the hour it is used for.

## Building model (1R1C)

The zone is one thermal mass `C` (kWh/K) that loses heat through `UA`
(W/K):

```
C · dT_in/dt = Q_heat_pump + Q_solar + Q_internal − UA · (T_in − T_out)
```

- `UA` comes from the energy label (transmission) plus ventilation, until
  calibration replaces it.
- `C` comes from floor area × construction class.
- `Q_solar` is the irradiance on each vertical window (sun position, direct
  and diffuse radiation) × glass area × solar heat gain coefficient.

### Mass + air (opt-in per zone)

With *Building model: Mass + air* the heat pump heats the thermal mass
(floor, structure) and the room air sits on top of it, warmed directly by
sun and internal gains. The air holds so little heat that it is solved
quasi-steadily:

```
T_air = (H · T_mass + UA · T_out + Q_solar + Q_internal) / (H + UA)
C · dT_mass/dt = Q_heat_pump − H · (T_mass − T_air)
```

`H` (kW/K) is the mass-air coupling. The DP state becomes the mass
temperature; comfort and the room thermostat see the air. The emitters
heat the mass, so their output depends on the mass temperature: a loaded
floor takes less heat. Their design point is taken against the mass
temperature that holds the air at the design indoor temperature, so offset
0 still covers the design heat loss.

The measured room air is the starting point of the plan: what it differs
from the air the mass implies (sensor placement, a draught, model error,
at most 2 K) is added to the modelled air and fades out with a time
constant of 3 hours.
Sun now shows up in the room straight away and leaves it again after
sunset, instead of being stored in the whole mass - which is what a one-node
model gets wrong in a heavy house with underfloor heating: it predicts the
afternoon too warm.

No sensor measures the mass temperature. An observer integrates the
delivered heat (measured, or electrical power × COP) against the measured
room air every minute: `C · dT_mass/dt = Q − H · (T_mass − T_air,measured)`.

`H` is learned: every zone keeps 14 days of 5-minute history (air, outdoor,
solar, heat), and every 6 hours a grid search replays the observer and
predicts the air 6 hours ahead from every hour, for each candidate `H`
together with a solar factor (0.3–1.2 × the building's). The sun reaches
the air directly in this model, so a window gain that is too high (low
autumn sun, shading) shows as a warm error and is learned away. Once there
are 3 days of history with some heating, the best `H` and solar factor are
used if they predict better than the one-node model (with its own best
solar factor); otherwise the zone runs as one node. Until then `H` = 55 W/K
per m² floor, or the value set in the zone (a set value always wins and
keeps the building's solar factor). The history is collected in one-node
zones too, so diagnostics show whether switching would pay off
(`learned_accepted`: at least 15 % better).

On one house (159 m², heavy, underfloor heating, two weeks of October data,
the last three days held out) the 6-hour-ahead error went from 0.24 K
(one node, label parameters) to 0.13–0.15 K with the learned `H` of about
8 kW/K, with the same `H` whether the heat came from the heat pump's own
heat counter or from flow × spread.

Temperature sensors on the floor manifold of that house show the screed
near the pipes holding a separate buffer: up to 5 K above the room after a
run, fading with a time constant of about 1.7 hours. A third (screed) node
fitted to those curves reproduced them, but did not predict the room air
better, so the model keeps two. The screed's heat is still counted: it is
part of the mass's energy balance and of the heat buffer.

## Emitters and heat pump

The offset only matters because it changes how much heat the emitters can
deliver. Emitter output follows the standard power law

```
Q = Q_nominal · ((T_supply − T_in) / ΔT_nominal) ^ n
```

with `n` = 1.3 for radiators, 1.1 for underfloor heating and 1.0 for fan
coils. The emitters are sized so that at the design outdoor temperature and
the cold-end supply temperature they deliver exactly the building's heat
loss. The heat pump delivers what the emitters can take, up to its
capacity; the electricity needed is `Q / COP` (see
[Configuration](configuration.md#heat-pump) for the COP model).

The capacity of an air-source heat pump falls in colder air: the maximum
thermal power changes 2 % per K away from its rating point (7 °C for a
configured value, the curve's design outdoor temperature for the value
derived from the emitters), between 50 % and 125 %.

Each step is integrated in substeps of at most 15 minutes, re-evaluating
the emitter output and the room thermostat as the temperature moves, so an
hourly price period behaves like four quarter-hour ones.

## Optimizer

Backward-induction dynamic programming over the state
`(indoor temperature, previous offset)`:

```
V_t(T, o_prev) = min over o of  cost_t(T, o) + comfort_t(T') + λ·(o − o_prev)² + V_t+1(T', o)
```

- **Decision**: offset `o` ∈ {−4 … +4}, never faster than 1 °C per
  `offset_delta_t` minutes: a period at least that long may change by
  `step_minutes // offset_delta_t` degrees, a shorter one by 1 degree every
  `⌈offset_delta_t / step_minutes⌉` periods (15-minute prices at 30 min/K:
  1 °C every half hour). The state carries the periods since the last
  change, and the first run after a change starts from how long ago the
  published offset changed. What else the plan controls depends on the
  *Switching* setting:
    - **Own thermostat**: the heat pump runs on its own room thermostat,
      which switches between the target and 0.3 °C above it. The model
      holds the room at the middle of that band (target + 0.15 °C) on
      average, with the curve's full output below it. The plan only moves
      the offset.
    - **Follows the plan**: there is no thermostat in the model, and the
      plan also chooses **pump off** (no heat, offset unchanged), at any
      temperature. That is how it coasts through expensive hours by letting
      the room drift down inside the band, and stops when the house needs
      less than the curve floor delivers. Below the band no rule forces
      heating: the comfort penalty decides, so a short, shallow dip just
      before free solar gain can beat heating at a price peak. Every run
      and every pause lasts at least the minimum run time / pause: the DP
      state also carries how long the current run or pause has lasted, and
      starts from the advice last published.
- **Counting**: the ramp limit and the run/pause minimums count only full
  price periods. The first step (the rest of the current period) does not
  count, so every limit holds in real time.
- **Transition**: `T'` from the building model, computed exactly (not
  rounded to the grid).
- **Value function**: stored on a 0.1 °C grid and interpolated linearly.
  Interpolation matters: with building time constants of tens of hours the
  temperature often changes by less than half a grid cell per step, which a
  rounding DP would not see at all.
- **Energy cost**: grid electricity × price, PV-covered electricity × feed-in
  price. PV available for heating is the forecast production minus the
  house base load (expert setting, 300 W), shared equally over the zones.
- **Comfort**: 50 €/K²/h below or above the band, plus a 1000 € penalty
  below `comfort_min − 1.5 °C`. With a home schedule `comfort_min` is per
  step: lowered by the away setback for steps that end while the schedule
  is off (see [Away periods](configuration.md#away-periods)).
- **Heat buffer**: `C · (T_mass − T_mass,floor)`, with `T_mass,floor` the mass
  temperature at which the air settles at `comfort_min` under the current
  weather (with one node: `C · (T_in − comfort_min)`).
- **Terminal value**: heat stored above the comfort floor at the end of the
  horizon saves heat the heat pump would otherwise make later, at the
  horizon's mean price and the last step's COP, so the plan does not empty
  the building just because the horizon ends. Stored heat is used up at the
  net heat demand `Q` while it leaks away: an excess of `x` K saves
  `C · (x − UA · x² / 2Q)` kWh (at most at `x = Q / UA`), and nothing when
  the house needs no heat. A deficit below the floor (an away setback
  running past the horizon) costs `C · x` to make up.

The forward pass simulates the continuous temperature and re-evaluates the
best offset at the actual state every step.

Outputs per step: offset, supply temperature, indoor temperature, thermal
and electrical power, COP and cost. The **shadow price** is the marginal
value of one extra kWh stored now (−∂V/∂T / C).

### Baseline and savings

The same model is run with offset 0 (the plain curve), with the room
thermostat described above. The savings forecast is the difference in
energy cost over the horizon, corrected for the value of the heat left in
the building at the end, so pre-heating is not counted as a loss. It only
compares like with like when both keep the comfort band: the savings
sensor's attributes `comfort_deficit_kh` and `baseline_comfort_deficit_kh`
(degree-hours below the band) show when the plain curve would have left the
house too cold - it is then cheaper only because it heats too little. The **total savings** sensor books, between two optimization
runs, the baseline cost minus the optimized cost of the step being executed
- including negative amounts while pre-heating.

## Run advice

The integration never turns hardware on or off; the *Heat pump plan
active* binary sensor is advisory only, for the user's own automation.

With *Follows the plan* the sensor is the plan's on/off decision for the
current step, and it never changes before the minimum run time or pause
has passed (attribute `hold_until`), not even when a re-plan within a step
would allow it. After a restart the measured heat pump state is the
starting point. The rest of this section applies to *Own thermostat*.

`comfort_penalty` is symmetric around the comfort band, so overshooting
`comfort_max` is priced too - the optimizer has no incentive to heat past
what solar and internal gains already cover. Because the DP plan is solved
over the whole horizon, that shows up in the current step's planned
`thermal_power_kw` well before the sun (or the cold) arrives: the sensor
follows that planned power (on above `IDLE_POWER_THRESHOLD_KW`, off below),
not the current indoor temperature. Its `changes_in_minutes` attribute
gives the lead time to the next planned transition.

This differs from *Heat demand*, which is a reactive hysteresis on the
current indoor temperature and target, independent of price or forecast.

## Calibration

Heat loss, thermal mass, solar and internal gains, the COP curve and the
emitter curve can be learned from measurements. See
[Calibration](calibration.md).

## Real-time PV-surplus layer

With grid sensors configured, every minute:

- exporting more than 300 W while the shadow price is above the export
  value (feed-in price / COP: what exporting the electricity for one kWh of
  heat earns instead) raises the offset one degree above the plan;
- the increase is stepped back down once the surplus is gone, judged on the
  grid power *without* the heat pump (grid power minus the heat pump power
  sensor): import caused by the heat pump running on the surplus keeps the
  increase. Without a power sensor, importing more than 300 W counts as no
  surplus;
- an idle heat pump with no export, or a shadow price at or below the
  export value (the building is full, or exporting pays more), also steps
  it down;
- every change holds for at least 15 minutes, so the heat pump's own
  response to a new offset settles before it is judged.

Home batteries come first. What a battery charges is its own surplus: the
heat pump only gets the export left over (a full battery, or one charging
at its limit). What a battery discharges is added to the grid power, as
the import it replaces, so a battery holding the grid at zero while it
powers the heat pump is not mistaken for surplus. Without battery power
sensors, a zero-grid battery hides both, and the layer is better switched
off by leaving the grid sensors empty.

The layer never goes below the plan and never beyond the ramp limit or the
−4 … +4 range, and it carries over when the plan is recomputed. The
result is the *Real-time heating curve offset* sensor.

## Hybrid gas boiler

Policy: **heat pump first, gas as comfort backup.** The *Gas boiler
preferred* binary sensor is decided from the heat-pump-only plan over the
next 3 hours:

| Situation | Gas boiler |
|---|---|
| Indoor temperature inside the comfort band and the plan stays there | Off, even when gas is cheaper. |
| Below the band now (measured, > 0.1 °C) or a planned dip, but the plan is back in the band within 3 hours | On only if gas is cheaper per kWh of heat. |
| The plan is still below the band after 3 hours: the heat pump cannot restore comfort | On, regardless of price (setting *Gas as comfort backup*, default on). With the setting off: only if gas is cheaper. |

"The band" is the per-step band: a planned dip during an away setback does
not count.

Cost per kWh of heat: gas = `gas price / calorific value / efficiency`,
heat pump = `electricity price / COP`, with the optimizer's own COP for
the current step (the calibrated COP model when calibration is applied).

The attributes `comfort_at_risk`, `comfort_reason`
(`below_comfort_band` / `heat_pump_cannot_keep_up`), `gas_cheaper` and
`lowest_planned_indoor_temp` show why the sensor is on or off.
