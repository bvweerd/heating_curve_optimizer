# Troubleshooting Guide

Common issues and solutions for the Heating Curve Optimizer integration.

## Sensor Issues

### All Sensors Unavailable

**Symptoms**: All 17 sensors show "unavailable" after installation

**Causes**:

1. Integration not loaded properly
2. Configuration incomplete
3. First initialization in progress

**Solutions**:

=== "Check Integration Status"
    1. Go to **Settings → Devices & Services**
    2. Find **Heating Curve Optimizer**
    3. Verify status is "Configured" not "Error"
    4. If error, click to see details

=== "Restart Home Assistant"
    ```bash
    # Restart HA to reload integration
    service homeassistant.restart
    ```

=== "Check Logs"
    ```bash
    # Settings → System → Logs
    # Filter for: custom_components.heating_curve_optimizer
    ```

=== "Wait for Initialization"
    First update can take 2-5 minutes. Be patient!

---

### Outdoor Temperature Unavailable

**Symptoms**: `sensor.heating_curve_optimizer_outdoor_temperature` shows "unavailable"

**Causes**:

1. No internet connectivity
2. open-meteo.com API unreachable
3. Invalid coordinates in Home Assistant

**Solutions**:

!!! tip "Check Internet"
    ```bash
    # Test open-meteo.com from HA server
    curl "https://api.open-meteo.com/v1/forecast?latitude=52.0907&longitude=5.1214&hourly=temperature_2m"
    ```

**Check configuration**:
```yaml
# configuration.yaml - verify coordinates
homeassistant:
  latitude: 52.0907  # Must be valid
  longitude: 5.1214
```

**Firewall**: Ensure HA can reach open-meteo.com (port 443)

**Logs to check**:
```
ERROR custom_components.heating_curve_optimizer.sensor
Failed to fetch outdoor temperature: [SSL: CERTIFICATE_VERIFY_FAILED]
```

**Solution**: Update CA certificates on HA server

---

### Heating Curve Offset Always Zero

**Symptoms**: Offset stays at 0°C, never changes

**Causes**:

1. **No price forecast**: Optimization requires future prices
2. **Fixed pricing**: All prices equal, no temporal optimization
3. **Configuration issue**: Planning window or time base incorrect

**Debug Steps**:

=== "Check Price Forecast"
    ```yaml
    # Developer Tools → States
    # Check your price sensor attributes

    sensor.nordpool_kwh_nl_eur_3_10_0:
      state: 0.25
      attributes:
        raw_today:  # Should have data
          - hour: "2025-11-15T00:00:00"
            price: 0.23
          - hour: "2025-11-15T01:00:00"
            price: 0.21
        raw_tomorrow:  # Should have data after ~13:00
          ...
    ```

=== "Check Diagnostics"
    ```yaml
    sensor.heating_curve_optimizer_diagnostics:
      attributes:
        optimization_success: false  # BAD
        errors:
          - "No price forecast available"  # Root cause
    ```

=== "Verify Configuration"
    Settings → Devices & Services → Heating Curve Optimizer → Configure

    - Price sensor selected?
    - Sensor has valid state?

**Solutions**:

| Cause | Solution |
|-------|----------|
| No price sensor configured | Add price sensor in configuration |
| Price sensor unavailable | Fix price integration |
| Price sensor has no forecast | Use different sensor or integration |
| Fixed pricing (intentional) | Accept limited optimization (COP only) |

---

### Heat Buffer Always Zero

**Symptoms**: `sensor.heating_curve_optimizer_heat_buffer` never accumulates

**Causes**:

1. **No solar gain**: Windows not configured or winter conditions
2. **High heat demand**: Loss always exceeds gain
3. **Optimization not utilizing buffer**: Configuration issue

**Debug Steps**:

**Check solar gain sensor**:
```yaml
sensor.heating_curve_optimizer_window_solar_gain:
  state: 0.0  # Check if this ever increases

  attributes:
    glass_south_m2: 0  # Problem: No windows configured!
    solar_radiation: 450  # Radiation available but...
```

**Check net heat loss**:
```yaml
sensor.heating_curve_optimizer_net_heat_loss:
  state: 7.5  # Always positive = never excess solar

  attributes:
    heat_loss: 8.0
    solar_gain: 0.5  # Too small to overcome loss
```

**Solutions**:

| Cause | Solution |
|-------|----------|
| Windows not configured | Add window areas in configuration |
| Wrong orientation | Check if windows actually face south/east/west |
| Winter period | Accept lower buffer (solar angle low) |
| Very cold weather | Buffer only works in mild conditions |
| Cloudy weather | Wait for sunny day |

---

## Optimization Issues

### High Electricity Costs Despite Optimization

**Symptoms**: Bills still high even with integration active

**Reality Check**:

Optimization **reduces** costs, doesn't eliminate them. Typical savings:

- Dynamic pricing: **10-30%**
- Fixed pricing: **2-8%**
- Cold weather: **2-5%**
- Mild weather: **15-30%**

**Debug Steps**:

=== "Verify Offset Application"
    **Critical**: Are you actually *applying* the optimized offset?

    ```yaml
    # Check if this automation exists and is enabled
    automation:
      - alias: "Apply Heating Offset"
        trigger:
          - platform: state
            entity_id: sensor.heating_curve_optimizer_heating_curve_offset
        action:
          - service: number.set_value
            target:
              entity_id: number.your_heat_pump_offset  # YOUR HEAT PUMP
            data:
              value: "{{ states('sensor.heating_curve_optimizer_heating_curve_offset') }}"
    ```

    **If missing**: Integration calculates optimal offset but you're not using it!

=== "Compare to Baseline"
    Track costs before/after:

    ```yaml
    sensor:
      - platform: integration
        source: sensor.heat_pump_power
        name: "Heating Energy Total"
        unit_time: h
    ```

    Compare week-to-week:

    | Week | Energy (kWh) | Avg Outdoor °C | Normalized kWh |
    |------|--------------|----------------|----------------|
    | Before | 420 | 5 | 420 / 5 = 84 |
    | After | 380 | 7 | 380 / 7 = 54 |

    **Improvement**: ~36% after temperature normalization

=== "Check Price Volatility"
    ```yaml
    # Statistics on your price sensor
    sensor.nordpool_kwh_nl_eur_3_10_0:
      attributes:
        min_price_today: 0.15
        max_price_today: 0.42
        average: 0.26
        volatility: 0.27  # max - min
    ```

    **Low volatility** (< €0.10): Limited temporal shifting benefit

    **High volatility** (> €0.20): Good optimization potential

=== "Verify COP Calibration"
    Is your k-factor correct?

    ```python
    # Measure actual COP at different supply temps
    # Compare to predicted COP

    # If predicted >> actual: k-factor too low (increase it)
    # If predicted << actual: k-factor too high (decrease it)
    ```

---

### Offset Changes Too Frequently

**Symptoms**: Offset changes every hour, seems unstable

**Expected Behavior**: Offset *should* change hourly (default time base)!

**Acceptable**: Changes of ±1°C per hour

**Problem**: Changes > ±1°C per hour (violates constraints)

**Causes** (if truly problematic):

1. Price forecast changes drastically (bad data)
2. Configuration error
3. Bug in optimization

**Debug**:

```yaml
# Check offset history
sensor.heating_curve_optimizer_heating_curve_offset:
  history:
    - time: 10:00, offset: 2
    - time: 11:00, offset: 1  # OK: -1°C
    - time: 12:00, offset: 0  # OK: -1°C
    - time: 13:00, offset: -1 # OK: -1°C
```

**Solutions**:

- **Increase time base**: 60 → 120 minutes (slower changes)
- **Check price sensor**: Bad data causes instability
- **Verify k-factor**: Too low can cause aggressive swings

---

### Poor Savings in Cold Weather

**Symptoms**: Minimal savings during winter

**This is normal!** See [Cold Snap Example](../examples/cold-snap.md).

**Why**:

- Heat demand ≈ capacity (no optimization room)
- Low COP in cold (less efficiency gain)
- Minimal solar gain (short days, low angle)

**Expected savings in extreme cold**: **2-5%**

**Expected savings in mild weather**: **15-30%**

**Solution**: Accept physics. Focus on annual average savings.

---

## Configuration Issues

### Invalid Energy Label

**Symptoms**: Sensor unavailable, log shows "Invalid energy label"

**Cause**: Typo in configuration

**Valid labels**: A+++, A++, A+, A, B, C, D, E, F, G

**Solution**: Reconfigure with correct label

---

### COP Seems Wrong

**Symptoms**: COP values don't match heat pump datasheet

**Debug**:

```yaml
sensor.heating_curve_optimizer_quadratic_cop:
  state: 3.45
  attributes:
    base_cop: 3.8
    k_factor: 0.028
    compensation_factor: 0.90
    outdoor_temp: 5.0
    supply_temp: 40.0
    # Calculated: (3.8 + 0.025*5 - 0.028*(40-35)) * 0.90
    #           = (3.8 + 0.125 - 0.14) * 0.90
    #           = 3.785 * 0.90 = 3.41 ≈ 3.45 ✓
```

**Calibration**:

1. Find datasheet COP at A7/W35 → base_cop
2. Measure or estimate k-factor (0.025-0.035 typical)
3. Measure actual system efficiency → adjust compensation

**Example**:

- Datasheet: COP 4.2 at A7/W35
- Measured: System COP 3.8 (including pumps, losses)
- Compensation: 3.8 / 4.2 = **0.90**

---

## Performance Issues

### Slow Updates

**Symptoms**: Sensors take minutes to update

**Causes**:

1. Large planning window (24 hours)
2. Small time base (15 minutes)
3. Slow API responses

**Solutions**:

| Change | Impact |
|--------|--------|
| Planning window: 24h → 6h | 4× faster |
| Time base: 15min → 60min | 16× faster |
| Disable unused sensors | Marginal improvement |

**Acceptable update times**:

- 6h window, 60min base: **0.3-0.8 seconds**
- 12h window, 30min base: **2-4 seconds**
- 24h window, 15min base: **10-20 seconds**

---

### Integration Causes HA Slowdown

**Symptoms**: Home Assistant sluggish after installing integration

**Unlikely**: Integration is lightweight.

**Check**:

1. **Logs for errors**: Repeated errors can cause slowdown
2. **API rate limiting**: open-meteo.com calls (should be cached)
3. **Database size**: Large history on 17 sensors

**Solutions**:

```yaml
# Exclude sensors from recorder to reduce DB size
recorder:
  exclude:
    entities:
      - sensor.heating_curve_optimizer_diagnostics
      - sensor.heating_curve_optimizer_energy_price_level
```

---

## API & Network Issues

### Open-Meteo API Failures

**Symptoms**: Outdoor temperature unavailable, log shows HTTP errors

**Causes**:

1. Internet connectivity issue
2. Firewall blocking API
3. API rate limit (unlikely, free tier is generous)
4. API maintenance (rare)

**Solutions**:

=== "Test Connectivity"
    ```bash
    # From HA server
    curl -v "https://api.open-meteo.com/v1/forecast?latitude=52.0907&longitude=5.1214&hourly=temperature_2m"
    ```

=== "Check Firewall"
    Allow outbound HTTPS (port 443) to api.open-meteo.com

=== "Retry Logic"
    Integration automatically retries failed requests (3 attempts).

    If persistent failures:
    - Check HA server internet
    - Verify DNS resolution
    - Check proxy settings (if any)

---

### Price Sensor Not Updating

**Symptoms**: Optimization stale because price sensor stuck

**This is not an integration issue!** Fix your price sensor.

**Common price integrations**:

- **Nordpool**: Prices update ~13:00 CET for next day
- **ENTSO-E**: Similar schedule
- **Fixed price**: No updates (expected)

**Check**:

```yaml
# Developer Tools → States
sensor.nordpool_kwh_nl_eur_3_10_0:
  last_updated: "2025-11-15T13:05:00"  # Should be recent
```

---

## Advanced Debugging

### Enable Debug Logging

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.heating_curve_optimizer: debug
```

**Restart HA**, then check logs:

```
DEBUG Optimization completed in 450ms, states explored: 48200
DEBUG Price forecast: [0.25, 0.28, 0.35, 0.32, 0.28, 0.22]
DEBUG Demand forecast: [7.0, 6.5, 5.2, 3.8, 4.5, 5.8]
DEBUG Optimal offsets: [2, 1, 0, -1, 0, 1]
DEBUG Buffer evolution: [0, 1.2, 2.5, 2.1, 0.8, 0]
```

### Diagnostics Download

1. Settings → Devices & Services
2. Heating Curve Optimizer → (⋯) → Download Diagnostics
3. Examine JSON file for configuration and state

### Check State Restoration

After HA restart, buffer and offsets should restore:

```yaml
sensor.heating_curve_optimizer_heat_buffer:
  restored: true
  last_state: 3.2  # Should match pre-restart value
```

---

## Getting Help

### Before Asking

1. ✓ Read this troubleshooting guide
2. ✓ Check logs for errors
3. ✓ Download diagnostics
4. ✓ Verify configuration

### Where to Ask

- **GitHub Issues**: [Report bugs](https://github.com/bvweerd/heating_curve_optimizer/issues)
- **Home Assistant Community**: General questions
- **Discord**: Real-time help (if available)

### Include This Information

```
**Integration Version**: 1.0.2
**Home Assistant Version**: 2024.11.1
**Installation Method**: HACS / Manual
**Issue**: Brief description

**Symptoms**: What's happening
**Expected**: What should happen

**Logs**: (paste relevant errors)

**Configuration**:
- Area: 150 m²
- Energy Label: C
- Heat Pump: Brand X, Model Y
- Price Sensor: sensor.nordpool_kwh_nl_eur_3_10_0

**Diagnostics**: (attach JSON file)
```

---

## Known Limitations

### Not Bugs (Working as Designed)

1. **No optimization without price forecast**: Integration requires future prices for temporal shifting
2. **Limited savings in extreme cold**: Physics limits optimization when at capacity
3. **Buffer limited by thermal mass**: Cannot store infinite heat
4. **Update latency**: 60-minute time base means hourly updates (configurable)
5. **Weather forecast dependency**: Accuracy depends on open-meteo.com

### Feature Requests

See [GitHub Issues](https://github.com/bvweerd/heating_curve_optimizer/issues) for planned features and roadmap.

---

**Still stuck?** [Open an issue](https://github.com/bvweerd/heating_curve_optimizer/issues/new) with details!
