# Entities

Entity IDs below assume the default device name *Heating Curve Optimizer*
and English entity names.

## Main device

| Entity | Unit | Description | Key attributes |
|---|---|---|---|
| `sensor.…_outdoor_temperature` | °C | Current outdoor temperature (open-meteo). | `forecast`, `humidity_forecast` |
| `sensor.…_heating_curve_supply_temperature` | °C | Supply temperature of the plain heating curve now. | |
| `sensor.…_heat_loss` | kW | Transmission + ventilation loss at the current indoor temperature. | `forecast` (at target), `htc_w_per_k` |
| `sensor.…_window_solar_gain` | kW | Solar gain through the windows this hour. | `forecast` |
| `sensor.…_net_heat_demand` | kW | Heat loss minus solar and internal gains. | `forecast`, `indoor_temperature_source` |
| `sensor.…_pv_production_forecast` | kW | Only with PV arrays. | `forecast` |
| `sensor.…_heating_curve_offset` | °C | **Offset to apply now.** | `offsets`, `prices`, `outdoor_forecast`, `step_start_times`, `step_minutes` |
| `sensor.…_optimized_supply_temperature` | °C | Planned supply temperature now. | `supply_temps`, `baseline_supply_temps` |
| `sensor.…_planned_indoor_temperature` | °C | Predicted indoor temperature at the end of the current step. | `indoor_temps`, `baseline_indoor_temps`, `comfort_min/max` |
| `sensor.…_heat_buffer` | kWh | Heat stored above the comfort floor. | `forecast` |
| `sensor.…_planned_cop` | | COP of the planned operating point now. | `cop`, `baseline_cop`, `cop_delta_now` |
| `sensor.…_cost_savings_forecast` | EUR | Expected savings over the horizon vs. the plain curve. | `optimized_cost_eur`, `baseline_cost_eur`, `horizon_hours` |
| `sensor.…_total_cost_savings` | EUR | Running total of realised modelled savings (can decrease). | |
| `sensor.…_real_time_heating_curve_offset` | °C | Only with grid sensors: plan + PV-surplus adjustment. | `planned_offset`, `adjustment`, `current_grid_w` |
| `sensor.…_heat_pump_thermal_power` | kW | Only with a power sensor: electrical power × COP. | `cop`, `supply_temperature` |
| `sensor.…_heat_pump_thermal_energy` | kWh | Only with a power sensor: cumulative heat delivered. | |
| `binary_sensor.…_heat_demand` | | On while the indoor temperature is below the upper comfort bound. | `heat_demand_factor`, bounds |
| `number.…_target_indoor_temperature` | °C | Primary zone setpoint (15–25). | |
| `number.…_comfort_band_below_target` / `…_above_target` | °C | Comfort band (0.1–2). | |
| `climate.…_heating` | | Disabled by default; the same setpoint as a thermostat card. | |

| `sensor.…_calibration` | | Calibration status and results, see [Calibration](calibration.md). | `progress_pct`, `effective_energy_label`, `time_constant_hours`, … |
| `sensor.…_model_accuracy` | K | Mean absolute error of 1-hour-ahead indoor predictions (24 h). | `mae_label_model_k`, `errors_k`, `two_mass_suspected` |

Diagnostic entities: *Value of stored heat* (shadow price, EUR/kWh) and
*Status* (`ok`/`partial`/`initializing`/`error`, per coordinator).

## Additional zones

Heating curve offset, optimized supply temperature, planned indoor
temperature, cost savings forecast, net heat demand and heat demand.

## Gas boiler device

| Entity | Unit | Description |
|---|---|---|
| `sensor.…_heat_pump_cost_per_kwh_heat` | EUR/kWh | Electricity price / COP. |
| `sensor.…_gas_boiler_cost_per_kwh_heat` | EUR/kWh | Gas price / calorific value / efficiency. |
| `sensor.…_gas_boiler_advantage_per_kwh_heat` | EUR/kWh | Heat pump cost − gas cost (positive: gas cheaper). |
| `binary_sensor.…_gas_boiler_preferred` | | See [hybrid gas boiler](algorithm.md#hybrid-gas-boiler). |

## Service

`heating_curve_optimizer.reset_thermal_calibration` (optional `entry_id`):
discard the learned heat loss and thermal mass of all zones.
