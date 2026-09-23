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
| `offset_delta_t` | integer | 10-60 | 10 | Minutes per °C offset change (controls change speed) |
| `min_supply_temp` | float | 20-45 | 25 | Minimum supply temperature (°C) |
| `max_supply_temp` | float | 35-60 | 50 | Maximum supply temperature (°C) |
| `min_outdoor_temp` | float | -20-5 | -10 | Minimum outdoor temperature for heating (°C) |
| `max_outdoor_temp` | float | 5-20 | 18 | Maximum outdoor temperature for heating (°C) |

## Temperature Control Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `target_indoor_temp` | float | 15-25 | 20.0 | Target indoor temperature setpoint (°C) |
| `indoor_temp_hysteresis` | float | 0.1-2.0 | 0.5 | Hysteresis band for heat demand modulation (°C) |

## Thermal Optimizer Parameters

These control the thermal optimizer (`building_model.py` /
`heatpump_model.py` / `thermal_optimizer.py`), which drives every
optimization cycle.

| Parameter | Type | Values | Default | Description |
|-----------|------|--------|---------|-------------|
| `grid_import_sensor` | entity_id | - | none | Real household grid import power sensor (W), positive = importing. Enables the real-time PV-surplus controller (`realtime_controller.py`) when set. |
| `grid_export_sensor` | entity_id | - | none | Real household grid export power sensor (W). Used alongside `grid_import_sensor` for the same real-time controller. |
| `thermal_mass_class` | select | `light`, `medium`, `heavy` | `medium` | Building thermal mass class, used instead of the energy-label-derived estimate when set. See the table below for the underlying Wh/m²K values. |
| `emitter_type` | select | `radiator`, `underfloor`, `fan_coil` | `radiator` | Heat emitter type - controls how emitter output falls off as supply temperature drops (EN 442-style exponent). See the table below. |

!!! note "Advanced parameters in the Basic Settings step"
    `thermal_mass_class`, `emitter_type` and `grid_import_sensor`/
    `grid_export_sensor` are all asked for in the **Basic Settings** step of
    the UI setup flow and options flow (`_build_basic_schema` in
    `config_flow.py`), alongside area/energy label/glazing.

### Thermal Mass Class → Wh/m²K

| `thermal_mass_class` | Wh/m²K | Typical construction |
|-----------------------|--------|------------------------|
| `light` | 40 | Timber frame, light interior finishes |
| `medium` (default) | 90 | Standard cavity wall + concrete floor |
| `heavy` | 165 | Masonry/concrete throughout, exposed screed or floor |

### Emitter Type → EN 442 Exponent

| `emitter_type` | Exponent | Notes |
|-----------------|----------|-------|
| `radiator` (default) | 1.3 | Standard panel/column radiators |
| `underfloor` | 1.1 | Underfloor heating, flatter output curve |
| `fan_coil` | 1.0 | Fan coils / forced-air emitters, flattest curve |

Higher exponents mean output falls off faster as supply temperature drops
toward the emitter's design ΔT - this only shapes how the optimizer models
available heat output at each candidate supply temperature, it does not
change the sized nominal power itself (still derived automatically from
your building's peak loss at the heating curve's design point).

## Hybrid Gas Boiler Comparison (optional subentry)

Compares the heat pump's cost per kWh of heat delivered (electricity price
divided by COP - the same COP model the optimizer itself uses) against a
configured gas boiler's cost per kWh, and publishes an advisory
recommendation for when gas would be cheaper right now. Like every other
entity in this integration, it never actuates real hardware directly - it
only informs; a user's own automation decides whether/how to act on the
recommendation.

Configured as a genuine **singleton subentry** (Settings → Devices &
Services → Heating Curve Optimizer → Add sub-entry → Hybrid gas boiler) -
only one can exist per config entry, since a hybrid installation has exactly
one physical gas boiler.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gas_price_sensor` | entity_id | *(required)* | Gas price sensor, in €/m³. Required to enable the comparison at all. |
| `gas_boiler_efficiency` | float | `0.90` | Boiler efficiency as a plain fraction (0.90 = 90%), not a percentage. Typical Dutch HR-combi boiler at normal return temperatures; a well-tuned condensing unit at a low return temperature can reach ~1.07 (107%, HHV-based). |
| `gas_calorific_value_kwh_per_m3` | float | `9.77` | Dutch G-gas bovenwaarde (higher heating value/HHV), ~35.17 MJ/m³. **Not** the lower heating value (~8.8 kWh/m³) some appliance datasheets quote instead - using the wrong one under- or overstates gas cost by roughly 10%. |

**Cost formula**: `gas_cost_eur_per_kwh = (gas_price_eur_per_m3 / gas_calorific_value_kwh_per_m3) / gas_boiler_efficiency`

### Why the recommendation is gated on "is heat actually needed"

Comparing gas cost against heat-pump cost in isolation would be wrong: the
building can often coast on its thermal buffer (stored solar gain, or a
pre-heated indoor temperature) without any heat input at all, and that
always costs €0 - cheaper than either paid source. `prefer_gas_boiler` is
therefore only ever `True` when heat is genuinely needed *and* gas is
cheaper.

Whether heat is needed right now is read in this priority order:

1. **Real signal (preferred)**: whether the heat pump's own electricity
   meter (`power_consumption`, from the main configuration) is confirmed
   drawing more than an idle-power threshold (0.1 kW) right now - ground
   truth, not a prediction, and the single most actionable moment to
   recommend switching (the heat pump is *currently* consuming the
   potentially-overpriced electricity).
2. **Modeled fallback**: only used when no power sensor is configured (or
   it's unavailable) - the same source-agnostic heat-demand signal the
   `heat_pump_demand` binary sensor already uses (based on indoor
   temperature vs. target and hysteresis).

The binary sensor's `heat_currently_needed`/`heat_currently_needed_source`
attributes show which of the two was used, and whether the current `off`
state means "no heat needed at all" or "heat pump is still cheaper".

### Known limitations

- **Instantaneous comparison only** - no gas-price forecast (Dutch gas
  contracts, even "dynamic" ones, reprice far less often than electricity,
  so this isn't a meaningful gap in practice) and no hysteresis/duration
  smoothing built in; add a `for:` trigger in your own automation if you
  want to avoid reacting to a brief price crossing.
- **Whole-house scope, not per zone** - one gas boiler serves the whole
  house; the comparison lives on the main config entry, not on a
  per-zone subentry.
- **The DP optimizers don't yet know about the gas boiler** - `optimized_offset`
  and the cost-savings sensors are computed exactly as if the gas boiler
  didn't exist. A future phase could layer a per-step cheaper-source
  choice on top of the existing plan (gas cost doesn't depend on curve
  offset, so this wouldn't need a new search dimension) - not implemented
  yet.

### Offset Change Speed (offset_delta_t) Explained

The `offset_delta_t` parameter controls how quickly the heating curve offset can change:

- **10 min/°C** (default): Fast changes, max 6°C per hour (at 60-min time base)
- **30 min/°C**: Moderate changes, max 2°C per hour
- **60 min/°C**: Slow changes, max 1°C per hour

**Formula**: `max_offset_change = time_base_minutes / offset_delta_t`

**Use cases**:
- **Lower values (10-20)**: Responsive to price changes, good for volatile markets
- **Higher values (30-60)**: Smoother operation, reduces system stress

### Heat Demand Modulation

When `target_indoor_temp` and `indoor_temp_hysteresis` are configured with an indoor temperature sensor, heat demand is automatically modulated:

| Indoor Temp Position | Heat Demand Factor |
|---------------------|-------------------|
| Below (target - hysteresis) | > 1.0 (increased proportionally) |
| Within hysteresis band | 0.0 to 1.0 (linear interpolation) |
| Above (target + hysteresis) | 0.0 (no heat demand) |

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
