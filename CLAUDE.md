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
- Building characteristics (area, energy label, windows)

### Core Functionality
The integration uses **dynamic programming** to minimize electricity costs while meeting heat demand over a 6-hour planning horizon. It considers:
- Variable electricity prices
- Heat pump efficiency variations with temperature
- Solar gain buffering (negative heat demand creates thermal buffer)
- Physical constraints (temperature limits, rate of change)

### Key Equation
Heat pump COP is calculated as:
```
COP = (base_cop + α × T_outdoor - k × (T_supply - 35)) × f
```
Where:
- `base_cop`: Base COP at 35°C supply temperature
- `α`: Outdoor temperature coefficient (0.025)
- `k`: k_factor (how COP declines as supply temperature rises)
- `T_supply`: Supply temperature (°C)
- `T_outdoor`: Outdoor temperature (°C)
- `f`: cop_compensation_factor (adjusts theoretical COP to actual system)

---

## Repository Structure

```
/media/data/github/heating_curve_optimizer/
├── custom_components/
│   └── heating_curve_optimizer/
│       ├── __init__.py                 # Integration entry point, coordinator setup
│       ├── binary_sensor.py            # Heat demand binary sensor
│       ├── calibration_sensor.py       # Calibration sensor
│       ├── config_flow.py              # UI configuration (1162 lines)
│       ├── const.py                    # Constants and defaults
│       ├── coordinator.py              # Data coordinators (Weather, Heat, Optimization)
│       ├── diagnostics.py              # Diagnostics export
│       ├── entity.py                   # Base entity class
│       ├── helpers.py                  # Helper functions
│       ├── manifest.json               # Integration manifest
│       ├── optimizer.py                # Optimization algorithm (dynamic programming)
│       │
│       ├── sensor/                     # MODULAR SENSOR STRUCTURE
│       │   ├── __init__.py             # Sensor platform setup
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
│       │   │   ├── heating_curve_offset.py  # Core optimizer sensor
│       │   │   ├── optimized_supply_temperature.py
│       │   │   └── heat_buffer.py
│       │   │
│       │   ├── cop/                    # COP sensors
│       │   │   ├── quadratic_cop.py
│       │   │   └── calculated_supply_temperature.py
│       │   │
│       │   └── diagnostics_sensor.py   # Diagnostics sensor
│       │
│       └── translations/
│           ├── en.json                 # English translations
│           └── nl.json                 # Dutch translations
│
├── tests/                              # 18 test modules
├── .github/workflows/                  # CI/CD pipelines
├── .pre-commit-config.yaml             # Pre-commit hooks
├── .bumpversion.toml                   # Version management
├── setup.cfg                           # Tool configurations
├── requirements.txt                    # Development dependencies
└── README.md                           # User documentation
```

### Critical Files

| File | Lines | Purpose |
|------|-------|---------|
| `coordinator.py` | 689 | Data update coordinators for weather, heat, and optimization |
| `optimizer.py` | ~300 | Dynamic programming optimization algorithm |
| `config_flow.py` | 1162 | Multi-step UI configuration flow |
| `sensor/__init__.py` | ~180 | Sensor platform setup and entity registration |
| `const.py` | 300+ | Configuration keys, defaults, energy label mappings |

### Modular Sensor Architecture (NEW)

**All sensors are now organized by function in separate files:**

- **Weather**: `sensor/weather/outdoor_temperature.py` (~60 lines)
- **Heat**: `sensor/heat/` (4 files, ~100 lines each)
- **Optimization**: `sensor/optimization/` (3 files, ~80 lines each)
- **COP**: `sensor/cop/` (2 files, ~90 lines each)
- **Diagnostics**: `sensor/diagnostics_sensor.py` (~100 lines)

**Benefits**:
- ✅ Easy to find and modify specific sensors
- ✅ Better testing isolation
- ✅ Reduced merge conflicts
- ✅ Improved IDE performance
- ✅ Clear separation of concerns

---

## Key Modules and Responsibilities

### `__init__.py` - Integration Entry Point
```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool
```
- Sets up config entry
- Initializes `hass.data[DOMAIN]` with runtime state
- Forwards setup to all platforms (sensor, number, binary_sensor)
- Registers update listener for config changes

**Important**: This integration is **config-entry only** (no YAML support).

### `const.py` - Constants and Defaults
Key constants to know:
- `DOMAIN = "heating_curve_optimizer"`
- `PLATFORMS = ["sensor", "number", "binary_sensor"]`
- `DEFAULT_PLANNING_WINDOW_HOURS = 6`
- `DEFAULT_TIME_BASE_MINUTES = 60`
- `U_VALUE_MAP`: Energy labels (A+++ to G) → U-values (0.18 to 2.5)
- `ENERGY_LABELS = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]`

### `entity.py` - Base Entity Class
```python
class BaseUtilitySensor(SensorEntity, RestoreEntity)
```
All sensors inherit from this base class which provides:
- State restoration from previous sessions
- Availability management with logging
- Value rounding and validation
- Unavailability messages (Dutch language)

### `coordinator.py` - Data Update Coordinators

**Three coordinators manage data updates**:

1. **`WeatherDataCoordinator`** (60-min interval)
   - Fetches weather data from open-meteo.com API
   - Provides current temperature + 24h forecast
   - Provides solar radiation data

2. **`HeatCalculationCoordinator`** (depends on WeatherCoordinator)
   - Calculates heat loss: `Q_loss = HTC × ΔT`
   - Calculates solar gain through windows
   - Calculates net heat loss (heat loss - solar gain)
   - Provides PV production forecast

3. **`OptimizationCoordinator`** (depends on HeatCoordinator)
   - Runs dynamic programming optimization
   - Calculates optimal heating curve offsets
   - Tracks heat buffer evolution
   - Provides cost savings analysis

**Benefits of coordinator pattern**:
- Efficient API calls (single fetch for multiple sensors)
- Automatic update propagation to all sensors
- Centralized error handling

### `optimizer.py` - Core Optimization Logic

**Location**: `custom_components/heating_curve_optimizer/optimizer.py`

**Key Function**: `optimize_offsets(demand_forecast, price_forecast, ...)`

**Algorithm**: Dynamic Programming
- **State Space**: `(time_step, offset, cumulative_offset_sum)`
- **Offsets**: -4°C to +4°C in 1°C increments
- **Constraints**:
  - Maximum offset change: ±1°C per time step
  - Supply temperature must stay within min/max bounds
  - Buffer cannot go negative (heat debt must be repaid)
- **Objective**: Minimize total electricity cost while meeting heat demand
- **Output**: Optimal offset sequence and buffer evolution

**Key Data Structures**:
```python
# Dynamic programming table
dp = {}  # (step, offset, cumulative_offset_sum) → (cost, parent_state, buffer)
```

### `sensor/` - Modular Sensor Structure (NEW)

**All sensors organized by category**:

#### Weather Sensors (`sensor/weather/`)
- **`CoordinatorOutdoorTemperatureSensor`** - Current temp + forecast from weather coordinator

#### Heat Calculation Sensors (`sensor/heat/`)
- **`CoordinatorHeatLossSensor`** - `Q_loss = HTC × ΔT` with detailed HTC breakdown
- **`CoordinatorWindowSolarGainSensor`** - Solar gain through windows
- **`CoordinatorPVProductionForecastSensor`** - PV production forecast
- **`CoordinatorNetHeatLossSensor`** - `heat_loss - solar_gain` (can be negative)

#### Optimization Sensors (`sensor/optimization/`)
- **`CoordinatorHeatingCurveOffsetSensor`** - **CORE OPTIMIZER** - optimal offset
- **`CoordinatorOptimizedSupplyTemperatureSensor`** - Optimized supply temperature
- **`CoordinatorHeatBufferSensor`** - Thermal buffer tracking

#### COP Sensors (`sensor/cop/`)
- **`CoordinatorQuadraticCopSensor`** - Heat pump COP calculation
- **`CoordinatorCalculatedSupplyTemperatureSensor`** - Supply temp based on heating curve

#### Diagnostics
- **`CoordinatorDiagnosticsSensor`** - Complete system diagnostics

**How to add a new sensor**:
1. Create file in appropriate subfolder (e.g., `sensor/heat/new_sensor.py`)
2. Import in `sensor/__init__.py`
3. Add to entities list in `async_setup_entry`
4. Add translations to `en.json` and `nl.json`

### `config_flow.py` - UI Configuration

**Multi-Step Flow**:
1. **Basic Settings**: area, energy label, glass properties, COP parameters
2. **Source Selection**: consumption/production sensors (dynamic discovery)
3. **Price Settings**: consumption/production price sensors
4. **Finish**: Validation and entry creation

**Features**:
- Dynamic sensor discovery by device class
- Validation at each step
- Options flow for updating configuration
- Defaults from `const.py`

### `number.py` - Manual Control Entities

Five number entities for manual override:
1. **`HeatingCurveOffsetNumber`**: Manual offset (-4 to +4°C)
2. **`HeatCurveMinNumber`**: Min supply temp (20-45°C)
3. **`HeatCurveMaxNumber`**: Max supply temp (35-60°C)
4. **`HeatCurveMinOutdoorNumber`**: Min outdoor temp (-20 to 5°C)
5. **`HeatCurveMaxOutdoorNumber`**: Max outdoor temp (5 to 20°C)

All entities:
- Restore state on restart
- Sync to `hass.data[DOMAIN]["runtime"]`
- Trigger sensor recalculation on change

### `binary_sensor.py` - Heat Demand Sensor

**`HeatDemandBinarySensor`**:
- State: `ON` when net heat loss > 0, `OFF` otherwise
- Device class: `HEAT`
- Used for automations requiring heat/no-heat logic

---

## Development Workflow

### Prerequisites
- Python 3.12
- Home Assistant development environment
- pytest, pytest-asyncio, pytest-homeassistant-custom-component
- pre-commit

### Setting Up Development Environment

```bash
# Install dependencies (for local development)
pip install pre-commit

# Install pre-commit hooks
pre-commit install

# IMPORTANT: Before pushing, always run
pre-commit run --all-files

# Install test dependencies
pip install -r requirements.txt

# Run tests
pytest
```

### Pre-commit Hooks

**Automatically runs on commit** (`.pre-commit-config.yaml`):
1. **pyupgrade**: Upgrades Python syntax to 3.7+
2. **black**: Code formatting (line length 88, safe mode)
3. **codespell**: Spell checking
4. **ruff**: Linting and auto-fix

**Manual run**:
```bash
pre-commit run --all-files
```

### Pre-Push Checklist

**IMPORTANT: Always run these commands before pushing code:**

```bash
# 1. Run pre-commit hooks to check code quality
pre-commit run --all-files

# 2. Run pytest to ensure all tests pass
pytest
```

**Why this workflow?**
- **Pre-commit hooks**: Run locally and fix formatting/linting issues immediately
- **Pytest**: Run locally to ensure all tests pass before pushing
- This ensures code quality and prevents breaking changes

**If pre-commit hooks fail:**
1. Review the errors
2. Fix the issues (many are auto-fixed by the hooks)
3. Stage the fixed files: `git add .`
4. Re-run: `pre-commit run --all-files`
5. Repeat until all hooks pass

### CI/CD Pipelines

**GitHub Actions** (`.github/workflows/`):

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `pytest.yml` | Push/PR | Runs test suite |
| `precommit.yml` | Push/PR | Code quality checks, auto-commits fixes |
| `hacs.yml` | Daily | HACS validation |
| `hassfest.yml` | Daily | Home Assistant manifest validation |
| `release.yml` | Release publish | Creates release zip |

**Important**: The `precommit.yml` workflow auto-commits fixes with message "chore: apply pre-commit fixes" using github-actions bot.

### Version Management

**Bumpversion** (`.bumpversion.toml`):
```bash
# Current version: 1.0.2
# Bump patch: 1.0.2 → 1.0.3
bumpversion patch

# Bump minor: 1.0.2 → 1.1.0
bumpversion minor

# Bump major: 1.0.2 → 2.0.0
bumpversion major
```

Automatically updates:
- `manifest.json` version field
- `.bumpversion.toml` current_version

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
addopts = --disable-warnings --maxfail=1 -q -p syrupy --strict --cov=tests
```

### Test Structure

**One test file per module** (18 test files):
- `test_outdoor_temperature_sensor.py`
- `test_heat_loss_sensor.py`
- `test_net_heat_loss_sensor.py`
- `test_heating_curve_offset_sensor.py` ← **Core optimization tests**
- `test_quadratic_cop_sensor.py`
- ... and 13 more

### Example Test Pattern
```python
import pytest
from unittest.mock import patch, MagicMock
from custom_components.heating_curve_optimizer.sensor import HeatLossSensor

@pytest.mark.asyncio
async def test_heat_loss_sensor(hass):
    """Test heat loss calculation."""
    # Setup mock config entry
    entry = MockConfigEntry(
        domain="heating_curve_optimizer",
        data={
            "area_m2": 150,
            "energy_label": "C",
        }
    )

    # Setup outdoor temperature sensor
    hass.states.async_set("sensor.outdoor_temperature", "5.0")
    hass.states.async_set("sensor.indoor_temperature", "20.0")

    # Create and update sensor
    sensor = HeatLossSensor(hass, entry)
    await sensor.async_update()

    # Assert results
    assert sensor.state is not None
    assert sensor.state > 0  # Positive heat loss
```

### Critical Tests to Maintain

**Optimization Tests** (`test_heating_curve_offset_sensor.py`):
- Offset optimization with varying prices
- Buffer evolution tracking
- Constraint validation (offset limits, temperature bounds)
- Edge cases (all high prices, all low prices, negative demand)

**When adding new features**:
1. Write tests first (TDD approach)
2. Ensure coverage of edge cases
3. Use mocking for external dependencies (API calls)
4. Run full test suite before committing

---

## Code Conventions

### Style Guide

**Tool Configurations** (`setup.cfg`):
- **Black**: Line length 88, safe mode
- **Flake8**: Ignores E501 (line too long), W503 (line break before operator), E203 (whitespace before ':')
- **isort**: Black-compatible profile
- **mypy**: Strict type checking, Python 3.12

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Sensors | `{Purpose}Sensor` | `HeatLossSensor` |
| Unique IDs | `{entry_id}_{sensor_name}` | `abc123_heat_loss` |
| Private methods | Prefix with `_` | `_optimize_offsets()` |
| Constants | UPPER_SNAKE_CASE | `DEFAULT_PLANNING_WINDOW_HOURS` |
| Variables | snake_case | `outdoor_temp` |
| Classes | PascalCase | `HeatingCurveOffsetSensor` |

### Type Hints

**Always use type hints**:
```python
from typing import Any, Dict, List, Optional
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry
) -> bool:
    """Set up from a config entry."""
    ...
```

### Logging

**Module-level logger**:
```python
import logging
_LOGGER = logging.getLogger(__name__)
```

**Logging levels**:
- `DEBUG`: Data values, API responses, calculations
- `INFO`: Initialization, state changes
- `WARNING`: Recoverable errors, missing optional data
- `ERROR`: Unrecoverable errors, API failures

**Example**:
```python
_LOGGER.debug("Calculated heat loss: %s kW", heat_loss)
_LOGGER.warning("No price forecast available, using current price")
_LOGGER.error("Failed to fetch weather data: %s", err)
```

### Error Handling

**Pattern for sensor updates**:
```python
async def async_update(self) -> None:
    """Update the sensor state."""
    try:
        # Fetch data
        data = await self._fetch_data()

        # Validate
        if data is None:
            self._attr_available = False
            _LOGGER.warning("No data available")
            return

        # Calculate
        self._attr_native_value = self._calculate(data)
        self._attr_available = True

    except Exception as err:
        _LOGGER.error("Update failed: %s", err)
        self._attr_available = False
```

**State validation**:
```python
# Check for unavailable states
if state.state in ("unknown", "unavailable"):
    _LOGGER.warning("Sensor %s unavailable", entity_id)
    return None

# Type coercion with fallback
try:
    value = float(state.state)
except (ValueError, TypeError):
    _LOGGER.warning("Invalid value: %s", state.state)
    return None
```

### Configuration Access

**Preference order**: options → data → default
```python
# Get value from config entry
value = entry.options.get(KEY) or entry.data.get(KEY) or DEFAULT_VALUE

# Store in hass.data
hass.data.setdefault(DOMAIN, {})
hass.data[DOMAIN][entry.entry_id] = entry.data
hass.data[DOMAIN]["runtime"] = {}  # Runtime state shared by all entities
```

### Async Patterns

**Use async/await consistently**:
```python
# Fetch with timeout
async with async_timeout.timeout(10):
    response = await session.get(url)
    data = await response.json()

# Run CPU-intensive in executor
result = await hass.async_add_executor_job(
    self._calculate_statistics, data
)

# Track state changes
self._unsub = async_track_state_change_event(
    hass, [entity_id], self._handle_state_change
)
```

### Translations

**Use translation keys** (not hardcoded strings):
```python
# In sensor
@property
def translation_key(self) -> str:
    return "heat_loss"

# In translations/en.json
{
  "entity": {
    "sensor": {
      "heat_loss": {
        "name": "Heat Loss"
      }
    }
  }
}
```

**Languages supported**: English (en), Dutch (nl)

---

## Common Tasks

### Adding a New Sensor (NEW MODULAR STRUCTURE)

**Example: Adding a new heat calculation sensor**

1. **Create sensor file** in appropriate subfolder (e.g., `sensor/heat/new_heat_sensor.py`):
```python
"""New heat sensor description."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...entity import BaseUtilitySensor


class CoordinatorNewHeatSensor(CoordinatorEntity, BaseUtilitySensor):
    """New heat sensor using heat calculation coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return sensor value."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("new_heat_value")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )
```

2. **Import in `sensor/__init__.py`**:
```python
from .heat.new_heat_sensor import CoordinatorNewHeatSensor
```

3. **Add to entities list** in `sensor/__init__.py` → `async_setup_entry`:
```python
entities.append(
    CoordinatorNewHeatSensor(
        coordinator=heat_coordinator,
        name="New Heat Sensor",
        unique_id=f"{entry.entry_id}_new_heat_sensor",
        icon="mdi:fire",
        device=device,
    )
)
```

4. **Add translations** to `en.json` and `nl.json`:
```json
{
  "entity": {
    "sensor": {
      "new_heat_sensor": {
        "name": "New Heat Sensor"
      }
    }
  }
}
```

5. **Create test file** `tests/test_new_heat_sensor.py`:
```python
@pytest.mark.asyncio
async def test_new_heat_sensor(hass):
    """Test new heat sensor."""
    # Test implementation
```

6. **Run tests and pre-commit**:
```bash
pytest tests/test_new_heat_sensor.py -v
pre-commit run --all-files
```

### Adding a Configuration Option

1. **Add constant** to `const.py`:
```python
CONF_NEW_OPTION = "new_option"
DEFAULT_NEW_OPTION = 42
```

2. **Add to config flow** in `config_flow.py`:
```python
# In _show_basic_settings_form
schema = vol.Schema({
    ...
    vol.Optional(CONF_NEW_OPTION, default=DEFAULT_NEW_OPTION): vol.Coerce(int),
})
```

3. **Add translations** to `en.json` and `nl.json`:
```json
{
  "config": {
    "step": {
      "basic_settings": {
        "data": {
          "new_option": "New Option"
        },
        "data_description": {
          "new_option": "Description of new option"
        }
      }
    }
  }
}
```

4. **Access in sensor**:
```python
new_option = self._entry.options.get(CONF_NEW_OPTION) or \
             self._entry.data.get(CONF_NEW_OPTION) or \
             DEFAULT_NEW_OPTION
```

### Modifying the Optimization Algorithm

**Location**: `sensor.py:1719-1836` (`_optimize_offsets` method)

**Key considerations**:
1. **State space**: Currently `(time_step, offset, cumulative_offset_sum)`
   - Adding dimensions increases computation exponentially
2. **Constraints**: Ensure all constraints are validated
3. **Buffer management**: Track buffer evolution to prevent negative values
4. **Testing**: Add tests for new edge cases

**Example modification** (adding comfort constraint):
```python
# In _optimize_offsets method
for next_offset in range(max(-4, offset - 1), min(5, offset + 2)):
    # Existing constraints
    ...

    # NEW: Comfort constraint - avoid too-cold supply temps during occupied hours
    if hour in occupied_hours and supply_temp < comfort_min_temp:
        continue  # Skip this state

    # Rest of algorithm
    ...
```

### Debugging Sensor Issues

1. **Enable debug logging** in Home Assistant `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.heating_curve_optimizer: debug
```

2. **Check diagnostics**:
   - Go to Settings → Devices & Services → Heating Curve Optimizer
   - Click "Download Diagnostics"
   - Review JSON output for sensor states and attributes

3. **Use DiagnosticsSensor**:
```python
# Access diagnostic data programmatically
diagnostic_sensor = hass.states.get("sensor.heating_curve_optimizer_diagnostics")
diagnostics = diagnostic_sensor.attributes
```

4. **Common issues**:
   - **Sensor unavailable**: Check dependency sensors are available
   - **Wrong values**: Verify unit conversions (W vs kW, °C vs K)
   - **No optimization**: Check price forecast extraction
   - **API failures**: Verify internet connectivity, check open-meteo.com status

---

## Troubleshooting Guide

### Common Issues

#### 1. Sensor Shows "Unavailable"

**Causes**:
- Dependency sensor unavailable
- API call failed (open-meteo.com)
- Invalid state from source sensor
- Configuration missing required values

**Debug**:
```python
# Check logs for specific unavailability message
_LOGGER.debug("OutdoorTemperatureSensor not beschikbaar: %s", reason)

# Verify dependencies
outdoor_temp = hass.states.get("sensor.outdoor_temperature")
if outdoor_temp is None or outdoor_temp.state in ("unknown", "unavailable"):
    # Dependency unavailable
```

**Fix**:
- Verify all required sensors exist and have valid states
- Check Home Assistant internet connectivity
- Review configuration for missing required fields

#### 2. Optimization Not Running

**Causes**:
- No price forecast available
- Net heat loss sensor unavailable
- Invalid forecast format

**Debug**:
```python
# Check price forecast extraction
_LOGGER.debug("Price forecast: %s", price_forecast)
_LOGGER.debug("Demand forecast: %s", demand_forecast)

# Verify forecast lengths match
if len(price_forecast) != len(demand_forecast):
    _LOGGER.warning("Forecast length mismatch")
```

**Fix**:
- Ensure price sensor has forecast attributes (raw_today/raw_tomorrow, forecast_prices, or net_prices_today/tomorrow)
- Verify net heat loss sensor updates correctly
- Check time_base configuration matches forecast intervals

#### 3. COP Values Seem Wrong

**Causes**:
- Incorrect k_factor for heat pump type
- Wrong cop_compensation_factor
- Supply temperature sensor misconfigured

**Debug**:
```python
# Log COP calculation components
_LOGGER.debug("COP calculation: base=%s, outdoor=%s, supply=%s, k=%s, comp=%s",
              base_cop, outdoor_temp, supply_temp, k_factor, cop_compensation)
```

**Fix**:
- Adjust k_factor (typical range: 0.01-0.05)
- Calibrate cop_compensation_factor against actual measurements
- Verify supply temperature sensor accuracy

#### 4. Tests Failing

**Common test issues**:
```bash
# Mock not working
# Solution: Ensure mocks are patched at correct import path
@patch('custom_components.heating_curve_optimizer.sensor.aiohttp.ClientSession')

# Async test not recognized
# Solution: Add @pytest.mark.asyncio decorator

# Snapshot mismatch
# Solution: Update snapshots if change is intentional
pytest --snapshot-update
```

#### 5. Pre-commit Hooks Failing

**Black formatting**:
```bash
# Run black manually
black custom_components/ tests/

# Check what would change
black --check custom_components/ tests/
```

**Ruff linting**:
```bash
# Auto-fix issues
ruff check --fix custom_components/ tests/

# Show all issues
ruff check custom_components/ tests/
```

**Codespell**:
```bash
# Add word to ignore list in .pre-commit-config.yaml
- repo: https://github.com/codespell-project/codespell
  hooks:
    - id: codespell
      args: [--ignore-words-list=hass,additional,some]
```

### Performance Issues

#### 1. Slow Updates

**Causes**:
- API calls blocking main thread
- CPU-intensive calculations on event loop
- Too frequent updates

**Solutions**:
```python
# Use executor for CPU-intensive work
result = await hass.async_add_executor_job(
    self._expensive_calculation, data
)

# Add timeout to API calls
async with async_timeout.timeout(10):
    response = await session.get(url)

# Reduce update frequency
SCAN_INTERVAL = timedelta(minutes=5)
```

#### 2. High Memory Usage

**Causes**:
- Large forecast histories stored in attributes
- Circular references preventing garbage collection
- Unsubscribed event listeners

**Solutions**:
```python
# Limit attribute sizes
self._attr_extra_state_attributes = {
    "forecast": forecast[-24:],  # Only last 24 hours
}

# Properly unsubscribe
async def async_will_remove_from_hass(self) -> None:
    """Cleanup."""
    if self._unsub:
        self._unsub()

# Avoid circular references
# Use weakref or store entity_id instead of entity object
```

---

## Architecture Insights for AI Assistants

### Sensor Dependency Graph

```
OutdoorTemperatureSensor (fetches from API)
    ↓
HeatLossSensor
    ↓
NetHeatLossSensor ← WindowSolarGainSensor
    ↓
HeatingCurveOffsetSensor ← CurrentElectricityPriceSensor
    ↓
OptimizedSupplyTemperatureSensor
```

**Important**: When modifying sensors, consider downstream dependencies. Changes to `OutdoorTemperatureSensor` affect all dependent sensors.

### State Management

**Two storage locations**:
1. **`hass.data[DOMAIN][entry_id]`**: Configuration data (immutable during runtime)
2. **`hass.data[DOMAIN]["runtime"]`**: Runtime state (mutable, shared by all entities)

**Runtime state includes**:
- Manual offset overrides
- Min/max temperature limits
- Heating curve parameters

**Why?**: Number entities and sensors need to share state for manual overrides to work.

### Time Base and Resampling

**Key concept**: All forecasts are resampled to `time_base` (default 60 minutes).

**Method**: `_resample_forecast(forecast, time_base)`
- Handles different source intervals (5 min, 15 min, 30 min, 60 min)
- Uses averaging for downsampling, interpolation for upsampling
- Logs warnings when time base doesn't match

**Why?**: Different data sources (prices, weather, production) may have different intervals. Resampling ensures alignment for optimization.

### Buffer System

**Solar buffer** tracks excess heat from solar gain:
```python
if demand < 0:  # Solar gain exceeds heat loss
    buffer += abs(demand)  # Store excess in buffer
else:
    if buffer > 0:
        buffer -= min(buffer, demand)  # Use buffer to meet demand
        demand -= min(buffer, demand)
```

**Why?**: Allows optimization to reduce heating when solar buffer is available, improving cost efficiency.

### Price Forecast Extraction

**Three supported formats** (in order of preference):
1. **`raw_today` / `raw_tomorrow`** attributes
2. **`forecast_prices`** attribute
3. **`net_prices_today` / `net_prices_tomorrow`** attributes

**Fallback**: Current price if no forecast available

**Why?**: Different price integrations use different attribute names. Supporting multiple formats improves compatibility.

### External API Integration

**open-meteo.com** endpoints:
- **Weather**: `https://api.open-meteo.com/v1/forecast?latitude=X&longitude=Y&hourly=temperature_2m&forecast_days=2`
- **Radiation**: `https://api.open-meteo.com/v1/forecast?latitude=X&longitude=Y&hourly=shortwave_radiation&forecast_days=2`

**Caching**:
- Radiation history cached to reduce API calls
- 10-second timeout for resilience
- Graceful degradation on failure (sensor becomes unavailable)

**Rate limits**: None (free tier), but respectful caching implemented

---

## Best Practices for AI Assistants

### When Adding Features

1. **Check existing patterns**: Review similar sensors before implementing
2. **Follow dependency order**: Ensure dependencies are available before use
3. **Add comprehensive tests**: Test normal cases, edge cases, and error cases
4. **Update translations**: Both en.json and nl.json
5. **Document in docstrings**: Explain complex logic
6. **Consider performance**: Use executor for CPU-intensive work
7. **Validate inputs**: Check for None, "unknown", "unavailable"
8. **Log appropriately**: Debug for data, Warning for recoverable errors, Error for failures
9. **Run pre-commit hooks**: Always run `pre-commit run --all-files` before pushing

### When Fixing Bugs

1. **Reproduce first**: Write a failing test that demonstrates the bug
2. **Check logs**: Review debug logs for root cause
3. **Consider side effects**: Changes may affect dependent sensors
4. **Test edge cases**: None values, unavailable states, API failures
5. **Update tests**: Ensure test coverage includes the fix

### When Refactoring

1. **Run tests first**: Ensure all tests pass before starting
2. **Refactor incrementally**: Small changes, test after each
3. **Maintain API compatibility**: Don't break existing config entries
4. **Update documentation**: Keep CLAUDE.md in sync
5. **Check performance**: Profile if changing hot paths (optimization loop)

### Code Review Checklist

- [ ] Type hints on all functions
- [ ] Docstrings on public methods
- [ ] Error handling with try/except
- [ ] Logging at appropriate levels
- [ ] Tests added/updated
- [ ] Translations added (en.json, nl.json)
- [ ] Pre-commit hooks pass
- [ ] No hardcoded values (use const.py)
- [ ] Async/await used correctly
- [ ] State restoration implemented (if stateful)

---

## Quick Reference

### File Locations (NEW MODULAR STRUCTURE)
| Purpose | Location |
|---------|----------|
| Add weather sensor | `sensor/weather/new_sensor.py` |
| Add heat sensor | `sensor/heat/new_sensor.py` |
| Add optimization sensor | `sensor/optimization/new_sensor.py` |
| Add COP sensor | `sensor/cop/new_sensor.py` |
| Register sensor | `sensor/__init__.py` (import + add to entities list) |
| Add coordinator logic | `coordinator.py` (WeatherDataCoordinator, HeatCalculationCoordinator, OptimizationCoordinator) |
| Modify optimization algorithm | `optimizer.py` (optimize_offsets function) |
| Add config option | `config_flow.py` + `const.py` |
| Add constant | `const.py` |
| Add translation | `translations/*.json` |
| Add test | `tests/test_*.py` |

### Useful Commands
```bash
# IMPORTANT: Always run before pushing
pre-commit run --all-files

# Tests run automatically in CI (complex local setup required)
# See .github/workflows/pytest.yml for CI test configuration

# Manual formatting (pre-commit handles this automatically)
black custom_components/ tests/

# Manual linting (pre-commit handles this automatically)
ruff check --fix custom_components/ tests/

# Bump version
bumpversion patch  # 1.0.2 → 1.0.3
```

### Key Configuration Keys
```python
# From const.py
CONF_AREA_M2 = "area_m2"
CONF_ENERGY_LABEL = "energy_label"
CONF_K_FACTOR = "k_factor"
CONF_COP_COMPENSATION_FACTOR = "cop_compensation_factor"
CONF_GLASS_EAST_M2 = "glass_east_m2"
CONF_GLASS_WEST_M2 = "glass_west_m2"
CONF_GLASS_SOUTH_M2 = "glass_south_m2"
CONF_GLASS_U_VALUE = "glass_u_value"
```

### Critical Sensor Methods
```python
# Update sensor state
async def async_update(self) -> None

# Restore previous state
async def async_added_to_hass(self) -> None

# Cleanup on removal
async def async_will_remove_from_hass(self) -> None

# Define unique identifier
@property
def unique_id(self) -> str

# Define translation key
@property
def translation_key(self) -> str
```

---

## Contact and Resources

- **Repository**: https://github.com/bvweerd/heating_curve_optimizer
- **Issues**: https://github.com/bvweerd/heating_curve_optimizer/issues
- **Home Assistant Docs**: https://developers.home-assistant.io/
- **HACS**: https://hacs.xyz/

---

## Architecture Changes (December 2023)

### Major Refactoring: Modular Sensor Structure

**What Changed**:
- **BEFORE**: Single `sensor.py` file (3485 lines) with all 17 sensor implementations
- **AFTER**: Modular structure with sensors organized in subfolders by function

**Migration Summary**:
1. Created `sensor/` directory with category subfolders (weather, heat, optimization, cop)
2. Split all sensors into individual files (~60-120 lines each)
3. Created `sensor/__init__.py` as platform entry point
4. Removed legacy `sensor.py` and `coordinator_sensors.py` (backed up as `_*_legacy_backup.py`)
5. Updated CLAUDE.md documentation to reflect new structure

**Benefits**:
- ✅ **Maintainability**: Each sensor in ~100 lines vs 3500-line monolith
- ✅ **Discoverability**: Clear categorization by function
- ✅ **Testing**: Better isolation for unit tests
- ✅ **Collaboration**: Reduced merge conflicts
- ✅ **IDE Performance**: Faster loading and navigation

**Migration Path for Developers**:
- All sensor imports now come from `sensor/` submodules
- Coordinator pattern remains unchanged
- Tests will need minor import path updates
- No changes to configuration or user-facing functionality

---

**Last Updated**: 2023-12-23
**Version**: 2.0.0 (post-modular-refactor)
**Maintainer**: @bvweerd
