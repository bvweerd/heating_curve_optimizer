# Configuration Reference

Quick reference for all configuration parameters.

## Basic Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `area_m2` | float | 50-500 | 150 | Heated floor area (m²) |
| `energy_label` | select | A+++-G | C | Building energy efficiency rating |
| `glass_east_m2` | float | 0-50 | 5 | East-facing window area (m²) |
| `glass_west_m2` | float | 0-50 | 5 | West-facing window area (m²) |
| `glass_south_m2` | float | 0-50 | 10 | South-facing window area (m²) |
| `glass_u_value` | float | 0.5-3.0 | 1.2 | Window thermal transmittance (W/m²K) |

## Heat Pump Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `base_cop` | float | 2.0-6.0 | 3.5 | COP at A7/W35 reference condition |
| `k_factor` | float | 0.01-0.10 | 0.03 | COP degradation per °C supply temp increase |
| `cop_compensation_factor` | float | 0.5-1.2 | 0.9 | System efficiency adjustment factor |

## Sensor Configuration

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `consumption_sensor` | entity_id | Yes | Electricity consumption sensor (W or kW) |
| `production_sensor` | entity_id | No | Electricity production sensor (W or kW) |
| `consumption_price_sensor` | entity_id | No* | Electricity consumption price (€/kWh) |
| `production_price_sensor` | entity_id | No | Feed-in tariff price (€/kWh) |

\* Required for price optimization, optional for COP-only optimization

## Advanced Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `planning_window_hours` | integer | 2-24 | 6 | Optimization planning horizon (hours) |
| `time_base_minutes` | integer | 15-120 | 60 | Optimization time step (minutes) |
| `min_supply_temp` | float | 20-45 | 25 | Minimum supply temperature (°C) |
| `max_supply_temp` | float | 35-60 | 50 | Maximum supply temperature (°C) |
| `min_outdoor_temp` | float | -20-5 | -10 | Minimum outdoor temperature for heating (°C) |
| `max_outdoor_temp` | float | 5-20 | 18 | Maximum outdoor temperature for heating (°C) |

## Energy Label to U-Value Mapping

| Energy Label | U-Value (W/m²K) | Building Quality |
|--------------|-----------------|------------------|
| A+++ | 0.18 | Passive house |
| A++ | 0.25 | Excellent |
| A+ | 0.35 | Very good |
| A | 0.45 | Good |
| B | 0.60 | Above average |
| C | 0.80 | Average (default) |
| D | 1.00 | Below average |
| E | 1.40 | Poor |
| F | 1.80 | Very poor |
| G | 2.50 | Minimal |

## Configuration via YAML

Configuration is done via UI only (no YAML support for initial setup). However, you can modify via:

```yaml
# Developer Tools → Services
service: homeassistant.update_config_entry
target:
  config_entry_id: "your_entry_id"
data:
  options:
    base_cop: 4.0
    k_factor: 0.028
```

Or via UI: Settings → Devices & Services → Heating Curve Optimizer → Configure

---

For detailed explanation of each parameter, see [Configuration Guide](../configuration.md).
