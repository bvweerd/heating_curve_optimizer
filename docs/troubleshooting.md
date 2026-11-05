# Troubleshooting

Enable debug logging first:

```yaml
logger:
  logs:
    custom_components.heating_curve_optimizer: debug
```

Repair notifications (price, indoor temperature, home schedule, gas price,
weather) clear themselves within seconds once the sensor reports again, and are removed
when the integration is unloaded or the zone/gas boiler they belong to is
deleted.

The *Status* diagnostic sensor shows per coordinator whether it has data
and the last error. **Download diagnostics** on the integration page gives
the full state with sensor IDs redacted.

## COP model does not match the measured COP

A repair issue appears when, over the stored history, the measured COP
(measured heat divided by electrical power) is below 0.7× or above 1.4×
the COP model at the same temperatures.

First make sure the heat measurement is right. A heat meter or the heat
pump's own heat counter is the best source. Flow × supply-return spread is
only as good as the two temperature sensors: a return sensor reading a
little low inflates the spread, and with it the heat. Physics gives a
check: a measured COP above about half the Carnot limit
`(T_supply + 273) / (T_supply − T_outdoor)` is implausible, and so is a
return temperature below the room temperature.

When the measurement is right, set the zone's calibration to *Apply* so
the COP curve is learned from it, or adjust the COP settings. Datasheet
curves are lab values; in real operation cycling, defrost and circulation
pump usually cost some efficiency (one LG Therma V ran at about 0.8× its
datasheet COP in mild weather, measured with its own heat counter).

## Validating the models after a few days

**Download diagnostics** on the integration page and run, in the
development environment:

```bash
python scripts/validate_diagnostics.py config_entry-heating_curve_optimizer-....json
```

It replays the stored 5-minute history: the 6-hour-ahead error of the
one-node model against *Mass + air* at every coupling, the measured COP
against the COP model, run and pause durations of the on/off advice, and
how often the PV-surplus adjustment changed.

## Only two sensors appear

No heating zone has been added yet. Add one from the integration page.

## Optimizer sensors are unavailable

- **Price sensor unavailable**: a repair issue names the sensor.
- **No price forecast**: check that the sensor has one of the supported
  attributes ([Installation](installation.md)). With only a current price
  the optimizer still runs, assuming a flat price.
- **No weather data**: open-meteo.com unreachable; a repair issue appears.

## The offset is always low (or always high)

- Check *Planned indoor temperature* and its `initial_indoor_temp`
  attribute. Without an indoor sensor the target temperature is assumed.
- Check the heating curve settings: they must match the curve on the heat
  pump, otherwise the modelled heat output is wrong.
- Check `ua_w_per_k_in_use` on the *Calibration* sensor: an energy label
  that is far off makes the house look leakier or tighter than it is.
  Calibration corrects this over time.

## Calibration does not progress

`last_result` and `excluded_windows` on the *Calibration* sensor tell why:

| Value | Meaning |
|---|---|
| `no_indoor_sensor` | No (working) indoor temperature sensor. |
| `no_power_reading` | No heat pump power sensor, or its unit is not W/kW. |
| `multi_zone` | Several zones share the heat pump; calibration is off. |
| `excluded_dhw` / `excluded_window_open` | Tap water run or open window during the window. |
| `excluded_temperature_jump` | The indoor temperature jumped more than 1 °C between runs. |
| `elapsed_gap` | Data gap, or the temperature did not move 0.3 °C within 6 hours. |
| `implausible` | The fit is outside the plausible range; it is not used. |
| `fitted` | Working. |

State *Provisional* with enough samples usually means too few cooling (or
heating) windows, or a low `r_squared`: the building needs some variation.
A wrong fit can be cleared with `heating_curve_optimizer.reset_thermal_calibration`.

## Gas boiler advised never turns on

That is the intended heat-pump-first behaviour while the house stays
comfortable, or recovers within 3 hours while the heat pump is cheaper.
Check `comfort_at_risk`, `comfort_reason` and `gas_cheaper` on the binary
sensor.

## Gas boiler cheaper is off during an expensive evening

The heat pump's price is the plan's marginal cost of heat, not the current
electricity price / COP: a house pre-heated on cheap hours coasts through
the peak on heat that has already been paid for. Compare
`heat_pump_cost_now_eur_per_kwh` (heating right now) with
`shadow_price_eur_per_kwh` on the heat pump *Cost per kWh* sensor.

## Total cost savings goes down

Expected while pre-heating: the extra cost is booked when it is made, the
saving when the building coasts through the expensive hours.

## Expert settings diagnostics

The **Download diagnostics** file includes an `expert_settings` section
that shows which expert settings differ from their defaults, plus
context-aware tips. This is the fastest way to spot misconfiguration
when troubleshooting unexpected optimizer behaviour.

See [Expert settings](expert-settings.md) for the full reference.
