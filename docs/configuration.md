# Configuration

All settings are made in the UI. Main settings can be changed later through
**Configure** on the integration page; zones, PV arrays and the gas boiler
through their own **Reconfigure** menu. Changing a main setting reloads the
integration; changing the target temperature or comfort band through the
number/climate entities re-runs the optimizer immediately without a reload.

## Main entry

### Electricity prices

| Setting | Default | Notes |
|---|---|---|
| Consumption price sensor | — | Required. Must carry a forecast (see [Installation](installation.md)). The optimizer's steps follow this sensor's interval, so quarter-hour prices give quarter-hour planning. If the sensor only has a current price, that price is assumed for the whole horizon. |
| Feed-in price sensor | — | Optional. Heat-pump electricity covered by your own PV is valued at this price. Without it, 0.07 EUR/kWh is used, so PV-covered heating is never treated as free. |

### Measurement sensors (optional)

| Setting | Used for |
|---|---|
| Heat pump electrical power | Thermal power and energy sensors; calibration; the gas-boiler comparison. W or kW (unit attribute required). |
| Heat pump thermal power | Heat meter or output reported by the heat pump (W/kW). Calibration without relying on the COP model, and learning the COP curve. |
| Tap water heating active | Binary sensor, on while the heat pump heats tap water; those periods are left out of calibration. Empty: no tap water operation. |
| Measured supply temperature | COP of the actual operating point (thermal power sensor, calibration). Without it the planned supply temperature is used. |
| Measured return temperature | Supply minus return is the real water spread, used for the mean water temperature in the emitter calibration. Without it a 5 K spread is assumed. |
| Heat meter (energy) | Cumulative thermal energy in kWh (Wh/MWh converted), e.g. the heat pump's own heat counter. Delivered heat = its increase over the last 10 minutes. The best heat source after a thermal power sensor; used for calibration, the COP curve and the *Thermal power* sensor. |
| Local outdoor temperature | Optional, in °C. Replaces the weather service for the current outdoor temperature. The difference with the weather service corrects the forecast, fading out over 6 hours (at most 5 K). Falls back to the weather service when unavailable; read at each weather update (30 min). |
| Water flow | L/min, L/h or m³/h. With supply and return temperature it measures the delivered heat: flow × spread × 4.16 kJ/(L·K), counted only while the heat pump draws more than its standby power. A sensor offset between supply and return is learned or set (see [Calibration](calibration.md#supply-return-sensor-offset)). Used like a heat meter (calibration, COP curve, *Thermal power* sensor); a thermal power sensor takes precedence. |
| Grid import / export power | Enables the real-time PV-surplus layer. W or kW; a sensor without unit is read as W. |
| Home battery power | One sensor per battery, positive while charging (the Battery Controller convention; the setup wizard offers its batteries). Discharge counts as grid import for the PV-surplus layer. |

### Heat pump

COP model:

```
COP = (base_cop + outdoor_coefficient × T_outdoor − k_factor × (T_supply − 35)) × compensation
COP ≤ (T_supply + 273.15) / (T_supply − T_outdoor)          (Carnot limit)
COP × defrost factor (0.6 … 1.0, between −10 °C and +6 °C at high humidity)
```

| Setting | Default | Typical |
|---|---|---|
| Base COP | 4.2 | COP at 35 °C supply and 0 °C outdoor. From the datasheet (A2/W35 is a good reference). |
| k-factor | 0.11 | COP loss per °C supply temperature above 35 °C. 0.08–0.12 for air-to-water. |
| Outdoor temperature coefficient | 0.08 | COP gain per °C outdoor temperature. 0.05–0.10. |
| Compensation factor | 1.0 | Scales the model to your measured seasonal COP. |
| Maximum thermal power | empty | Rated heating capacity in kW at 7 °C outdoor (A7/W35); the model lowers it 2 % per K in colder air. Empty: 1.3 × the building's heat loss at the design outdoor temperature (holding there). |
| Switching | Own thermostat | *Own thermostat*: the heat pump switches on its own room thermostat around the target (modelled as holding target + 0.15 °C); the plan only sets the offset. *Follows the plan*: an automation switches the heat pump with the *Plan active* binary sensor, and the plan decides when it runs. |
| Minimum run time / pause | 60 / 60 min | Only with *Follows the plan*: every run and every pause lasts at least this long, to spare the compressor. |

### Heating curve

Enter the curve exactly as it is set on the heat pump; the optimizer adds an
offset of −4 … +4 °C on top of it.

| Setting | Default | Notes |
|---|---|---|
| Supply temperature at the warm end | 25 °C | Used at and above the warm-end outdoor temperature. |
| Supply temperature at the cold end | 45 °C | Used at and below the design outdoor temperature. The emitters are assumed to deliver exactly the building's heat loss at this point. |
| Design outdoor temperature | −10 °C | Dutch design value. |
| Warm-end outdoor temperature | 15 °C | |
| Minutes per 1 °C offset change | 30 | Ramp-rate limit: 30 allows 2 °C per hour. With 15-minute steps the limit is at least 1 °C per step. |

### Advanced

| Setting | Default | Notes |
|---|---|---|
| Planning horizon | 24 h | Rounded up to whole price periods and limited by the available price forecast (day-ahead prices usually reach 12–36 h ahead). |

## Heating zone

| Setting | Default | Notes |
|---|---|---|
| Name | — | |
| Heated floor area | — | m². |
| Energy label | C | Converted to a heat loss coefficient (NTA 8800 energy use × heating share / degree days), plus ventilation loss. Replaced by the calibrated value once learned. |
| Ventilation | natural (standard) | Air change rate for the ventilation loss. |
| Ceiling height | 2.5 m | Volume for the ventilation loss. |
| Construction | medium | Thermal mass: light 40, medium 90, heavy 165 Wh/(m²·K). |
| Heat emitters | radiators | Emitter exponent: radiators 1.3, underfloor 1.1, fan coils 1.0. |
| Building model | One node | *Mass + air*: the heat pump heats floor and structure, sun and internal gains warm the air directly. Recommended for underfloor heating in a heavy house; needs the indoor temperature sensor. See [Algorithm](algorithm.md#mass-air-opt-in-per-zone). |
| Mass-air coupling | empty | Only with *Mass + air*. Empty: learned from the history after a few days (until then 55 W/K per m² floor). |
| Internal heat gains | 3 W/m² | People, appliances, lighting. |
| Window area south/east/west | 0 | m² glass; solar gain uses sun position and direct/diffuse irradiance. |
| Glazing U-value | 1.2 | Determines the solar heat gain coefficient. |
| Target temperature, comfort band below/above | 20 °C, 0.3, 0.5 | The optimizer keeps the indoor temperature between target − below and target + above. Deviations are penalised quadratically (50 €/K²/h). |
| Indoor temperature sensor | — | Strongly recommended. Without it the optimizer assumes the room is at its target temperature and calibration is disabled. An unavailable sensor raises a repair issue. |
| Window and door contacts | — | Optional binary sensors; periods with an open window are left out of calibration. |
| Home schedule | — | Optional [schedule helper](https://www.home-assistant.io/integrations/schedule/), on while someone is home. See [Away periods](#away-periods). |
| Away setback | 2 °C | How far the bottom of the comfort band drops while the home schedule is off. |
| Calibration | Observe | Off / Observe / Apply, see [Calibration](calibration.md). |
| Own heating curve (warm/cold end) | empty | Only for a separate heating circuit with its own curve. Fill both or neither. |

The first zone is the primary zone; its setpoints are exposed as number and
climate entities. Additional zones each get their own device with offset,
supply temperature, planned indoor temperature, savings and heat demand.

### Away periods

With a home schedule the comfort floor follows the schedule: every planning
step that ends while the schedule is off may drop to `target − below −
away setback`. The top of the band stays where it is. Because the optimizer
reads the whole weekly schedule, it knows when you come back and reheats in
the cheapest hours before the schedule switches on, instead of heating hard
at whatever price the moment you return.

Lowering the target with an automation when you leave does not do this:
the optimizer then only learns about the return when the target goes back
up, with the room already below the band.

In a heavy, well-insulated house the room cools slowly, so an away period
mostly gives the optimizer freedom to skip expensive hours; the energy
saved by the lower temperature itself is small. If the schedule cannot be
read, a repair issue appears and the full band applies all the time.

!!! note "Several zones on one heat pump"
    Each zone is optimized independently, as its own heating circuit. The
    shared heat pump capacity is not divided between zones.

## PV array

Peak power (kWp), orientation (azimuth, 180 = south), tilt, system
efficiency and DC coupling. Several arrays can be added. When Battery
Controller has PV arrays, you can import one.

## Gas boiler (hybrid)

Gas price sensor (EUR/m³), boiler efficiency (default 0.90), calorific
value (default 9.77 kWh/m³, Dutch upper heating value), an optional **gas
meter** (m³ or kWh, used to calibrate the heat pump's COP level, see
[Calibration](calibration.md)) and **Gas as comfort
backup** (default on): recommend the boiler whenever the heat pump cannot
restore the comfort band within 3 hours, even if gas is more expensive.
Switch it off to use gas only when it is also cheaper.

**Gas boiler heat in the building model.** Heat from the boiler goes into
the building model (thermal mass, heat buffer, learned heat loss), which
otherwise sees a gas run as the house warming up on its own: the buffer
goes negative right after heating on gas and the learned heat loss comes
out low. The integration uses the best source you have:

| You have | Boiler heat |
|---|---|
| **Boiler heat power** sensor (W/kW: heat meter, ems-esp, ebusd) | measured |
| **Boiler heats the house** signal + **gas meter** | signal × output, the output **learned** per gas meter step: gas heat / hours the signal was on |
| **Boiler heats the house** signal only | signal × **boiler heat output** (default 6 kW) |
| neither | none; the mass estimate only follows the measured air |

*Boiler heats the house* is a binary sensor, switch or climate entity
(heating while its `hvac_action` is `heating`). It must not be on for tap
water: a gas meter cannot tell heating from tap water, so the signal and
the main entry's **DHW active** sensor make that split. While DHW is
active the boiler counts as not heating the house (a combi boiler gives
tap water priority), and meter steps with tap water are not learned from.
Examples:

- OpenTherm gateway: a template that is on with flame on, CH mode on and
  DHW mode off; DHW active = the gateway's DHW mode.
- A smart thermostat that drives the boiler: its climate entity.
- A boiler switched by a relay: that switch.

The learned output, its source and the hours behind it are attributes of
the calibration sensor (`gas_boiler_output_kw`, `gas_boiler_heat_source`,
`gas_boiler_output_learned_hours`). See
[How it works](algorithm.md#hybrid-gas-boiler) for when the boiler is
recommended.
