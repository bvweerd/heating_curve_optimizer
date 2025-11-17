# Heating Curve Optimizer

**Intelligent heating optimization for Home Assistant**

Minimize electricity costs while maximizing comfort using dynamic programming and predictive algorithms.

[![GitHub Release](https://img.shields.io/github/release/bvweerd/heating_curve_optimizer.svg?style=flat-square)](https://github.com/bvweerd/heating_curve_optimizer/releases)
[![License](https://img.shields.io/github/license/bvweerd/heating_curve_optimizer.svg?style=flat-square)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-Default-orange.svg?style=flat-square)](https://hacs.xyz)

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

Configuration is done entirely through the UI. The following options are required:

### Building Characteristics
- Home area in square meters (`area_m2`)
- Energy label from A+++ to G (`energy_label`)
- Window areas facing east, west, and south (in m²)
- Glass U-value (thermal transmittance)

### Heat Pump Parameters
- Base COP at A7/W35 conditions
- K-factor (typical range: 0.015-0.045, varies by heat pump type)
- COP compensation factor (accounts for system losses, typically 0.85-0.95)

### Sensor Configuration
- Electricity consumption price sensor
- Optional: electricity production price sensor
- Optional: multiple consumption and production sources
- Optional: custom indoor temperature sensor
- Optional: custom supply temperature sensor

For detailed configuration instructions, see the [Configuration Guide](https://bvweerd.github.io/heating_curve_optimizer/configuration/).

## Sensors

The integration creates 17 sensors to provide comprehensive insights:

| Sensor | Description |
|--------|-------------|
| `sensor.outdoor_temperature` | Outdoor temperature with 24h forecast |
| `sensor.heat_loss` | Estimated heat loss based on building characteristics |
| `sensor.window_solar_gain` | Solar gain through windows |
| `sensor.net_heat_loss` | Net heat loss after subtracting solar gain |
| `sensor.heating_curve_offset` | **Optimal offset for the next 6 hours** |
| `sensor.optimized_supply_temperature` | Optimized supply temperature forecast |
| `sensor.heat_buffer` | Thermal energy buffer from solar gain |
| `sensor.heat_pump_cop` | Current heat pump COP |
| `sensor.heat_pump_thermal_power` | Current thermal output |
| `sensor.current_consumption_price` | Current electricity consumption price |
| `sensor.current_production_price` | Current electricity production price |
| `sensor.current_net_consumption` | Net power (consumption - production) |
| `sensor.expected_energy_consumption` | Standby power usage forecast |
| `sensor.energy_price_level` | Energy availability by price level |
| `sensor.cop_efficiency_delta` | COP improvement from optimization |
| `sensor.heat_generation_delta` | Heat generation difference |
| `binary_sensor.heat_demand` | Heat demand indicator |

![Heating Curve Optimizer Sensors](heat%20curve%20optimizer.png)

The `sensor.heating_curve_offset` attributes include:
- Future offset values for the 6-hour planning horizon
- Price forecast used for optimization
- Buffer evolution
- Supply temperature forecast

For detailed sensor documentation, see the [Sensor Reference](https://bvweerd.github.io/heating_curve_optimizer/reference/sensors/).

## Automation Example

Use the optimized offset in your heating automation:

```yaml
automation:
  - alias: "Update heating curve offset"
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

## Supported Configurations

- ✅ Air-to-water heat pumps
- ✅ Ground-source heat pumps
- ✅ Hybrid heating systems
- ✅ Dynamic electricity pricing
- ✅ Fixed electricity pricing
- ✅ Solar production integration
- ✅ Multi-zone buildings (with appropriate configuration)

## Documentation

**Full documentation** is available at: **[https://bvweerd.github.io/heating_curve_optimizer/](https://bvweerd.github.io/heating_curve_optimizer/)**

### Quick Links

- **[Quick Start Guide](https://bvweerd.github.io/heating_curve_optimizer/quick-start/)** - Get up and running in minutes
- **[Heat Pump Profiles](https://bvweerd.github.io/heating_curve_optimizer/heat-pump-profiles/)** - Optimized settings for specific heat pump models
- **[Algorithm Explanation](https://bvweerd.github.io/heating_curve_optimizer/algorithm/overview/)** - Deep dive into the optimization engine
- **[Example Scenarios](https://bvweerd.github.io/heating_curve_optimizer/examples/price-optimization/)** - Real-world usage examples
- **[Configuration Reference](https://bvweerd.github.io/heating_curve_optimizer/reference/configuration/)** - Complete configuration options
- **[Troubleshooting](https://bvweerd.github.io/heating_curve_optimizer/reference/troubleshooting/)** - Common issues and solutions
- **[Contributing](https://bvweerd.github.io/heating_curve_optimizer/development/contributing/)** - Help improve the integration

## How to Get Help

- **Issues**: [Report bugs or request features](https://github.com/bvweerd/heating_curve_optimizer/issues)
- **Discussions**: [Ask questions or share experiences](https://github.com/bvweerd/heating_curve_optimizer/discussions)
- **Documentation**: [Full documentation site](https://bvweerd.github.io/heating_curve_optimizer/)

## Contributing

Contributions are welcome! Please see the [Contributing Guide](https://bvweerd.github.io/heating_curve_optimizer/development/contributing/) for details.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Built with ❤️ for the Home Assistant community**
