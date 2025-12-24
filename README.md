# Heating Curve Optimizer

**⚠️ EXPERIMENTAL INTEGRATION - EXPERT USERS ONLY ⚠️**

**Intelligent heating optimization for Home Assistant**

Minimize electricity costs while maximizing comfort using dynamic programming and predictive algorithms.

[![GitHub Release](https://img.shields.io/github/release/bvweerd/heating_curve_optimizer.svg?style=flat-square)](https://github.com/bvweerd/heating_curve_optimizer/releases)
[![License](https://img.shields.io/github/license/bvweerd/heating_curve_optimizer.svg?style=flat-square)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-Default-orange.svg?style=flat-square)](https://hacs.xyz)

---

## ⚠️ Important Notice

**This integration is EXPERIMENTAL and intended for EXPERT USERS only.**

### Before Installing

- **NOT production-ready**: This integration has NOT been extensively validated across multiple homes and configurations
- **Expert knowledge required**: You should have deep understanding of:
  - Heat pump systems and heating curves
  - Building thermodynamics and energy labels
  - Home Assistant automation and YAML
  - Debugging and troubleshooting complex integrations
- **Risk of system instability**: Incorrect configuration can lead to:
  - Suboptimal heating performance
  - Increased energy costs
  - Potential system damage if not monitored carefully
- **Active monitoring required**: You MUST closely monitor the system after installation
- **No warranty**: Use entirely at your own risk

### Prerequisites

You may need the **[Dynamic Energy Contract Calculator](https://github.com/bvweerd/dynamic_energy_contract_calculator)** integration to provide the required electricity price forecasts for this integration to work optimally.

---

## What is Heating Curve Optimizer?

This Home Assistant custom integration automatically adjusts your heating system to minimize electricity costs while maintaining optimal comfort. It uses advanced **dynamic programming** algorithms to predict and optimize heating based on:

- **Weather Forecasts** - Temperature and solar radiation from open-meteo.com
- **Electricity Prices** - Dynamic pricing for consumption and production
- **Heat Pump Efficiency** - Real-time COP (Coefficient of Performance) calculations
- **Building Characteristics** - Your home's thermal properties and energy label

## Key Features

### Cost Optimization
Dynamically adjusts heating curves to minimize electricity costs while meeting heat demand. The optimizer shifts heating load to periods with lower electricity prices when possible.

### Solar Integration
Automatically accounts for solar gain through windows and solar production, creating a thermal buffer that reduces heating requirements during peak price periods.

### Smart Predictions
Uses a 6-hour planning horizon with dynamic programming to find the optimal heating strategy considering:
- Variable electricity prices
- Weather forecasts (temperature, solar radiation)
- Heat pump efficiency curves
- Building thermal mass

### Comfort Constraints
Maintains supply temperature within configurable limits while respecting maximum rate-of-change constraints to prevent system stress.

## How It Works

The integration continuously:

1. **Fetches** weather forecasts and electricity price predictions
2. **Calculates** heat demand based on your building's thermal properties
3. **Optimizes** heating curve offsets using dynamic programming
4. **Outputs** optimal supply temperature adjustments
5. **Adapts** in real-time as conditions change

The COP (Coefficient of Performance) is calculated using:

```
COP = (COP_base + α × T_outdoor - k × (T_supply - 35)) × f
```

Where:
- `COP_base` is the base COP at 35°C supply temperature
- `α = 0.025` is the outdoor temperature coefficient
- `k` is the k-factor (how COP degrades with supply temperature)
- `f` is the COP compensation factor (accounts for system losses)

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Go to "Integrations"
3. Click the "+" button
4. Search for "Heating Curve Optimizer"
5. Click "Download"
6. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/heating_curve_optimizer` directory to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services**
4. Click **Add Integration**
5. Search for "Heating Curve Optimizer"

## Configuration

Configuration is done entirely through the UI in multiple steps:

### Step 1: Building Characteristics

Configure your building's thermal properties:

- **Home area** (m²): Total heated floor area
- **Energy label**: Select from A+++ to G
  - This determines the thermal transmittance (U-value) of your building envelope
  - If uncertain, start conservative (e.g., C or D) and calibrate later
- **Ceiling height** (m): Average ceiling height (default: 2.5m)
- **Ventilation type**: Natural, mechanical exhaust, balanced, or heat recovery
  - Affects air change rate and heat loss calculations

### Step 2: Window Configuration

Configure solar gain through windows:

- **East-facing windows** (m²): Total glass area facing east (±45°)
- **West-facing windows** (m²): Total glass area facing west (±45°)
- **South-facing windows** (m²): Total glass area facing south (±45°)
- **Glass U-value** (W/m²·K): Thermal transmittance of your windows
  - Single glazing: ~5.0
  - Double glazing: ~2.8
  - HR++ glazing: ~1.2
  - Triple glazing: ~0.8

### Step 3: Heat Pump Parameters

**⚠️ Critical Section - Incorrect values can cause poor optimization**

Configure your heat pump's efficiency characteristics:

- **Base COP**: COP at A7/W35 conditions (7°C outdoor, 35°C supply)
  - Check your heat pump's datasheet
  - Typical range: 3.0-4.5 for air-source, 4.0-5.5 for ground-source

- **K-factor**: How much COP decreases per °C supply temperature increase
  - Typical values:
    - Low-temp optimized heat pumps: 0.015-0.025
    - Standard heat pumps: 0.025-0.035
    - Older models: 0.035-0.045
  - **Start with 0.025 if uncertain**

- **COP Compensation Factor**: Accounts for real-world system losses
  - Theoretical COP × compensation = actual COP
  - Well-designed system: 0.90-0.95
  - Average system: 0.85-0.90
  - Poor system: 0.80-0.85
  - **Start with 0.90 if uncertain**

- **Outdoor Temperature Coefficient**: COP increase per °C outdoor temperature
  - Default: 0.025 (suitable for most heat pumps)
  - Adjust based on manufacturer data if available

### Step 4: Temperature Limits

Configure safe operating ranges:

- **Minimum supply temperature** (°C): Lowest safe supply temp (typically 20-30°C)
- **Maximum supply temperature** (°C): Highest safe supply temp (typically 45-55°C)
- **Minimum outdoor temperature** (°C): Coldest expected outdoor temp (typically -20 to -10°C)
- **Maximum outdoor temperature** (°C): Warmest temp requiring heating (typically 15-20°C)

### Step 5: Sensor Selection

**Required Sensors:**

- **Consumption price sensor**: Entity providing current electricity price
  - Must have forecast attributes (e.g., from Dynamic Energy Contract Calculator)
  - Supported formats: `raw_today`/`raw_tomorrow`, `forecast_prices`, or `net_prices_today`/`net_prices_tomorrow`

**Optional Sensors:**

- **Production price sensor**: For solar production optimization
- **Indoor temperature sensor**: Override default 20°C assumption
- **Supply temperature sensor**: For real-time COP calculation
- **Heat pump thermal power sensor**: For calibration and monitoring

### Step 6: Advanced Settings (Optional)

- **Planning horizon**: Optimization time window (default: 6 hours)
  - Longer horizons provide better optimization but increase computation
- **Time base**: Time step for optimization (default: 60 minutes)
  - Must match your price forecast interval
- **Buffer efficiency**: Thermal mass storage efficiency (default: 0.5)

## Sensors Created

The integration creates the following sensors:

### Weather Sensors
- `sensor.outdoor_temperature` - Current and forecasted outdoor temperature

### Heat Calculation Sensors
- `sensor.heat_loss` - Building heat loss (kW)
- `sensor.window_solar_gain` - Solar gain through windows (kW)
- `sensor.pv_production_forecast` - PV production forecast (kW)
- `sensor.net_heat_loss` - Net heat demand after solar gain (kW)

### Optimization Sensors
- `sensor.heating_curve_offset` - **Optimal heating curve offset (°C)** ⭐
- `sensor.optimized_supply_temperature` - Optimal supply temperature (°C)
- `sensor.heat_buffer` - Thermal buffer from solar gain (kWh)
- `sensor.cost_savings` - Estimated cost savings (€)

### COP Sensors
- `sensor.heat_pump_cop` - Current heat pump COP
- `sensor.calculated_supply_temperature` - Supply temp from heating curve

### Diagnostic Sensors
- `sensor.diagnostics` - System health and status
- `binary_sensor.heat_demand` - Heat demand indicator

### Calibration Sensor
- `sensor.calibration` - Calibration recommendations

![Heating Curve Optimizer Sensors](heat%20curve%20optimizer.png)

## Using the Integration

### Basic Automation

Use the optimized offset to adjust your heating curve:

```yaml
automation:
  - alias: "Update heating curve offset"
    trigger:
      - platform: state
        entity_id: sensor.heating_curve_offset
    condition:
      - condition: template
        value_template: "{{ states('sensor.heating_curve_offset') not in ['unknown', 'unavailable'] }}"
    action:
      - service: number.set_value
        target:
          entity_id: number.heat_curve_offset
        data:
          value: "{{ states('sensor.heating_curve_offset') | float(0) }}"
```

### Advanced Automation with Safety Checks

```yaml
automation:
  - alias: "Update heating curve offset (with safety)"
    trigger:
      - platform: state
        entity_id: sensor.heating_curve_offset
    condition:
      # Only update if value is valid
      - condition: template
        value_template: "{{ states('sensor.heating_curve_offset') not in ['unknown', 'unavailable'] }}"
      # Only update if system is healthy
      - condition: state
        entity_id: sensor.diagnostics
        state: "OK"
      # Limit offset range for safety
      - condition: template
        value_template: "{{ -4 <= states('sensor.heating_curve_offset') | float(0) <= 4 }}"
    action:
      - service: number.set_value
        target:
          entity_id: number.heat_curve_offset
        data:
          value: "{{ states('sensor.heating_curve_offset') | float(0) }}"
```

### Monitoring Dashboard

Create a dashboard to monitor the integration:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Heating Optimization
    entities:
      - sensor.heating_curve_offset
      - sensor.optimized_supply_temperature
      - sensor.heat_pump_cop
      - sensor.cost_savings

  - type: entities
    title: Heat Balance
    entities:
      - sensor.heat_loss
      - sensor.window_solar_gain
      - sensor.net_heat_loss
      - sensor.heat_buffer

  - type: history-graph
    title: Offset History
    hours_to_show: 24
    entities:
      - sensor.heating_curve_offset
      - sensor.current_consumption_price
```

## Calibration and Tuning

**⚠️ Essential for proper operation**

After installation, monitor and calibrate:

1. **Check diagnostics sensor** for health status and warnings
2. **Compare predicted vs actual**:
   - Monitor `sensor.heat_loss` against actual heating power
   - Adjust `energy_label` if consistently off
3. **Verify COP calculations**:
   - Compare `sensor.heat_pump_cop` with manufacturer specs
   - Adjust `k_factor` and `cop_compensation_factor` if needed
4. **Monitor cost savings**:
   - Track `sensor.cost_savings` over weeks
   - Positive values indicate effective optimization

### Energy Label Calibration

The integration provides a calibration sensor that analyzes historical data to recommend the best energy label:

```yaml
# Use calibration sensor to find optimal energy label
sensor.calibration
```

Attributes include:
- `recommended_label`: Suggested energy label based on historical heat loss
- `measured_u_value`: Calculated U-value from actual consumption
- `confidence`: Confidence level of recommendation

## Troubleshooting

### Sensor Unavailable

**Symptom**: `sensor.heating_curve_offset` shows "unavailable"

**Possible causes**:
1. No price forecast available
   - Check that consumption price sensor has forecast attributes
   - Verify [Dynamic Energy Contract Calculator](https://github.com/bvweerd/dynamic_energy_contract_calculator) is working
2. Weather API failure
   - Check internet connectivity
   - Verify open-meteo.com is accessible
3. Invalid configuration
   - Check logs for configuration errors
   - Verify all required fields are filled

### Unexpected Offsets

**Symptom**: Offset values seem illogical

**Possible causes**:
1. Incorrect k-factor
   - Too high: optimizer will prefer low supply temps excessively
   - Too low: optimizer won't respond enough to price changes
2. Wrong COP compensation factor
   - Affects cost calculation accuracy
3. Invalid price forecast
   - Check price sensor attributes
   - Verify forecast format matches expectations

### High Energy Consumption

**Symptom**: Energy use increased after installation

**Possible causes**:
1. Incorrect energy label (too optimistic)
   - Use calibration sensor to find correct label
2. K-factor too low
   - System not reducing supply temp enough during expensive periods
3. COP compensation too high
   - Optimizer overestimates efficiency

### Enable Debug Logging

Add to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.heating_curve_optimizer: debug
```

Then restart Home Assistant and check logs for detailed operation info.

## Supported Configurations

- ✅ Air-to-water heat pumps
- ✅ Ground-source heat pumps
- ✅ Hybrid heating systems
- ✅ Dynamic electricity pricing (required for optimization)
- ✅ Fixed electricity pricing (limited optimization benefit)
- ✅ Solar production integration
- ✅ Multi-zone buildings (with appropriate configuration)

## Related Integrations

### Required for Optimal Operation

- **[Dynamic Energy Contract Calculator](https://github.com/bvweerd/dynamic_energy_contract_calculator)**
  - Provides electricity price forecasts needed for cost optimization
  - Without this, the integration can only do thermal optimization

### Recommended

- **HACS** - For easy installation and updates
- **Nordpool** or similar - For dynamic electricity prices
- **Open-Meteo** - Weather integration (optional, as this integration fetches directly)

## Documentation

**Full documentation** is available at: **[https://bvweerd.github.io/heating_curve_optimizer/](https://bvweerd.github.io/heating_curve_optimizer/)**

### Quick Links

- **[Quick Start Guide](https://bvweerd.github.io/heating_curve_optimizer/quick-start/)** - Get up and running
- **[Algorithm Explanation](https://bvweerd.github.io/heating_curve_optimizer/algorithm/overview/)** - Deep dive into optimization
- **[Example Scenarios](https://bvweerd.github.io/heating_curve_optimizer/examples/price-optimization/)** - Real-world examples
- **[Configuration Reference](https://bvweerd.github.io/heating_curve_optimizer/reference/configuration/)** - All configuration options
- **[Troubleshooting](https://bvweerd.github.io/heating_curve_optimizer/reference/troubleshooting/)** - Common issues
- **[Contributing](https://bvweerd.github.io/heating_curve_optimizer/development/contributing/)** - Help improve this project

## How to Get Help

- **Issues**: [Report bugs or request features](https://github.com/bvweerd/heating_curve_optimizer/issues)
- **Discussions**: [Ask questions or share experiences](https://github.com/bvweerd/heating_curve_optimizer/discussions)
- **Documentation**: [Full documentation site](https://bvweerd.github.io/heating_curve_optimizer/)

## Contributing

Contributions are welcome! Please see the [Contributing Guide](https://bvweerd.github.io/heating_curve_optimizer/development/contributing/) for details.

Before contributing:
1. Run tests: `pytest`
2. Run pre-commit hooks: `pre-commit run --all-files`
3. Ensure all tests pass and coverage is maintained

## Development Status

**Current Status**: Experimental / Alpha

This integration is under active development. Breaking changes may occur between versions. Always read release notes before updating.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Built with ❤️ for the Home Assistant community**

**⚠️ USE AT YOUR OWN RISK - MONITOR CLOSELY AFTER INSTALLATION ⚠️**
