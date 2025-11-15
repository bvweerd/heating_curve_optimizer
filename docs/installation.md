# Installation

This guide will help you install the Heating Curve Optimizer integration in Home Assistant.

## Prerequisites

Before installing, ensure you have:

- :white_check_mark: Home Assistant 2023.1 or newer
- :white_check_mark: HACS (Home Assistant Community Store) installed
- :white_check_mark: Internet connectivity (for weather forecasts from open-meteo.com)
- :white_check_mark: Electricity price sensor (or fixed price configuration)

## Installation Methods

### Method 1: HACS (Recommended)

1. **Open HACS** in your Home Assistant instance
2. **Click** on "Integrations"
3. **Click** the three dots in the top right corner
4. **Select** "Custom repositories"
5. **Add** this repository:
   ```
   https://github.com/bvweerd/heating_curve_optimizer
   ```
6. **Select** "Integration" as the category
7. **Click** "Add"
8. **Search** for "Heating Curve Optimizer"
9. **Click** "Download"
10. **Restart** Home Assistant

### Method 2: Manual Installation

1. **Download** the latest release from [GitHub Releases](https://github.com/bvweerd/heating_curve_optimizer/releases)
2. **Extract** the zip file
3. **Copy** the `custom_components/heating_curve_optimizer` directory to your Home Assistant `custom_components` directory
4. **Restart** Home Assistant

Your directory structure should look like:
```
config/
├── custom_components/
│   └── heating_curve_optimizer/
│       ├── __init__.py
│       ├── sensor.py
│       ├── config_flow.py
│       └── ...
```

## Initial Configuration

After installation and restart:

1. **Navigate** to Settings → Devices & Services
2. **Click** "+ Add Integration"
3. **Search** for "Heating Curve Optimizer"
4. **Follow** the configuration wizard (see [Configuration Guide](configuration.md))

## Verifying Installation

After configuration, you should see 17 new sensors and 5 number entities:

### Sensors

- `sensor.heating_curve_optimizer_outdoor_temperature`
- `sensor.heating_curve_optimizer_heat_loss`
- `sensor.heating_curve_optimizer_window_solar_gain`
- `sensor.heating_curve_optimizer_net_heat_loss`
- `sensor.heating_curve_optimizer_heating_curve_offset` (main optimization output)
- And 12 more...

### Number Entities (Manual Controls)

- `number.heating_curve_optimizer_offset` - Manual offset override
- `number.heating_curve_optimizer_min_supply_temp`
- `number.heating_curve_optimizer_max_supply_temp`
- `number.heating_curve_optimizer_min_outdoor_temp`
- `number.heating_curve_optimizer_max_outdoor_temp`

!!! tip "Check Sensor States"
    After a few minutes, check that the sensors show actual values (not "unavailable"). If sensors are unavailable, check the [Troubleshooting Guide](reference/troubleshooting.md).

## Next Steps

- :material-cog: [Configure your integration](configuration.md) with accurate building parameters
- :material-play: [Quick Start Guide](quick-start.md) to begin optimization
- :material-chart-line: [View Examples](examples/price-optimization.md) to understand expected behavior

## Updating

### Via HACS

1. **Open** HACS
2. **Navigate** to Integrations
3. **Find** "Heating Curve Optimizer"
4. **Click** "Update" if available
5. **Restart** Home Assistant

### Manual Update

Follow the same steps as manual installation, replacing existing files.

!!! warning "Breaking Changes"
    Check the [Release Notes](https://github.com/bvweerd/heating_curve_optimizer/releases) for any breaking changes before updating.

## Uninstallation

To remove the integration:

1. **Navigate** to Settings → Devices & Services
2. **Find** "Heating Curve Optimizer"
3. **Click** the three dots
4. **Select** "Delete"
5. **Optionally** remove the integration files from `custom_components/`
6. **Restart** Home Assistant

---

**Next**: [Configuration Guide](configuration.md) - Learn how to configure your building parameters
