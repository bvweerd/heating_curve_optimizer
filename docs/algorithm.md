# How it works

## Coordinators

```
Weather (30 min, open-meteo)
  └─ Heat calculation, per zone (5 min): heat loss, solar gain, internal gains, PV
       └─ Optimization, per zone (15 min, on price change, on setpoint change)
            └─ Gas boiler comparison (optional)
```

All forecasts are put on one time grid: one step per price period, the
first step starting *now* and ending at the next price boundary. Hourly
weather values are averaged onto that grid by overlap. open-meteo reports
radiation as the mean over the *preceding* hour; the integration shifts it
so every value describes the hour it is used for.

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

## Optimizer

Backward-induction dynamic programming over the state
`(indoor temperature, previous offset)`:

```
V_t(T, o_prev) = min over o of  cost_t(T, o) + comfort_t(T') + λ·(o − o_prev)² + V_t+1(T', o)
```

- **Decision**: offset `o` ∈ {−4 … +4}, with at most
  `step_minutes / offset_delta_t` degrees change per step. What else the
  plan controls depends on the *Switching* setting:
    - **Own thermostat**: the heat pump stops on its own room thermostat;
      the model tapers its output to zero over 0.3 °C above the target.
      The plan only moves the offset.
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
- **Transition**: `T'` from the building model, computed exactly (not
  rounded to the grid).
- **Value function**: stored on a 0.1 °C grid and interpolated linearly.
  Interpolation matters: with building time constants of tens of hours the
  temperature often changes by less than half a grid cell per step, which a
  rounding DP would not see at all.
- **Energy cost**: grid electricity × price, PV-covered electricity × feed-in
  price.
- **Comfort**: 50 €/K²/h below or above the band, plus a 1000 € penalty
  below `comfort_min − 1.5 °C`. With a home schedule `comfort_min` is per
  step: lowered by the away setback for steps that end while the schedule
  is off (see [Away periods](configuration.md#away-periods)).
- **Terminal value**: heat stored above the comfort floor at the end of the
  horizon is worth what it would cost to produce then, so the plan does not
  empty the building just because the horizon ends; a deficit below the
  floor (an away setback running past the horizon) costs the same to make
  up.

The forward pass simulates the continuous temperature and re-evaluates the
best offset at the actual state every step.

Outputs per step: offset, supply temperature, indoor temperature, thermal
and electrical power, COP and cost. The **shadow price** is the marginal
value of one extra kWh stored now (−∂V/∂T / C).

### Baseline and savings

The same model is run with offset 0 (the plain curve), with a room
thermostat that stops the heat pump 0.3 °C above the target. The savings forecast
is the difference in energy cost over the horizon, corrected for the value
of the heat left in the building at the end, so pre-heating is not counted
as a loss. The **total savings** sensor books, between two optimization
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

- exporting more than 300 W while the shadow price is positive raises the
  offset one degree above the plan;
- the increase is stepped back down once the surplus is gone, judged on the
  grid power *without* the heat pump (grid power minus the heat pump power
  sensor): import caused by the heat pump running on the surplus keeps the
  increase. Without a power sensor, importing more than 300 W counts as no
  surplus;
- an idle heat pump with no export, or a shadow price of zero or less (the
  building is full), also steps it down;
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
heat pump = `electricity price / COP` at the planned operating point.

The attributes `comfort_at_risk`, `comfort_reason`
(`below_comfort_band` / `heat_pump_cannot_keep_up`), `gas_cheaper` and
`lowest_planned_indoor_temp` show why the sensor is on or off.
