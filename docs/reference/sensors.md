# Sensor Reference

Complete reference for all 19 sensors provided by the Heating Curve Optimizer integration.

## Core Sensors

### Outdoor Temperature
**Entity ID**: `sensor.heating_curve_optimizer_outdoor_temperature`

**Description**: Current outdoor temperature with 24-hour forecast

**Unit**: °C

**Update Frequency**: Every 5 minutes

**Data Source**: open-meteo.com API

**Attributes**:
```yaml
forecast: [5.2, 5.5, 6.1, 6.8, ...]  # 24-hour forecast
last_update: "2025-11-15T10:30:00"
latitude: 52.0907
longitude: 5.1214
```

**Usage**: Base input for all heat loss and COP calculations

---

### Heat Loss
**Entity ID**: `sensor.heating_curve_optimizer_heat_loss`

**Description**: Instantaneous heat loss through building envelope

**Unit**: kW

**Formula**:
\\[ Q_{loss} = U \times A \times (T_{indoor} - T_{outdoor}) / 1000 \\]

**Attributes**:
```yaml
u_value: 0.80  # W/m²K
area_m2: 150
indoor_temp: 20.0
outdoor_temp: 5.0
```

**Typical Range**:

- Well-insulated (A label): 2-8 kW
- Average (C label): 4-12 kW
- Poor (E label): 8-20 kW

---

### Window Solar Gain
**Entity ID**: `sensor.heating_curve_optimizer_window_solar_gain`

**Description**: Heat gain from solar radiation through windows

**Unit**: kW

**Formula**:
\\[ Q_{solar} = g \times A_{window} \times I_{radiation} \times \cos(\theta) / 1000 \\]

Where:

- \\( g = 0.7 \\): Solar heat gain coefficient (typical double glazing)
- \\( \theta \\): Angle of incidence

**Attributes**:
```yaml
glass_east_gain: 0.3  # kW
glass_west_gain: 0.2  # kW
glass_south_gain: 2.1  # kW
total_gain: 2.6  # kW
solar_radiation: 450  # W/m²
```

**Typical Range**:

- Night: 0 kW
- Cloudy day: 0.5-1.5 kW
- Sunny day: 3-8 kW
- Peak summer: 10-15 kW (not heating season)

---

### Net Heat Loss
**Entity ID**: `sensor.heating_curve_optimizer_net_heat_loss`

**Description**: Heat loss minus solar gain (actual heating demand)

**Unit**: kW

**Formula**:
\\[ Q_{net} = Q_{loss} - Q_{solar} \\]

**Attributes**:
```yaml
heat_loss: 8.5  # kW
solar_gain: 2.6  # kW
net_loss: 5.9  # kW
is_negative: false
```

**Typical Range**:

- Cold night: 8-12 kW (no solar)
- Cloudy day: 4-8 kW
- Sunny day: -2 to +4 kW (can be negative!)

!!! info "Negative Values"
    Negative net loss means solar gain exceeds heat loss. This excess energy is stored in the thermal buffer.

---

## Optimization Sensors

### Heating Curve Offset ⭐
**Entity ID**: `sensor.heating_curve_optimizer_heating_curve_offset`

**Description**: **Primary optimization output** - optimal heating curve offset

**Unit**: °C

**Range**: -4 to +4

**Update Frequency**: Every 60 minutes (configurable)

**Attributes**:
```yaml
offset_forecast: [2, 1, 0, -1, -2, 0]  # 6-hour forecast
optimization_time_ms: 450
states_explored: 48200
optimal_cost: 2.45  # EUR for planning window
buffer_evolution: [0, 1.2, 2.5, 2.1, 0.8, 0]
```

**Usage**: Apply this offset to your heating system (via automation)

**Interpretation**:

- **Positive** (+1 to +4): Increase supply temperature
- **Zero** (0): Use base heating curve
- **Negative** (-1 to -4): Decrease supply temperature

---

### Optimized Supply Temperature
**Entity ID**: `sensor.heating_curve_optimizer_optimized_supply_temperature`

**Description**: Forecast of optimal supply temperatures

**Unit**: °C

**Attributes**:
```yaml
forecast_temperatures: [40, 38, 37, 35, 36, 38]  # °C
forecast_offsets: [2, 1, 0, -1, -2, 0]
base_temperatures: [38, 37, 37, 36, 38, 38]
```

**Usage**: For systems that accept absolute supply temperature (not offset)

---

### Heat Buffer
**Entity ID**: `sensor.heating_curve_optimizer_heat_buffer`

**Description**: Accumulated thermal energy in building thermal mass

**Unit**: kWh

**Range**: 0 to ~20 kWh (typical)

**Attributes**:
```yaml
buffer_forecast: [3.2, 4.1, 4.8, 3.5, 1.2, 0]
buffer_source: "solar"  # or "pre-heating"
max_buffer_24h: 8.5
```

**Interpretation**:

- **0 kWh**: No buffer (typical in cold/cloudy conditions)
- **1-5 kWh**: Moderate buffer
- **5-15 kWh**: High buffer (sunny days)
- **> 15 kWh**: Exceptional (rare)

---

### Cost Savings Forecast
**Entity ID**: `sensor.heating_curve_optimizer_cost_savings_forecast`

**Description**: Predicted cost savings from optimization over the planning window (typically 6 hours)

**Unit**: €

**State Class**: `measurement` (forecast value, not cumulative)

**Attributes**:
```yaml
total_cost_eur: 2.45  # Optimized cost
baseline_cost_eur: 2.73  # Cost without optimization
cost_savings_eur: 0.28  # Savings
savings_percentage: 10.3  # Percentage saved
planning_window_hours: 6
```

**Formula**:
\\[ Savings = Cost_{baseline} - Cost_{optimized} \\]

**Usage**: Monitor optimization effectiveness and forecast savings

---

### Total Cost Savings
**Entity ID**: `sensor.heating_curve_optimizer_total_cost_savings`

**Description**: **Cumulative** cost savings since integration activation

**Unit**: €

**State Class**: `total_increasing` (cumulative counter)

**Attributes**:
```yaml
last_update: "2025-11-15T10:30:00"
time_base_minutes: 60
```

**Behavior**:
- Accumulates only **positive** savings
- Updates every `time_base` minutes (default: 60)
- Persists across restarts
- Only counts when offset ≠ 0 and heat demand > 0

**Typical Growth**:
- Per day: €0.50 - €2.00
- Per week: €3.00 - €15.00
- Per month: €15.00 - €60.00
- Per year: €200.00 - €700.00

**Usage**: Track total financial benefit and ROI

---

## Price & Power Sensors

### Current Electricity Price
**Entity ID**: `sensor.heating_curve_optimizer_current_electricity_price`

**Description**: Current electricity price with forecast

**Unit**: €/kWh (or your configured currency)

**Attributes**:
```yaml
price_forecast: [0.25, 0.28, 0.35, 0.32, 0.28, 0.22]
forecast_source: "nordpool"
consumption_price: 0.25
production_price: 0.08
net_price: 0.17
```

---

### Net Power Consumption
**Entity ID**: `sensor.heating_curve_optimizer_net_power_consumption`

**Description**: Current net power (consumption - production)

**Unit**: kW

**Formula**:
\\[ P_{net} = P_{consumption} - P_{production} \\]

**Attributes**:
```yaml
consumption: 3.5  # kW
production: 2.1  # kW (solar)
net: 1.4  # kW
```

**Interpretation**:

- **Positive**: Consuming from grid
- **Zero**: Self-sufficient
- **Negative**: Exporting to grid

---

## Heat Pump Sensors

### Quadratic COP
**Entity ID**: `sensor.heating_curve_optimizer_quadratic_cop`

**Description**: Heat pump Coefficient of Performance

**Unit**: Dimensionless (ratio)

**Formula**:
\\[ \text{COP} = (COP_{base} + 0.025 \times T_{out} - k \times (T_{supply} - 35)) \times f \\]

**Attributes**:
```yaml
base_cop: 3.8
k_factor: 0.028
compensation_factor: 0.90
outdoor_temp: 5.0
supply_temp: 40.0
calculated_cop: 3.45
```

**Typical Range**:

- Excellent conditions: 4.5-5.5
- Good conditions: 3.5-4.5
- Average conditions: 2.8-3.5
- Poor conditions (cold): 2.0-2.8

---

### Heat Pump Thermal Power
**Entity ID**: `sensor.heating_curve_optimizer_heat_pump_thermal_power`

**Description**: Current heat pump thermal output

**Unit**: kW

**Formula**:
\\[ P_{thermal} = P_{electrical} \times COP \\]

**Attributes**:
```yaml
electrical_power: 2.5  # kW
cop: 3.5
thermal_power: 8.75  # kW
```

---

### Calculated Supply Temperature
**Entity ID**: `sensor.heating_curve_optimizer_calculated_supply_temperature`

**Description**: Current supply temperature based on heating curve + offset

**Unit**: °C

**Formula**:
\\[ T_{supply} = T_{base}(T_{outdoor}) + \text{offset} \\]

**Attributes**:
```yaml
base_temperature: 38.0
offset: 2.0
calculated_temperature: 40.0
outdoor_temperature: 5.0
```

---

## Forecast & Diagnostic Sensors

### Energy Consumption Forecast
**Entity ID**: `sensor.heating_curve_optimizer_energy_consumption_forecast`

**Description**: Predicted electricity consumption for planning window

**Unit**: kWh

**Attributes**:
```yaml
hourly_forecast: [2.8, 2.5, 1.9, 1.2, 0.8, 1.5]
total_forecast: 11.7  # kWh
baseline_forecast: 13.2  # kWh (without optimization)
savings_forecast: 1.5  # kWh
```

---

### Energy Price Level
**Entity ID**: `sensor.heating_curve_optimizer_energy_price_level`

**Description**: Categorization of current price (low/medium/high)

**State**: `low`, `medium`, or `high`

**Attributes**:
```yaml
current_price: 0.25
price_threshold_low: 0.22
price_threshold_high: 0.32
percentile: 45  # Current price is 45th percentile
```

---

### COP Delta
**Entity ID**: `sensor.heating_curve_optimizer_cop_delta`

**Description**: COP improvement from optimization compared to baseline (heating curve without offset)

**Unit**: Dimensionless (COP points)

**Formula**:
\\[ \Delta COP = COP_{optimized} - COP_{baseline} \\]

Where:
- \\( COP_{baseline} \\): COP calculated using heating curve without offset
- \\( COP_{optimized} \\): COP with optimized offset applied

**Attributes**:
```yaml
future_cop: [3.65, 3.68, 3.71, ...]  # Optimized COP forecast
cop_deltas: [0.13, 0.15, 0.14, ...]  # Delta for each hour
baseline_cop: 3.52  # COP without offset
```

**Behavior**:
- Returns `0.0` when offset is `0` (no optimization active)
- Stable when offset remains constant
- Updates when outdoor temperature or offset changes

**Usage**: Monitor COP efficiency gains from optimization

---

### Heat Generation Delta
**Entity ID**: `sensor.heating_curve_optimizer_heat_generation_delta`

**Description**: Heat output difference from optimization compared to baseline

**Unit**: kW

**Formula**:
\\[ \Delta Q = Q_{optimized} - Q_{baseline} \\]

Where:
- \\( Q_{baseline} \\): Heat output at baseline COP (without offset)
- \\( Q_{optimized} \\): Heat output at optimized COP (with offset)

**Attributes**:
```yaml
future_heat_generation: [12.5, 12.8, 13.1, ...]  # Optimized heat forecast (kW)
heat_deltas: [0.5, 0.6, 0.5, ...]  # Delta for each hour (kW)
baseline_heat_generation: 12.0  # Heat without offset (kW)
baseline_cop: 3.52  # Baseline COP
```

**Behavior**:
- Returns `0.0` when offset is `0`
- Positive delta: more heat generated (higher COP from lower supply temp)
- Negative delta: less heat generated (lower COP from higher supply temp)

**Usage**: Monitor heat output changes from optimization

---

### Diagnostics
**Entity ID**: `sensor.heating_curve_optimizer_diagnostics`

**Description**: Comprehensive diagnostic information

**State**: Number of sensors active

**Attributes**:
```yaml
version: "1.0.2"
sensors_active: 17
last_optimization: "2025-11-15T10:00:00"
optimization_success: true
errors: []
warnings: ["Price forecast limited to 12 hours"]

# Optimization metrics
optimization_time_ms: 450
states_explored: 48200
optimal_cost: 2.45

# Configuration
planning_window_hours: 6
time_base_minutes: 60
area_m2: 150
energy_label: "C"

# Current state
buffer_kWh: 3.2
offset: 1
supply_temp: 40.0
outdoor_temp: 5.0
cop: 3.45
```

**Usage**: Troubleshooting and monitoring integration health

---

## Binary Sensor

### Heat Demand
**Entity ID**: `binary_sensor.heating_curve_optimizer_heat_demand`

**Description**: Indicates if heating is currently needed

**Device Class**: `heat`

**State**:

- `on`: Heat demand > 0 (net heat loss positive)
- `off`: No heat demand (solar gain sufficient)

**Attributes**:
```yaml
net_heat_loss: 5.9  # kW
threshold: 0  # kW
```

**Usage**: Automations requiring binary heat/no-heat logic

---

## Sensor Update Behavior

### Update Intervals

| Sensor | Update Frequency | Trigger |
|--------|------------------|---------|
| Outdoor Temperature | 5 min | Time interval |
| Heat Loss | 5 min | Outdoor temp change |
| Solar Gain | 5 min | Solar radiation change |
| Net Heat Loss | 5 min | Heat loss or solar gain change |
| Heating Curve Offset | 60 min | Time interval + price change |
| COP | Real-time | Supply/outdoor temp change |
| Buffer | 60 min | Offset optimization |
| Prices | Varies | Price sensor update |

### Availability

Sensors become unavailable when:

1. **Dependencies unavailable**: E.g., net heat loss unavailable if outdoor temp sensor fails
2. **API failure**: Outdoor temperature unavailable if open-meteo.com unreachable
3. **Configuration error**: E.g., invalid energy label
4. **Initialization**: First 1-2 minutes after restart

Check logs for specific unavailability reasons.

---

## Best Practices

### Monitoring

Create a dashboard to monitor key sensors:

```yaml
type: entities
title: Heating Optimization
entities:
  - sensor.heating_curve_optimizer_heating_curve_offset
  - sensor.heating_curve_optimizer_heat_buffer
  - sensor.heating_curve_optimizer_quadratic_cop
  - sensor.heating_curve_optimizer_current_electricity_price
  - binary_sensor.heating_curve_optimizer_heat_demand
```

### Automation

Apply the optimized offset automatically:

```yaml
automation:
  - alias: "Apply Heating Offset"
    trigger:
      - platform: state
        entity_id: sensor.heating_curve_optimizer_heating_curve_offset
    action:
      - service: number.set_value
        target:
          entity_id: number.your_heat_pump_offset
        data:
          value: "{{ states('sensor.heating_curve_optimizer_heating_curve_offset') }}"
```

### Statistics

Track long-term trends:

```yaml
sensor:
  - platform: statistics
    entity_id: sensor.heating_curve_optimizer_heat_buffer
    name: "Buffer Statistics"
    sampling_size: 100
    max_age:
      days: 7
```

---

**Related**: [Troubleshooting Guide](troubleshooting.md) for sensor issues
