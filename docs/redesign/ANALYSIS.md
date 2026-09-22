# Diagnostic Analysis Report - Heating Curve Optimizer

**Date**: 2025-11-16
**Analysis of**: Diagnostic data showing incorrect large numbers and mismatches

---

## Executive Summary

The diagnostic data reveals **6 critical issues** causing incorrect optimization results:

1. **Wrong sensor types configured** → Extremely high baseline consumption (9.032 kW)
2. **Missing planning_window parameter** → Using 6 hours instead of configured 12 hours
3. **Constant production forecast** → No variation with solar radiation
4. **Negative heat buffer** → Result of incorrect demand/production forecasts
5. **Duplicate configuration entry** → Third entry duplicates the first
6. **Missing PV production calculation** → PV Wp parameters stored but not used

---

## Issue 1: Wrong Sensor Configuration (CRITICAL)

### Problem
```json
"baseline_consumption_kw": [9.032, 9.032, 9.032, 9.032, 9.032, 9.032]
"production_forecast_kw": [4.8, 4.8, 4.8, 4.8, 4.8, 4.8]
```

Both values are:
- **Unrealistically high/constant** (9.032 kW baseline is ~10x normal household consumption)
- **Identical across all time steps** (should vary with time of day)

### Root Cause

**User configured cumulative energy sensors instead of power sensors:**

```json
"configurations": [
  {
    "source_type": "Electricity consumption",
    "sources": ["sensor.energy_consumption_tarif_1", "sensor.energy_consumption_tarif_2"]
  },
  {
    "source_type": "Electricity production",
    "sources": ["sensor.energy_production_tarif_1", "sensor.energy_production_tarif_2"]
  }
]
```

These sensors:
- Are cumulative energy counters (kWh) with state_class "total_increasing"
- **Do NOT have "forecast" attributes**
- Current state is total accumulated energy (e.g., 9032 kWh since installation)

**Code behavior** (sensor.py:2131-2139, 2184-2209):
1. Checks for "forecast" attribute → NOT FOUND
2. Falls back to current sensor state
3. Converts if > 100: `value / 1000` (assumes Watts → kW)
4. Uses this constant value for ALL time steps

**Result**: Cumulative energy value (9032 kWh) ÷ 1000 = 9.032, misinterpreted as 9.032 kW power.

### Impact
- **Optimization is using garbage data**
- Net balance calculations are completely wrong
- Negative buffer values (-0.907 kWh) result from unrealistic forecasts
- Price selection logic (production vs consumption price) is incorrect

### Solution Required
**Option A**: Configure correct sensors with forecast attributes
**Option B**: Implement PV production calculation from Wp parameters and solar radiation (see Issue 6)
**Option C**: Add validation to reject sensors without forecast attributes with clear error message

---

## Issue 2: Planning Window Not Applied (BUG)

### Problem
```json
"options": { "planning_window": 12 }
"future_offsets": [-2, -1, 0, 0, 1, 2]  // Only 6 values, should be 12
```

Configuration shows 12 hours, but optimization only calculates 6 hours.

### Root Cause

**Bug in sensor.py:2915-2930** - Missing parameters in HeatingCurveOffsetSensor initialization:

```python
heating_curve_offset_sensor = HeatingCurveOffsetSensor(
    hass=hass,
    name="Heating Curve Offset",
    unique_id=f"{entry.entry_id}_heating_curve_offset",
    net_heat_sensor=net_heat_sensor,
    price_sensor=consumption_price_sensor,
    device=device_info,
    k_factor=k_factor,
    cop_compensation_factor=cop_compensation_factor,
    outdoor_temp_coefficient=outdoor_temp_coefficient,
    consumption_sensors=consumption_sources,
    heatpump_sensor=power_sensor,
    production_sensors=production_sources,
    production_price_sensor=production_price_sensor,
    outdoor_sensor=outdoor_sensor_entity,
    # MISSING: planning_window=planning_window
    # MISSING: time_base=time_base
)
```

The sensor class accepts these parameters (line 1986-1987) but they're not passed during initialization.

**Result**: Uses default `DEFAULT_PLANNING_WINDOW = 6` instead of configured value of 12.

### Impact
- Optimization only looks ahead 6 hours instead of 12 hours
- Suboptimal decisions due to limited planning horizon
- User configuration is ignored

### Fix Location
**File**: `custom_components/heating_curve_optimizer/sensor.py`
**Line**: 2915-2930
**Action**: Add missing parameters:
```python
planning_window=planning_window,
time_base=time_base,
```

---

## Issue 3: Constant Production Forecast

### Problem
```json
"production_forecast_kw": [4.8, 4.8, 4.8, 4.8, 4.8, 4.8]
"radiation_forecast": [5.0, 15.0, 20.0, 33.0, 40.0, 36.0, ...]
```

Solar radiation varies (5 → 40 W/m²) but production forecast is constant at 4.8 kW.

### Root Cause

Same as Issue 1: `sensor.energy_production_tarif_1` doesn't have "forecast" attribute, so code uses current state value (4800 W → 4.8 kW) for all time steps.

### Impact
- Optimizer doesn't benefit from solar production timing
- Price selection is wrong (should use production price when PV is high)
- Missed opportunities for cost optimization

### Solution
See Issue 6 - implement PV production forecast calculation from radiation and Wp parameters.

---

## Issue 4: Negative Heat Buffer

### Problem
```json
"state": "-0.609",
"buffer_evolution": [-0.609, -0.907, -0.907, -0.907, -0.624, -0.07]
```

Buffer goes negative, indicating "heat debt" (demand exceeds supply).

### Root Cause

**Cascading effect from Issues 1-3**:
- Incorrect baseline consumption (9.032 kW) is too high
- Constant production forecast (4.8 kW) doesn't match reality
- Net balance is wrong: `prod - cons = 4.8 - 9.032 = -4.232 kW`
- Optimization algorithm tries to compensate but can't avoid negative buffer

### Impact
- Buffer physically can't go negative (can't borrow heat from the future)
- Indicates optimization is working with impossible scenarios
- Results are meaningless

### Solution
Fix Issues 1-3, then buffer should stabilize to realistic positive/zero values.

---

## Issue 5: Duplicate Configuration Entry

### Problem
```json
"configurations": [
  {
    "source_type": "Electricity consumption",
    "sources": ["sensor.energy_consumption_tarif_1", "sensor.energy_consumption_tarif_2"]
  },
  {
    "source_type": "Electricity production",
    "sources": ["sensor.energy_production_tarif_1", "sensor.energy_production_tarif_2"]
  },
  {
    "source_type": "Electricity consumption",  // DUPLICATE
    "sources": ["sensor.energy_consumption_tarif_1", "sensor.energy_consumption_tarif_2"]
  }
]
```

Third entry duplicates the first.

### Root Cause
Likely UI bug in config flow allowing duplicate entries.

### Impact
**Currently: MITIGATED** by sensor.py:2729-2731:
```python
# Deduplicate sources while preserving order (fixes issue with duplicate configs)
consumption_sources = list(dict.fromkeys(consumption_sources))
production_sources = list(dict.fromkeys(production_sources))
```

This code deduplicates the sensor lists, so functionally it works. However, it's still incorrect configuration.

### Solution
- Low priority (already mitigated)
- Could add validation in config_flow.py to prevent duplicates
- Or add UI button to remove configuration entries

---

## Issue 6: Missing PV Production Calculation

### Problem

Integration collects PV parameters:
```json
"pv_east_wp": 2400.0,
"pv_south_wp": 1300.0,
"pv_west_wp": 2400.0,
"pv_tilt": 45.0
```

But these are **never used** to calculate production forecast. No sensor exists to compute expected PV output from solar radiation forecast.

### Current State

- `WindowSolarGainSensor` calculates **thermal** gain from radiation (for heating reduction)
- **No equivalent sensor** for electrical PV production
- Production forecast relies entirely on configured sensors having forecast attributes

### Solution Needed

Create a new `PVProductionForecastSensor`:
- Inputs: radiation forecast, PV Wp (east/south/west), tilt, efficiency
- Output: Expected PV production (kW) per time step
- Similar logic to WindowSolarGainSensor but for electrical output
- Use this as fallback when production sensors lack forecast attributes

---

## Configuration Data vs Options (NOT AN ISSUE)

### Observation
```json
"data": { "area_m2": 0.0, "base_cop": 4.2, "k_factor": 0.11, "planning_window": 6 }
"options": { "area_m2": 159.0, "base_cop": 4.7, "k_factor": 0.028, "planning_window": 12 }
"stored_data": { "area_m2": 0.0, ... }  // Matches data
```

### Analysis
This is **expected behavior**, not a bug:
- `data`: Original configuration from initial setup
- `options`: Current active configuration (updated via options flow)
- `stored_data`: Copy of `data` for diagnostic purposes

**Code correctly prioritizes options > data** (sensor.py:2733-2795):
```python
area_m2 = entry.options.get(CONF_AREA_M2, entry.data.get(CONF_AREA_M2))
# Uses options first, falls back to data if options not set
```

**Evidence it's working**: HeatLossSensor shows correct calculation:
```
htc_w_per_k: 150.8
area_m2: 159 (from options)
heat_loss: 2.02 kW ✓
```

If it used `area_m2: 0.0` from data, HTC would be ~0 and heat_loss would be 0.

---

## Priority Fixes

### P0 - Critical (Blocks functionality)
1. **Fix Issue 2**: Add `planning_window=planning_window, time_base=time_base` to HeatingCurveOffsetSensor initialization
2. **Fix Issue 1**: Implement validation or PV production calculation

### P1 - High (Degrades functionality)
3. **Fix Issue 6**: Implement PVProductionForecastSensor
4. **Fix Issue 1**: Add validation warning when sensors lack forecast attributes

### P2 - Low (Cosmetic/already mitigated)
5. **Fix Issue 5**: Prevent duplicate configurations in UI

---

## Recommended Immediate Actions

1. **Add planning_window and time_base parameters** to HeatingCurveOffsetSensor initialization (5-minute fix)

2. **Validate sensor configuration** - Add check for forecast attributes with clear error message:
   ```python
   state = hass.states.get(consumption_sensors[0])
   if state and "forecast" not in state.attributes:
       _LOGGER.warning(
           "Consumption sensor %s does not have 'forecast' attribute. "
           "Please configure a power sensor (W/kW) with forecast, not a cumulative energy sensor (kWh).",
           consumption_sensors[0]
       )
   ```

3. **Implement PV production forecast** - Create sensor that calculates expected production from:
   - Solar radiation forecast (already fetched)
   - PV Wp parameters (already configured)
   - Time of day, panel orientation, efficiency (~15-20%)

4. **Update documentation** - Clarify required sensor types in README:
   - ✅ Power sensors with forecast attribute (W or kW)
   - ❌ Cumulative energy sensors (kWh) - won't work correctly

---

## Testing Plan After Fixes

1. Configure correct sensors or wait for PV forecast implementation
2. Verify `future_offsets` has 12 values (matches configured planning_window)
3. Verify baseline_consumption is realistic (0.2-0.5 kW for typical household)
4. Verify production_forecast varies with time (high during day, zero at night)
5. Verify heat buffer stays >= 0 (no negative values)
6. Verify optimization status shows "OK"

---

**End of Analysis**
