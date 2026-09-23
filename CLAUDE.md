# CLAUDE.md - AI Assistant Guide for Heating Curve Optimizer

This document provides comprehensive guidance for AI assistants working on the Heating Curve Optimizer codebase.

## Table of Contents
1. [Project Overview](#project-overview)
2. [Repository Structure](#repository-structure)
3. [Key Modules and Responsibilities](#key-modules-and-responsibilities)
4. [Development Workflow](#development-workflow)
5. [Testing Requirements](#testing-requirements)
6. [Code Conventions](#code-conventions)
7. [Common Tasks](#common-tasks)
8. [Troubleshooting Guide](#troubleshooting-guide)

---

## Project Overview

**Heating Curve Optimizer** is a Home Assistant custom integration that optimizes heating system efficiency by dynamically calculating the optimal heating curve offset based on:
- Weather forecasts (temperature, solar radiation from open-meteo.com)
- Electricity prices (consumption and production)
- Heat pump COP (Coefficient of Performance)
- Building characteristics (area, energy label, thermal mass, emitter type)

### Core Functionality
The integration uses **backward-induction dynamic programming** over indoor temperature to minimize electricity costs while maintaining comfort. It considers:
- Variable electricity prices (import and feed-in)
- Heat pump efficiency variations with temperature (COP model with Carnot cap)
- Solar gain and PV surplus
- Building thermal physics (1R1C model)
- Comfort constraints (quadratic penalty outside comfort band)
- Offset ramp-rate limits (configurable via `offset_delta_t`)

### Key Equation
Heat pump COP is calculated as:
```
COP = (base_cop + alpha * T_outdoor - k * (T_supply - 35)) * f
```
Where:
- `base_cop`: Base COP at 35 deg C supply temperature
- `alpha`: Outdoor temperature coefficient (default 0.025)
- `k`: k_factor (how COP declines as supply temperature rises)
- `T_supply`: Supply temperature (deg C)
- `T_outdoor`: Outdoor temperature (deg C)
- `f`: cop_compensation_factor (adjusts theoretical COP to actual system)

COP is clamped to the Carnot limit: `COP <= (T_supply + 273.15) / (T_supply - T_outdoor)`.

---

## Repository Structure

```
/media/data/github/heating_curve_optimizer/
├── custom_components/
│   └── heating_curve_optimizer/
│       ├── __init__.py                 # Entry point, coordinator setup, subentry handling
│       ├── binary_sensor.py            # Heat demand + gas boiler preferred sensors
│       ├── building_model.py           # 1R1C thermal building model (BuildingConfig, EmitterConfig)
│       ├── calibration.py              # Thermal calibration logic (UA/thermal mass learning)
│       ├── calibration_sensor.py       # Calibration sensor entity
│       ├── climate.py                  # Climate entity (target temp control)
│       ├── companion_integrations.py   # Detection of battery_controller and other integrations
│       ├── config_flow.py              # Multi-step + sectioned UI configuration
│       ├── const.py                    # Constants, defaults, energy label mappings
│       ├── coordinator.py              # Weather, Heat, Optimization coordinators
│       ├── diagnostics.py              # Diagnostics export
│       ├── entity.py                   # Base entity class (BaseUtilitySensor)
│       ├── gas_boiler_coordinator.py   # Gas boiler cost comparison coordinator
│       ├── gas_boiler_model.py         # Gas boiler cost model
│       ├── heatpump_model.py           # Heat pump efficiency model (HeatPumpConfig)
│       ├── helpers.py                  # Helper functions (price extraction, supply temp calc)
│       ├── manifest.json               # Integration manifest
│       ├── number.py                   # Number entities (target temp, hysteresis)
│       ├── realtime_controller.py      # Real-time PV-surplus grid control
│       ├── sensor_realtime_offset.py   # Real-time offset adjustment sensor
│       ├── sensor_thermal_calibration.py # Thermal calibration sensor
│       ├── thermal_optimizer.py        # DP optimizer over indoor temperature
│       │
│       ├── sensor/                     # MODULAR SENSOR STRUCTURE
│       │   ├── __init__.py             # Sensor platform setup and entity registration
│       │   ├── event_driven.py         # Event-driven sensors (price, thermal power, COP delta)
│       │   ├── diagnostics_sensor.py   # Diagnostics sensor
│       │   │
│       │   ├── weather/                # Weather sensors
│       │   │   └── outdoor_temperature.py
│       │   │
│       │   ├── heat/                   # Heat calculation sensors
│       │   │   ├── heat_loss.py
│       │   │   ├── solar_gain.py
│       │   │   ├── pv_production.py
│       │   │   └── net_heat_loss.py
│       │   │
│       │   ├── optimization/           # Optimization sensors
│       │   │   ├── base.py             # Base class for optimization sensors
│       │   │   ├── heating_curve_offset.py
│       │   │   ├── optimized_supply_temperature.py
│       │   │   ├── heat_buffer.py
│       │   │   ├── cost_savings.py
│       │   │   └── total_cost_savings.py
│       │   │
│       │   ├── cop/                    # COP sensors
│       │   │   ├── quadratic_cop.py
│       │   │   └── calculated_supply_temperature.py
│       │   │
│       │   ├── daily_utility/          # Daily energy tracking sensors
│       │   │   ├── heat_pump_energy.py
│       │   │   └── net_heat_loss_energy.py
│       │   │
│       │   └── gas_boiler/             # Gas boiler comparison sensors
│       │       ├── base.py
│       │       ├── heat_pump_cost.py
│       │       ├── gas_cost.py
│       │       └── cost_savings.py
│       │
│       └── translations/
│           ├── en.json                 # English translations
│           └── nl.json                 # Dutch translations
│
├── tests/                              # 35 test modules
├── .github/workflows/                  # CI/CD pipelines
├── .pre-commit-config.yaml             # Pre-commit hooks
├── .bumpversion.toml                   # Version management
├── setup.cfg                           # Tool configurations
├── requirements.txt                    # Development dependencies
└── docs/                               # Documentation (MkDocs)
```

### Critical Files

| File | Lines | Purpose |
|------|-------|---------|
| `coordinator.py` | ~1480 | Weather, Heat, and Optimization coordinators |
| `config_flow.py` | ~1970 | Multi-step + sectioned UI configuration, subentry flows |
| `thermal_optimizer.py` | ~390 | Backward-induction DP over indoor temperature |
| `sensor/__init__.py` | ~630 | Sensor platform setup and entity registration |
| `sensor/event_driven.py` | ~650 | Event-driven sensors (price, thermal power, COP delta) |
| `__init__.py` | ~490 | Entry point, zone/PV/gas subentry orchestration |
| `const.py` | ~410 | Configuration keys, defaults, energy label mappings |
| `building_model.py` | ~245 | 1R1C building thermal model |
| `calibration_sensor.py` | ~780 | Thermal calibration entity |
| `calibration.py` | ~285 | Calibration state machine and persistence |

---

## Key Modules and Responsibilities

### `__init__.py` - Integration Entry Point
```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool
```
- Sets up config entry with coordinator chain
- Creates primary zone from first `ZONE_SUBENTRY_TYPE` subentry
- Sets up additional zone subentries (each gets own coordinators + device)
- Sets up PV array subentries and gas boiler subentry
- Forwards to all platforms: `sensor`, `binary_sensor`, `number`, `climate`
- Stores runtime data as a `HcoRuntimeData` dataclass in `hass.data[DOMAIN][entry_id]`

**Important**: This integration is **config-entry only** (no YAML support). Heating zones, PV arrays, and gas boiler are **config subentries**, added via the integration's device page after initial setup.

### `const.py` - Constants and Defaults
Key constants:
- `DOMAIN = "heating_curve_optimizer"`
- `PLATFORMS = ["sensor", "binary_sensor", "number", "climate"]`
- `DEFAULT_PLANNING_WINDOW = 6` (hours)
- `DEFAULT_TIME_BASE = 60` (minutes per step)
- `ZONE_SUBENTRY_TYPE = "heating_zone"`
- `PV_SUBENTRY_TYPE = "pv_array"`
- `GAS_SUBENTRY_TYPE = "gas_boiler"`
- `ENERGY_LABELS = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]`

### `thermal_optimizer.py` - Core Optimization Logic

**Key Function**: `optimize_thermal_schedule(building, heatpump, emitter, ...)`

**Algorithm**: Backward-induction dynamic programming over indoor temperature.

**State Space**: `(T_indoor, previous_offset)` - indoor temperature IS the state, not a separate buffer.

- **Offsets**: -4 to +4 deg C in 1 deg C increments
- **Indoor temperature**: discretized in 0.5 deg C steps
- **Ramp-rate limit**: `max_change = time_base // offset_delta_t`
- **Comfort**: quadratic penalty outside `[comfort_min, comfort_max]`, hard floor penalty below
- **Terminal value**: heat stored above comfort floor valued at end-of-horizon price/COP
- **PV surplus**: priced at feed-in rate (default 0.07 EUR/kWh), not free

**Key difference from old optimizer.py** (removed): The old DP used `(time_step, offset, cumulative_offset_sum)` with a separate buffer payload. The new DP uses physical indoor temperature as the state dimension, so energy conservation is enforced by construction.

**Result**: `ThermalOptimizationResult` dataclass with offsets, supply temps, indoor temps, thermal/electrical power, costs, and shadow price.

### `building_model.py` - Building Thermal Model

**1R1C model** (analogous to battery_controller's battery model):
- `BuildingConfig`: UA (W/K), thermal mass (kWh/K), comfort band
- `EmitterConfig`: radiator/underfloor/fan coil characteristics
- `next_indoor_temp()`: physics step function called thousands of times per DP run

### `heatpump_model.py` - Heat Pump Model

- `HeatPumpConfig`: base COP, k_factor, outdoor temp coefficient, compensation factor
- `cop_at(supply_temp, outdoor_temp)`: COP with Carnot cap
- Used by both thermal_optimizer.py and coordinator.py

### `coordinator.py` - Data Update Coordinators

**Three coordinators manage data updates**:

1. **`WeatherDataCoordinator`** (60-min interval)
   - Fetches weather + radiation data from open-meteo.com API
   - Provides temperature + 48h forecast and solar radiation

2. **`HeatCalculationCoordinator`** (depends on WeatherCoordinator)
   - Calculates heat loss, solar gain, net heat loss, PV production
   - Tracks indoor temperature and heat demand factor
   - Uses `BuildingConfig` for thermal calculations

3. **`OptimizationCoordinator`** (depends on HeatCoordinator)
   - Calls `optimize_thermal_schedule()` in executor thread
   - Tracks `_current_offset` for smooth transitions
   - Manages real-time controller (PV surplus offset adjustments)
   - Provides thermal calibration data collection
   - Handles buffer evolution (derived from indoor temperature trajectory)

### `gas_boiler_coordinator.py` - Gas Boiler Comparison

Optional coordinator (requires `GAS_SUBENTRY_TYPE` subentry):
- Compares heat pump electricity cost vs gas boiler cost
- Uses current outdoor/supply temperature operating point
- Provides cost savings and "gas cheaper" binary sensor

### `realtime_controller.py` - Real-time Grid Control

Optional (requires `CONF_GRID_IMPORT_SENSOR` or `CONF_GRID_EXPORT_SENSOR`):
- Adjusts offset in real-time based on grid power flow
- Uses shadow price from thermal optimizer
- Clamps effective offset within [-4, +4] bounds

### `config_flow.py` - UI Configuration

**Main entry flow** (shared/infrastructure config only):
1. **Basic Settings**: power/grid sensors (dynamic discovery)
2. **Source Selection**: consumption/production sensors
3. **Price Settings**: consumption/production price sensors
4. **Heating Curve Settings**: COP parameters, heating curve limits
5. **Finish**: Validation and entry creation

Supports both multi-step flow and modern single-page **sectioned form** (graceful degradation).

**Subentry flows** (added via integration device page after setup):
- `HeatingZoneSubentryFlow`: area, energy label, glass, ventilation, thermal mass, emitter, target temp, hysteresis, indoor sensor
- `HeatingPvArraySubentryFlow`: peak power, orientation, tilt, efficiency, DC-coupled
- `HeatingGasBoilerSubentryFlow`: gas price sensor, boiler efficiency, calorific value

**Primary zone**: The first (oldest) zone subentry drives the main device's full sensor set. Additional zones get lightweight devices. With zero zones configured, the integration loads but `heat_coordinator`/`optimization_coordinator` are `None`.

### `number.py` - Manual Control Entities

Three number entities for live temperature-setpoint control:

1. **`TargetIndoorTemperatureNumber`**: Target indoor temperature (15-25 deg C, step 0.5)
2. **`IndoorTempHysteresisLowerNumber`**: Hysteresis below target (0.1-2.0 deg C)
3. **`IndoorTempHysteresisUpperNumber`**: Hysteresis above target (0.1-2.0 deg C)

All three restore state on restart and sync to `hass.data[DOMAIN]["runtime"]`.

### `binary_sensor.py` - Binary Sensors

- **`CoordinatorHeatDemandBinarySensor`**: ON when net heat loss > 0
- **`GasBoilerPreferredBinarySensor`**: ON when gas boiler is cheaper than heat pump
- Legacy `HeatDemandBinarySensor` fallback when coordinator unavailable

### `sensor/` - Modular Sensor Structure

**Weather**: `sensor/weather/outdoor_temperature.py`
**Heat**: `sensor/heat/` - heat_loss, solar_gain, pv_production, net_heat_loss
**Optimization**: `sensor/optimization/` - offset, supply temp, buffer, cost savings, total savings
**COP**: `sensor/cop/` - quadratic COP, calculated supply temperature
**Daily utility**: `sensor/daily_utility/` - heat pump energy, net heat loss energy
**Gas boiler**: `sensor/gas_boiler/` - heat pump cost, gas cost, cost savings
**Event-driven**: `sensor/event_driven.py` - price sensor, thermal power, COP delta, heat generation delta
**Diagnostics**: `sensor/diagnostics_sensor.py`
**Standalone**: `sensor_realtime_offset.py`, `sensor_thermal_calibration.py`

---

## Development Workflow

### Prerequisites
- Python 3.13+
- Home Assistant development environment
- pytest, pytest-asyncio, pytest-homeassistant-custom-component
- pre-commit

### Setting Up Development Environment

```bash
pip install pre-commit
pre-commit install
pip install -r requirements.txt
```

### Pre-commit Hooks

**Automatically runs on commit** (`.pre-commit-config.yaml`):
1. **pyupgrade**: Upgrades Python syntax to 3.7+
2. **codespell**: Spell checking (Dutch design docs in `docs/redesign/` and `docs/algorithm/` are excluded)
3. **ruff**: Linting, auto-fix, and formatting

**Manual run**:
```bash
pre-commit run --all-files
```

### Pre-Push Checklist

```bash
# 1. Run pre-commit hooks
pre-commit run --all-files

# 2. Run tests
pytest
```

### CI/CD Pipelines

**GitHub Actions** (`.github/workflows/`):

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Push/PR | Runs test suite + pre-commit checks |
| `docs.yml` | Push to main | Deploys MkDocs documentation |
| `release.yml` | Release publish | Creates release zip |
| `bump-version.yml` | Manual | Bumps version via bumpversion |
| `link-check.yml` | Schedule | Validates documentation links |
| `stale.yml` | Schedule | Marks stale issues/PRs |
| `label-issues.yml` | Issues | Auto-labels issues |
| `label-prs.yml` | PRs | Auto-labels PRs |
| `sync-labels.yml` | Manual | Syncs GitHub labels |

### Version Management

**Bumpversion** (`.bumpversion.toml`):
```bash
# Current version: 2.0.0
bumpversion patch  # 2.0.0 -> 2.0.1
bumpversion minor  # 2.0.0 -> 2.1.0
bumpversion major  # 2.0.0 -> 3.0.0
```

Automatically updates `manifest.json` and `.bumpversion.toml`.

---

## Testing Requirements

### Test Framework
- **pytest** with asyncio support
- **pytest-cov** for coverage reporting
- **syrupy** for snapshot testing
- **pytest-homeassistant-custom-component** for HA fixtures

### Test Configuration (`setup.cfg`)
```ini
[tool:pytest]
testpaths = tests
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

### Test Structure

**35 test files** covering all modules:

| Category | Test Files |
|----------|------------|
| Core | `test_init.py`, `test_coordinator.py`, `test_thermal_optimizer.py` |
| Models | `test_building_model.py`, `test_heatpump_model.py`, `test_gas_boiler_model.py` |
| Config | `test_config_flow.py`, `test_config_flow_sectioned.py` |
| Subentries | `test_zone_subentry.py`, `test_pv_array_subentry.py`, `test_gas_boiler_subentry.py` |
| Sensors | `test_modular_sensors.py`, `test_outdoor_temperature_sensor.py`, `test_heat_pump_thermal_power_sensor.py` |
| Calibration | `test_calibration.py`, `test_calibration_sensor.py`, `test_calibration_wiring.py` |
| Gas boiler | `test_gas_boiler_coordinator.py`, `test_gas_boiler_sensors.py` |
| Real-time | `test_realtime_controller.py`, `test_realtime_wiring.py` |
| Other | `test_diagnostics.py`, `test_helpers.py`, `test_const.py`, `test_entity.py`, `test_number.py`, `test_climate.py`, `test_binary_sensor.py`, `test_companion_integrations.py`, `test_scenario_real_world_data.py` |

---

## Code Conventions

### Style Guide

- **Formatter**: ruff-format (replaces black)
- **Linter**: ruff (replaces flake8 + isort)
- **Type checking**: mypy, Python 3.13

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Sensors | `Coordinator{Purpose}Sensor` | `CoordinatorHeatLossSensor` |
| Unique IDs | `{entry_id}_{sensor_name}` | `abc123_heat_loss` |
| Private methods | Prefix with `_` | `_calculate_cop()` |
| Constants | UPPER_SNAKE_CASE | `DEFAULT_PLANNING_WINDOW` |
| Variables | snake_case | `outdoor_temp` |
| Classes | PascalCase | `BuildingConfig` |

### Logging

```python
import logging
_LOGGER = logging.getLogger(__name__)
```

Levels: `DEBUG` for data/calculations, `INFO` for init/state changes, `WARNING` for recoverable errors, `ERROR` for failures.

### Error Handling

Coordinators use `UpdateFailed` with translation support:
```python
raise _update_failed("price_sensor_unavailable", {"sensor": sensor_id})
```
This falls back to plain strings on HA versions that don't support `translation_domain`.

### Configuration Access

**Preference order**: options -> data -> default
```python
value = entry.options.get(KEY) or entry.data.get(KEY) or DEFAULT_VALUE
```

Zone-specific config is merged: `zone_config = {**config, **subentry.data}`

### Translations

**Languages supported**: English (en), Dutch (nl)

Use `strings.json` as the canonical source (HA copies to `translations/` at build time). Both `strings.json` and `translations/*.json` must stay in sync.

---

## Common Tasks

### Adding a New Sensor

1. Create file in appropriate subfolder (e.g., `sensor/heat/new_sensor.py`)
2. Import in `sensor/__init__.py`
3. Add to entities list in `async_setup_entry`
4. Add translations to `strings.json`, `translations/en.json`, and `translations/nl.json`
5. Create test file `tests/test_new_sensor.py`
6. Run `pre-commit run --all-files` and `pytest`

### Modifying the Optimization Algorithm

**Location**: `thermal_optimizer.py` (`optimize_thermal_schedule` function)

**Key considerations**:
1. **State space**: `(T_indoor, previous_offset)` - adding dimensions increases computation exponentially
2. **Building physics**: `building_model.py`'s `next_indoor_temp()` is the transition function
3. **Testing**: `tests/test_thermal_optimizer.py` includes brute-force reference tests
4. This module is pure Python (no HA dependency) for easy testing

### Adding a Configuration Option

1. Add constant to `const.py`: `CONF_NEW_OPTION = "new_option"` + `DEFAULT_NEW_OPTION = 42`
2. Add to appropriate config flow step or subentry flow in `config_flow.py`
3. Add translations to `strings.json`, `translations/en.json`, and `translations/nl.json`
4. Access via `config.get(CONF_NEW_OPTION, DEFAULT_NEW_OPTION)`

---

## Troubleshooting Guide

### 1. Sensor Shows "Unavailable"

**Common causes**:
- Dependency sensor unavailable or in `unknown` state
- API call failed (open-meteo.com timeout)
- No heating zone subentry configured yet
- Missing required config values (area, energy label)

**Debug**: Enable debug logging:
```yaml
logger:
  logs:
    custom_components.heating_curve_optimizer: debug
```

### 2. Optimization Not Running

**Causes**:
- No price forecast available (check price sensor attributes)
- Heat coordinator has no data (check weather API)
- No zone subentry configured (optimization requires a heating zone)

**Price forecast formats supported** (in order):
1. `raw_today` / `raw_tomorrow` attributes
2. `forecast_prices` attribute
3. `net_prices_today` / `net_prices_tomorrow` attributes
4. Fallback: current price only

### 3. Gas Boiler Comparison Error

"No operating point available yet" is expected at startup until the optimization coordinator has produced at least one result with outdoor/supply temperature data.

### 4. Real-time Offset Unavailable

Expected until: grid import/export sensors are configured AND reporting, at least one full optimization cycle completes, AND the real-time controller produces its first adjustment. This sensor is disabled by default (opt-in diagnostic).

### 5. Pre-commit Hooks Failing

```bash
# Auto-fix most issues
ruff check --fix custom_components/ tests/
ruff format custom_components/ tests/
```

---

## Architecture Insights

### Coordinator Dependency Chain

```
WeatherDataCoordinator (60-min, open-meteo.com API)
    |
    v
HeatCalculationCoordinator (depends on weather)
    |       Calculates: heat loss, solar gain, net heat loss, PV production
    v
OptimizationCoordinator (depends on heat + price sensor)
    |       Calls: optimize_thermal_schedule() in executor
    |       Manages: real-time controller, thermal calibration
    v
GasBoilerCoordinator (optional, depends on optimization + gas price)
```

### Subentry Architecture

Modelled on battery_controller's pattern:

- **Heating zones** (`ZONE_SUBENTRY_TYPE`): Each zone gets own `HeatCalculationCoordinator` + `OptimizationCoordinator` + device. Primary zone (first/oldest) drives the main device's full sensor set.
- **PV arrays** (`PV_SUBENTRY_TYPE`): Multiple arrays at different orientations. Data passed to coordinator as `config["pv_arrays"]` list.
- **Gas boiler** (`GAS_SUBENTRY_TYPE`): Singleton. Optional hybrid cost comparison.

### State Management

Runtime data stored as `HcoRuntimeData` dataclass:
```python
@dataclass
class HcoRuntimeData:
    weather_coordinator: WeatherDataCoordinator
    heat_coordinator: HeatCalculationCoordinator | None
    optimization_coordinator: OptimizationCoordinator | None
    zones: dict[str, dict[str, Any]]
    gas_boiler_coordinator: GasBoilerCoordinator | None
    ...
```

### Thermal Model (1R1C)

The building is modelled as a single-node RC network:
- **R** = 1/UA (thermal resistance, from energy label + ventilation)
- **C** = thermal mass (from area, thermal mass class: light/medium/heavy)
- **State**: indoor temperature
- **Inputs**: outdoor temp, solar gain, heat pump power, offset

`BuildingConfig.next_indoor_temp()` is the physics step function.

### Price Forecast Extraction

Handled by `helpers.py:extract_price_forecast()` and `extract_price_forecast_with_interval()`. Supports multiple price integration formats for maximum compatibility.

---

## Quick Reference

### File Locations
| Purpose | Location |
|---------|----------|
| Modify optimization algorithm | `thermal_optimizer.py` |
| Add/modify building physics | `building_model.py` |
| Add/modify COP model | `heatpump_model.py` |
| Add coordinator logic | `coordinator.py` |
| Add weather sensor | `sensor/weather/` |
| Add heat sensor | `sensor/heat/` |
| Add optimization sensor | `sensor/optimization/` |
| Add COP sensor | `sensor/cop/` |
| Add gas boiler sensor | `sensor/gas_boiler/` |
| Register sensor | `sensor/__init__.py` |
| Add config option | `config_flow.py` + `const.py` |
| Add translation | `strings.json` + `translations/*.json` |

### Useful Commands
```bash
pre-commit run --all-files    # Lint + format
pytest                        # Run tests
pytest tests/test_thermal_optimizer.py -v  # Run specific test
bumpversion patch             # Bump version
```

### Key Configuration Keys
```python
# Main entry (shared)
CONF_CONSUMPTION_PRICE_SENSOR   CONF_PRODUCTION_PRICE_SENSOR
CONF_K_FACTOR                   CONF_BASE_COP
CONF_COP_COMPENSATION_FACTOR    CONF_OUTDOOR_TEMP_COEFFICIENT
CONF_HEAT_CURVE_MIN/MAX         CONF_HEAT_CURVE_MIN/MAX_OUTDOOR
CONF_PLANNING_WINDOW            CONF_TIME_BASE
CONF_OFFSET_DELTA_T             CONF_POWER_CONSUMPTION
CONF_GRID_IMPORT_SENSOR         CONF_GRID_EXPORT_SENSOR

# Zone subentry
CONF_AREA_M2                    CONF_ENERGY_LABEL
CONF_GLASS_EAST/WEST/SOUTH_M2  CONF_GLASS_U_VALUE
CONF_VENTILATION_TYPE           CONF_CEILING_HEIGHT
CONF_THERMAL_MASS_CLASS         CONF_EMITTER_TYPE
CONF_TARGET_INDOOR_TEMP         CONF_INDOOR_TEMP_HYSTERESIS_LOWER/UPPER
CONF_INDOOR_TEMPERATURE_SENSOR

# PV subentry
CONF_PV_PEAK_POWER_KWP         CONF_PV_ORIENTATION
CONF_PV_TILT                    CONF_PV_EFFICIENCY_FACTOR

# Gas boiler subentry
CONF_GAS_PRICE_SENSOR           CONF_GAS_BOILER_EFFICIENCY
CONF_GAS_CALORIFIC_VALUE
```

---

## Contact and Resources

- **Repository**: https://github.com/bvweerd/heating_curve_optimizer
- **Issues**: https://github.com/bvweerd/heating_curve_optimizer/issues
- **Home Assistant Docs**: https://developers.home-assistant.io/
- **HACS**: https://hacs.xyz/

---

**Last Updated**: 2026-09-23
**Version**: 2.0.0
**Maintainer**: @bvweerd
