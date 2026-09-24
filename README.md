# Heating Curve Optimizer

[![GitHub Release](https://img.shields.io/github/release/bvweerd/heating_curve_optimizer.svg?style=flat-square)](https://github.com/bvweerd/heating_curve_optimizer/releases)
[![License](https://img.shields.io/github/license/bvweerd/heating_curve_optimizer.svg?style=flat-square)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)](https://hacs.xyz)

A Home Assistant integration that plans the **heating curve offset** of a
weather-compensated heat pump for the next 24 hours: heat when electricity
is cheap and the heat pump is efficient, coast when it is expensive, and
keep the indoor temperature inside the comfort band you choose.

> **Experimental.** The integration only publishes sensors; you apply the
> planned offset to your heat pump with your own automation. Watch your
> installation closely during the first weeks.

📖 **Documentation:** <https://bvweerd.github.io/heating_curve_optimizer/>

## How it works

- Weather forecast (temperature, humidity, direct/diffuse radiation) from
  [open-meteo.com](https://open-meteo.com), no account needed.
- Your electricity price forecast (Nord Pool, ENTSO-E, Dynamic Energy
  Contract Calculator, …), 15- or 60-minute prices.
- A physical model of the house (heat loss, thermal mass, solar and
  internal gains, emitters) and of the heat pump (COP depending on outdoor
  and supply temperature, defrost losses, Carnot limit).
- Dynamic programming over the indoor temperature finds the cheapest
  offset sequence (−4 … +4 °C) that stays within the comfort band.
- Heat loss and thermal mass are calibrated automatically from measured
  indoor temperature and heat pump power.

Optional: PV arrays (PV-covered electricity valued at the feed-in price),
a real-time PV-surplus layer, a hybrid gas boiler (heat pump first, gas as
comfort backup or when comfort is at risk and gas is cheaper), and several
heating zones.

## Installation

1. HACS → Integrations → ⋮ → *Custom repositories* → add this repository
   as *Integration*, install **Heating Curve Optimizer**, restart.
2. *Settings → Devices & services → Add integration → Heating Curve
   Optimizer*: select your price sensor, enter the heat pump's COP data and
   its heating curve.
3. On the integration page, **Add heating zone**: floor area, energy label,
   construction, emitters, windows, setpoint and indoor temperature sensor.

Requires Home Assistant 2025.4 or newer.

## Using the result

| Entity | Use |
|---|---|
| `sensor.heating_curve_optimizer_heating_curve_offset` | Offset to apply now; full plan in the `offsets` attribute. |
| `sensor.heating_curve_optimizer_optimized_supply_temperature` | Planned supply temperature. |
| `sensor.heating_curve_optimizer_planned_indoor_temperature` | Predicted indoor temperature trajectory. |
| `sensor.heating_curve_optimizer_cost_savings_forecast` | Expected savings vs. the plain curve. |

Example automation:

```yaml
automation:
  - alias: Apply heating curve offset
    trigger:
      - platform: state
        entity_id: sensor.heating_curve_optimizer_heating_curve_offset
    condition: "{{ trigger.to_state.state not in ['unknown', 'unavailable'] }}"
    action:
      - service: number.set_value
        target:
          entity_id: number.my_heat_pump_heating_curve_offset
        data:
          value: "{{ trigger.to_state.state | int }}"
```

See the [documentation](https://bvweerd.github.io/heating_curve_optimizer/)
for all settings, entities, the model and troubleshooting.

## Development

```bash
pip install -r requirements.txt pre-commit mypy
pre-commit run --all-files
mypy custom_components/heating_curve_optimizer
pytest
```

## License

Apache License 2.0, see [LICENSE](LICENSE).
