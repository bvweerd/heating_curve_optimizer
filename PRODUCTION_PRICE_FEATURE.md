# Feature Request: Dynamic Price Selection Based on Net Energy Balance

## Problem

Currently, the heating curve optimization always uses **consumption prices** for cost calculations, regardless of whether the household has net energy consumption or production at any given time.

This is suboptimal when solar panels are producing more energy than the house consumes (net production). In such cases:
- The household is selling energy back to the grid at **production prices** (typically lower than consumption prices)
- When the heat pump runs, it reduces net production, which means **lost revenue** at production prices
- The optimization should account for this opportunity cost

## Current Behavior

**sensor.py:2631** - HeatingCurveOffsetSensor always uses `consumption_price_sensor`:
```python
heating_curve_offset_sensor = HeatingCurveOffsetSensor(
    hass=hass,
    name="Heating Curve Offset",
    unique_id=f"{entry.entry_id}_heating_curve_offset",
    net_heat_sensor=net_heat_sensor,
    price_sensor=consumption_price_sensor,  # ← Always consumption prices
    ...
)
```

## Desired Behavior

The optimization should dynamically select prices based on the **net energy balance** for each time step:

### Net Consumption (Consumption > Production + Heat Pump)
- Use **consumption prices**
- Cost = `(consumption - production + heat_pump_power) × consumption_price / COP`

### Net Production (Production > Consumption + Heat Pump)
- Use **production prices**
- Cost = `(production - consumption - heat_pump_power) × production_price / COP`
- This represents lost revenue (opportunity cost)

### Example

**18:00-19:00:**
- Solar production: 3.0 kW
- Baseline consumption (without heat pump): 1.5 kW
- Heat pump power: 0.8 kW
- Net balance: 3.0 - 1.5 - 0.8 = +0.7 kW (still producing)
- **Use production price** because we're reducing sellback

**19:00-20:00:**
- Solar production: 0.5 kW
- Baseline consumption: 1.5 kW
- Heat pump power: 0.8 kW
- Net balance: 0.5 - 1.5 - 0.8 = -1.8 kW (consuming)
- **Use consumption price** because we're buying from grid

## Implementation Considerations

### 1. Production Forecast Required

The optimization needs forecasts of:
- Solar production (kW per time step)
- Baseline consumption (kW per time step, excluding heat pump)

Currently available:
- `production_sensors` - list of production sensor entity IDs
- `consumption_sensors` - list of consumption sensor entity IDs

**Challenge:** How to get **forecasts** from these sensors?
- Most solar inverters don't provide production forecasts
- May need to use solar radiation forecast (already available via OutdoorTemperatureSensor) + PV system parameters
- Baseline consumption forecast could use historical averages

### 2. Net Balance Calculation Per Time Step

For each optimization step `t`:
```python
# Get forecasts
production_forecast[t]  # kW from PV
baseline_consumption[t]  # kW household load (excluding heat pump)

# Calculate heat pump power from offset and COP
heat_pump_power[t] = demand[t] / COP[t]  # kW

# Net balance
net_balance[t] = production_forecast[t] - baseline_consumption[t] - heat_pump_power[t]

# Select price
if net_balance[t] < 0:
    price[t] = consumption_price[t]  # Buying from grid
else:
    price[t] = production_price[t]   # Reducing sellback (opportunity cost)
```

### 3. Optimization Impact

The optimization algorithm would need to consider:
- **High production prices + net production** → Good time to heat (reduces expensive sellback reduction)
- **Low consumption prices + net consumption** → Good time to heat (cheap electricity)
- **Low production prices + net production** → Avoid heating (don't reduce cheap sellback)
- **High consumption prices + net consumption** → Avoid heating (expensive electricity)

### 4. Code Changes Required

**sensor.py - HeatingCurveOffsetSensor.__init__():**
- Add `production_price_sensor` parameter
- Store both price sensors

**sensor.py - HeatingCurveOffsetSensor.async_update():**
- Extract production price forecast
- Extract production forecast (from production_sensors or solar model)
- Extract baseline consumption forecast
- Calculate net balance per time step
- Build dynamic price array based on net balance

**sensor.py - _optimize_offsets():**
- Modify to accept two price arrays (consumption and production)
- Modify to accept net balance forecast
- Select appropriate price per step based on net balance

## Testing Strategy

1. **Unit tests** for net balance calculation with various scenarios
2. **Integration test** with mock solar production and price sensors
3. **Validation** against real-world data to ensure sensible behavior

## Alternative: Simplified Approach

If full net balance tracking is too complex, a **simplified heuristic** could be:

- If any production sensor shows current production > 0.5 kW
- AND production price is available
- Use **minimum(consumption_price, production_price)** for optimization

This would at least partially account for production price scenarios without full forecasting.

## References

- Current price sensor selection: sensor.py:2500-2507, 2631
- Production sensors passed to HeatingCurveOffsetSensor: sensor.py:2638
- Optimization function: sensor.py:1719-1901 (_optimize_offsets)
- Price extraction: sensor.py:2034-2048 (_extract_prices)

## Priority

**Medium-High** - This affects cost optimization accuracy for households with significant solar production. Without this feature, the optimizer may make suboptimal decisions during high solar production periods.

## Related Issues

- Buffer calculation showing negative values (separate issue, addressed in resampling fix)
- Price resampling from 15-min to 60-min intervals (fixed in commit 8b574ed)
