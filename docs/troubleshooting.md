# Troubleshooting

Enable debug logging first:

```yaml
logger:
  logs:
    custom_components.heating_curve_optimizer: debug
```

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
- Check `ua_w_per_k_in_use` on *Thermal calibration samples*: an energy
  label that is far off makes the house look leakier or tighter than it
  is. Calibration corrects this over time.

## Calibration does not progress

`last_result` on *Thermal calibration samples* tells why:

| Value | Meaning |
|---|---|
| `no_indoor_sensor` | No (working) indoor temperature sensor. |
| `no_power_reading` | No heat pump power sensor, or its unit is not W/kW. |
| `elapsed_gap` | Too long between runs, or the temperature did not move 0.3 °C within 6 hours. |
| `implausible` | The fit is more than 3× off the label estimate; samples are kept, the fit is not applied. |
| `fitted` | Working; applied after 30 samples. |

A wrong fit can be cleared with `heating_curve_optimizer.reset_thermal_calibration`.

## Gas boiler preferred never turns on

That is the intended heat-pump-first behaviour while the house stays
comfortable, or recovers within 3 hours while the heat pump is cheaper.
Check `comfort_at_risk`, `comfort_reason` and `gas_cheaper` on the binary
sensor.

## Total cost savings goes down

Expected while pre-heating: the extra cost is booked when it is made, the
saving when the building coasts through the expensive hours.
