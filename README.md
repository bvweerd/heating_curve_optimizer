# Heating Curve Optimizer

This Home Assistant integration calculates the optimal heating curve offset for the next few hours. All values are exposed as sensors for use in automations.

## Overview
- Retrieves weather and solar radiation data from *open-meteo.com*.
- Estimates hourly heat loss and net heat demand for your home.
- Creates sensors for electricity prices, consumption and production.
- Predicts standby energy usage and current net power.
- Optimizes the heating curve offset using a dynamic programming algorithm.

## Configuration
Configuration is done entirely through the UI. The following options can be provided:
- Home area in square meters (`area_m2`).
- Energy label from A to G (`energy_label`).
- Window areas facing east, west and south and the glass U‑value.
- Optional indoor temperature, power consumption and supply temperature sensors.
- A price sensor for electricity rates.
- Optional multiple consumption and production sources.
- The `k_factor` describing how the COP declines as the supply temperature rises.

## Sensors
| Sensor | Description |
|-------|-------------|
| `sensor.outdoor_temperature` | Outdoor temperature with a 24h forecast. |
| `sensor.current_consumption_price` | Current electricity price for consumption. |
| `sensor.current_production_price` | Current electricity price for production. |
| `sensor.hourly_heat_loss` | Estimated heat loss in kW per hour. |
| `sensor.window_solar_gain` | Expected solar gain through windows in kW. |
| `sensor.hourly_net_heat_demand` | Net heat demand after subtracting solar gain. |
| `sensor.expected_energy_consumption` | Average standby power usage per hour. |
| `sensor.current_net_consumption` | Current net power (consumption minus production). |
| `sensor.heat_pump_cop` | COP derived from outdoor and supply temperature. |
| `sensor.heat_pump_thermal_power` | Current thermal output of the heat pump. |
| `sensor.heating_curve_offset` | Optimal offset for the next six hours. |

The `sensor.heating_curve_offset` attributes include future offsets and the price list used for the calculation.

## Automation Example
```yaml
- alias: "Update heating curve"
  trigger:
    - platform: state
      entity_id: sensor.heating_curve_offset
  action:
    - service: number.set_value
      target:
        entity_id: number.heat_curve_offset
      data:
        value: "{{ states('sensor.heating_curve_offset') | float }}"
```

## Installation
1. Copy `custom_components/heating_curve_optimizer` into `<config>/custom_components`.
2. Restart Home Assistant.
3. Add the integration via **Settings → Integrations**.
4. Complete the setup steps and save.

