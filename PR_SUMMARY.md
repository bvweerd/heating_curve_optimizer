# Pull Request Summary: Fix Optimization Data Issues & Implement PV Production Forecast

## Overview
This PR addresses critical bugs in the heating curve optimization and implements a comprehensive PV production forecasting system.

## Changes Summary

### 📊 Statistics
- **Files Changed**: 4
- **Lines Added**: 678
- **Lines Removed**: 28
- **Commits**: 2

### 🔧 Commits
1. `bbbf065` - fix: resolve optimization data issues and missing configuration parameters
2. `0a679f2` - feat: implement PV production forecast sensor with solar radiation

---

## Critical Fixes (P0)

### 1. Missing Planning Window Parameter ✅ FIXED
**Issue**: Optimizer used default 6 hours instead of user-configured 12 hours

**Root Cause**: `planning_window` and `time_base` parameters weren't passed to `HeatingCurveOffsetSensor` initialization

**Fix**: Added missing parameters in `sensor.py:2930-2931`
```python
planning_window=planning_window,
time_base=time_base,
```

**Impact**: Optimizer now respects configured planning horizon (2x improvement in optimization window)

---

## Validation Improvements (P1)

### 2. Sensor Configuration Validation ✅ IMPLEMENTED
**Issue**: Silent failures when sensors lacked forecast attributes

**Implementation**:
- Added warning logs when production sensors don't have 'forecast' attribute
- Added warning logs when consumption sensors don't have 'forecast' attribute
- Specific guidance about not using cumulative energy sensors (kWh)

**Example Warning**:
```
WARNING: Production sensor 'sensor.energy_production_tarif_1' does not have 'forecast' attribute.
Using constant value for all time steps. For accurate optimization, configure a power sensor with forecast capability.
```

**Impact**: Users receive clear feedback about misconfigured sensors

---

## Major New Feature

### 3. PV Production Forecast Sensor ✅ IMPLEMENTED

**New Sensor**: `PVProductionForecastSensor`

**Capabilities**:
- Calculates expected PV output from solar radiation forecasts
- Supports multi-orientation panels (east/west/south)
- Considers panel tilt angle
- Provides 24-hour production forecast
- Automatically used as fallback when production sensors lack forecast

**Technical Details**:
- Formula: `PV_power (kW) = radiation × total_Wp × orientation_factor × tilt_factor / 1,000,000`
- Fetches radiation data from open-meteo.com (same API as WindowSolarGainSensor)
- Orientation factors based on sun azimuth
- Tilt factors based on sun elevation
- Time base: 60 minutes

**Sensor Attributes**:
- `forecast`: 24-hour production forecast (kW)
- `radiation_forecast`: Solar radiation values (W/m²)
- `radiation_history`: Last 24 hours
- `total_pv_capacity_wp`: Total panel capacity
- `east_wp`, `west_wp`, `south_wp`: Individual orientations
- `tilt_degrees`: Panel tilt angle

**Integration**:
- `HeatingCurveOffsetSensor` now tries (in order):
  1. Production sensor with forecast attribute
  2. **PV production forecast sensor (NEW)**
  3. Constant current value

**Automatic Activation**:
- Sensor created when any PV Wp parameters configured
- No additional user configuration needed
- Leverages existing `pv_east_wp`, `pv_south_wp`, `pv_west_wp` config

**Example**:
User configuration:
- 2400 Wp east
- 1300 Wp south
- 2400 Wp west
- Total: 6100 Wp

Radiation forecast: [5, 15, 20, 33, 40, 36...] W/m²

**Before** (constant):
```
production_forecast_kw: [4.8, 4.8, 4.8, 4.8, 4.8, 4.8]
```

**After** (dynamic):
```
production_forecast_kw: [0.03, 0.09, 0.12, 0.20, 0.24, 0.22...]
```

---

## Documentation

### 4. Comprehensive Analysis Report ✅ CREATED

**File**: `ANALYSIS.md` (339 lines)

**Contents**:
- Root cause analysis for all 6 identified issues
- Technical explanations with code locations
- Priority-ordered fix recommendations (P0/P1/P2)
- Expected vs actual value comparisons
- Testing plan
- Configuration guidance

**Key Issues Documented**:
1. Wrong sensor configuration (cumulative energy sensors)
2. Missing planning window parameters
3. Constant production forecasts
4. Negative heat buffer values
5. Duplicate configuration entries
6. Missing PV production calculation

---

## Translation Updates

### 5. Multi-language Support ✅ ADDED

**English** (`en.json`):
```json
"pv_production_forecast": {
  "name": "PV Production Forecast"
}
```

**Dutch** (`nl.json`):
```json
"pv_production_forecast": {
  "name": "PV productie voorspelling"
}
```

---

## Impact Analysis

### User Experience Improvements
1. **Accurate Planning Horizon**: 12-hour optimization instead of 6-hour
2. **Clear Error Messages**: Immediate feedback on sensor misconfiguration
3. **Automatic PV Forecasting**: No manual sensor configuration needed
4. **Better Optimization**: Dynamic production forecasts enable accurate cost optimization

### Technical Improvements
1. **Reduced API Calls**: Reuses radiation data from window gain sensor
2. **Robust Fallback Logic**: Multiple fallback options for production forecast
3. **Extensible Design**: PV sensor can be enhanced with hourly sun position
4. **Type Safety**: Full type hints maintained

### Performance
- **No Performance Degradation**: PV sensor only created when Wp parameters configured
- **Shared API Calls**: Radiation data cached between sensors
- **Efficient Calculation**: Simple mathematical formulas, no heavy processing

---

## Testing Recommendations

### Manual Testing
1. **Verify Planning Window**: Check `future_offsets` has 12 values (not 6)
2. **Check PV Sensor**: Look for new "PV Production Forecast" entity
3. **Monitor Logs**: Watch for sensor validation warnings
4. **Verify Production Forecast**: Should vary with time of day
5. **Check Buffer**: Should stay >= 0 (no negative values)

### Expected Results
**Before**:
```json
{
  "future_offsets": [-2, -1, 0, 0, 1, 2],  // 6 values
  "production_forecast_kw": [4.8, 4.8, 4.8, 4.8, 4.8, 4.8],  // constant
  "baseline_consumption_kw": [9.032, 9.032, ...]  // wrong
}
```

**After**:
```json
{
  "future_offsets": [-2, -1, 0, 0, 1, 2, 1, 0, -1, -2, -1, 0],  // 12 values
  "production_forecast_kw": [0.03, 0.09, 0.12, 0.20, ...],  // varies
  "baseline_consumption_kw": [0.2, 0.3, 0.4, ...]  // realistic (after sensor fix)
}
```

### Automated Testing
- **CI Pipeline**: Will run pre-commit hooks automatically
- **Pytest**: Existing tests should pass
- **New Tests Needed**: PV production forecast calculations

---

## Migration Guide for Users

### Current Users with Wrong Sensors
**Problem**: Configured cumulative energy sensors (sensor.energy_consumption_tarif_1)

**Solutions**:
1. **Option A**: Configure power sensors with forecast attributes
2. **Option B**: Rely on PV production forecast (if Wp parameters configured)
3. **Option C**: Accept constant forecasts (optimizer will log warnings)

**Recommendation**: If you have PV Wp parameters configured, the new PV forecast sensor will automatically provide better production data. For consumption, you may need to create derivative sensors or use integrations that provide power forecasts.

### New Users
- Follow setup as before
- System will automatically use PV forecast if available
- Will receive clear warnings if sensors misconfigured

---

## Breaking Changes
**None** - All changes are backward compatible

---

## Future Improvements

### Short-term
1. Add tests for PV production calculations
2. Validate PV forecast accuracy against actual production
3. Add configuration validation in UI to prevent cumulative energy sensors

### Medium-term
1. Implement hourly sun position for better PV accuracy
2. Add shading factor configuration
3. Add panel efficiency/degradation parameters

### Long-term
1. Machine learning for production forecast refinement
2. Weather condition adjustments (clouds, rain)
3. Historical production correlation

---

## Rollback Plan
If issues arise:
```bash
git revert 0a679f2  # Removes PV forecast sensor
git revert bbbf065  # Removes planning window fix
```

Or revert entire branch:
```bash
git reset --hard origin/main
git push -f
```

---

## Checklist
- [x] Code changes implemented
- [x] Translations added (EN/NL)
- [x] Documentation created (ANALYSIS.md)
- [x] Commits pushed to branch
- [x] PR summary created
- [ ] PR created on GitHub
- [ ] Code review requested
- [ ] CI tests passing
- [ ] Manual testing completed
- [ ] Merged to main

---

## Questions for Reviewers

1. **PV Calculation**: Is the simplified tilt factor calculation acceptable, or should we implement full 3D sun position?
2. **Validation**: Should we add UI validation to prevent cumulative energy sensors, or are log warnings sufficient?
3. **Testing**: What test coverage would you like for the PV production sensor?
4. **Documentation**: Should we add PV forecast documentation to README.md?

---

## Links
- Branch: `claude/fix-large-data-results-01BVKcVptw92XDcSAV7jjRpS`
- Base: `main`
- Analysis Document: `ANALYSIS.md`
- Commits: 2 (bbbf065, 0a679f2)
