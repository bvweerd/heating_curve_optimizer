# Troubleshooting

Enable debug logging first:

```yaml
logger:
  logs:
    custom_components.heating_curve_optimizer: debug
```

Repair notifications (price, indoor temperature, gas price, weather) clear
themselves within seconds once the sensor reports again, and are removed
when the integration is unloaded or the zone/gas boiler they belong to is
deleted.

The *Status* diagnostic sensor shows per coordinator whether it has data
and the last error. **Download diagnostics** on the integration page gives
the full state with sensor IDs redacted.

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

## Gas boiler preferred never turns on

That is the intended heat-pump-first behaviour while the house stays
comfortable, or recovers within 3 hours while the heat pump is cheaper.
Check `comfort_at_risk`, `comfort_reason` and `gas_cheaper` on the binary
sensor.

## Total cost savings goes down

Expected while pre-heating: the extra cost is booked when it is made, the
saving when the building coasts through the expensive hours.
