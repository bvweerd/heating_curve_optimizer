# Testing Guide

Comprehensive guide for testing the Heating Curve Optimizer integration.

## Test Framework

### Tools

- **pytest**: Test runner
- **pytest-asyncio**: Async test support
- **pytest-cov**: Coverage reporting
- **pytest-homeassistant-custom-component**: HA fixtures
- **syrupy**: Snapshot testing

### Configuration

```ini
# setup.cfg
[tool:pytest]
testpaths = tests
asyncio_mode = auto
addopts = --disable-warnings --maxfail=1 -q -p syrupy --strict --cov=tests
```

## Running Tests

### All Tests

```bash
pytest
```

### Specific Test File

```bash
pytest tests/test_heating_curve_offset_sensor.py -v
```

### With Coverage

```bash
pytest --cov=custom_components.heating_curve_optimizer --cov-report=html
```

Coverage report generated in `htmlcov/index.html`

### Watch Mode

```bash
pytest-watch
```

Automatically re-runs tests on file changes.

## Test Structure

### Test Files

One test file per module (18 total):

```
tests/
├── test_outdoor_temperature_sensor.py
├── test_heat_loss_sensor.py
├── test_net_heat_loss_sensor.py
├── test_heating_curve_offset_sensor.py  ← Most critical
├── test_quadratic_cop_sensor.py
├── test_window_solar_gain_sensor.py
├── test_current_electricity_price_sensor.py
└── ... (11 more)
```

### Test Patterns

#### Basic Sensor Test

```python
import pytest
from unittest.mock import Mock, patch
from custom_components.heating_curve_optimizer.sensor import HeatLossSensor

@pytest.mark.asyncio
async def test_heat_loss_sensor(hass):
    """Test heat loss calculation."""
    # Arrange
    entry = Mock()
    entry.entry_id = "test_id"
    entry.data = {
        "area_m2": 150,
        "energy_label": "C",
    }

    hass.states.async_set("sensor.outdoor_temperature", "5.0")
    hass.data = {"heating_curve_optimizer": {"runtime": {}}}

    sensor = HeatLossSensor(hass, entry)

    # Act
    await sensor.async_update()

    # Assert
    assert sensor.state is not None
    assert sensor.state > 0  # Heat loss should be positive
    assert sensor.available is True
```

#### API Mocking

```python
@pytest.mark.asyncio
@patch('custom_components.heating_curve_optimizer.sensor.aiohttp.ClientSession')
async def test_outdoor_temperature_api(mock_session, hass):
    """Test outdoor temperature API call."""
    # Arrange
    mock_response = Mock()
    mock_response.json.return_value = {
        "hourly": {
            "temperature_2m": [5.0, 5.5, 6.0, ...]
        }
    }
    mock_session.return_value.__aenter__.return_value.get.return_value = mock_response

    sensor = OutdoorTemperatureSensor(hass, entry)

    # Act
    await sensor.async_update()

    # Assert
    assert sensor.state == 5.0
    mock_session.assert_called_once()
```

#### Optimization Test

```python
@pytest.mark.asyncio
async def test_optimization_algorithm(hass):
    """Test heating curve optimization."""
    # Arrange
    demand_forecast = [8.0, 7.0, 6.0, 5.0, 6.0, 7.0]
    price_forecast = [0.15, 0.20, 0.30, 0.35, 0.25, 0.20]

    sensor = HeatingCurveOffsetSensor(hass, entry)

    # Act
    offsets, buffer = sensor._optimize_offsets(
        demand_forecast,
        price_forecast,
        base_temp=38,
        k_factor=0.03,
        cop_compensation=0.9
    )

    # Assert
    assert len(offsets) == 6
    assert all(-4 <= o <= 4 for o in offsets)
    assert offsets[0] > offsets[3]  # Higher offset during low price
    assert all(abs(offsets[i+1] - offsets[i]) <= 1 for i in range(5))  # ±1 constraint
```

## Test Categories

### Unit Tests

Test individual components in isolation.

**Example**: Heat loss calculation
```python
def test_heat_loss_calculation():
    """Test U × A × ΔT formula."""
    u_value = 0.8
    area = 150
    delta_t = 15  # 20°C indoor - 5°C outdoor

    expected = u_value * area * delta_t / 1000  # = 1.8 kW

    assert abs(heat_loss - expected) < 0.01
```

### Integration Tests

Test component interactions.

**Example**: Net heat loss = heat loss - solar gain
```python
@pytest.mark.asyncio
async def test_net_heat_loss_integration(hass):
    """Test net heat loss calculation."""
    # Setup heat loss sensor
    hass.states.async_set("sensor.heat_loss", "8.0")

    # Setup solar gain sensor
    hass.states.async_set("sensor.solar_gain", "2.5")

    # Create net heat loss sensor
    sensor = NetHeatLossSensor(hass, entry)
    await sensor.async_update()

    # Should be 8.0 - 2.5 = 5.5
    assert abs(sensor.state - 5.5) < 0.01
```

### Edge Case Tests

Test boundary conditions and error handling.

```python
@pytest.mark.asyncio
async def test_optimization_extreme_cold(hass):
    """Test optimization during capacity-limited conditions."""
    # Very high demand (at capacity)
    demand_forecast = [12.0] * 6

    # Varying prices (should have minimal effect)
    price_forecast = [0.15, 0.40, 0.15, 0.40, 0.15, 0.40]

    offsets, _ = sensor._optimize_offsets(...)

    # Should use max offset regardless of price
    assert all(o == 4 for o in offsets)
```

## Critical Tests

### Optimization Algorithm Tests

**Location**: `tests/test_heating_curve_offset_sensor.py`

**Test cases**:

1. **Basic optimization**
   - Variable prices → temporal shifting
   - Verify offset range (-4 to +4)
   - Verify rate limits (±1°C)

2. **Buffer management**
   - Negative demand → buffer accumulation
   - Buffer usage during positive demand
   - Buffer non-negativity constraint

3. **Price correlation**
   - High offset during low prices
   - Low offset during high prices
   - Monotonic relationship (within constraints)

4. **Constraint validation**
   - Temperature bounds respected
   - Offset change limits enforced
   - COP within physical range

5. **Edge cases**
   - All prices equal (fixed pricing)
   - All demands zero (no heating needed)
   - Extreme cold (capacity limited)
   - Missing price forecast

### COP Calculation Tests

**Location**: `tests/test_quadratic_cop_sensor.py`

```python
@pytest.mark.asyncio
async def test_cop_calculation_reference_point(hass):
    """Test COP at reference condition (A7/W35)."""
    sensor = QuadraticCopSensor(hass, entry)

    # Reference: 7°C outdoor, 35°C supply
    # Should equal base_cop
    cop = sensor._calculate_cop(
        outdoor_temp=7.0,
        supply_temp=35.0,
        base_cop=3.8,
        k_factor=0.03,
        compensation=1.0  # No compensation
    )

    assert abs(cop - 3.8) < 0.01  # Should match base_cop
```

## Mocking Strategies

### Home Assistant Core

```python
@pytest.fixture
def hass():
    """Mock Home Assistant."""
    hass = Mock()
    hass.data = {}
    hass.states = Mock()
    hass.states.async_set = Mock()
    hass.states.get = Mock(return_value=Mock(state="0"))
    return hass
```

### Config Entry

```python
@pytest.fixture
def config_entry():
    """Mock config entry."""
    entry = Mock()
    entry.entry_id = "test_entry"
    entry.data = {
        "area_m2": 150,
        "energy_label": "C",
        "base_cop": 3.5,
        "k_factor": 0.03,
        "cop_compensation_factor": 0.9,
    }
    entry.options = {}
    return entry
```

### API Responses

```python
@pytest.fixture
def mock_weather_response():
    """Mock weather API response."""
    return {
        "hourly": {
            "time": ["2025-11-15T00:00", "2025-11-15T01:00", ...],
            "temperature_2m": [5.0, 5.2, 5.5, ...],
            "shortwave_radiation": [0, 0, 100, 200, ...]
        }
    }
```

## Test Coverage Goals

### Current Coverage

```
custom_components/heating_curve_optimizer/
├── __init__.py          95%
├── sensor.py            87%  ← Core logic
├── config_flow.py       78%
├── number.py            85%
├── binary_sensor.py     90%
└── const.py             100% (constants)

Overall: 85%
```

### Target Coverage

- Critical paths (optimization): **>95%**
- Sensor updates: **>90%**
- Configuration flow: **>80%**
- Overall: **>85%**

## Continuous Integration

### GitHub Actions Workflow

```yaml
# .github/workflows/pytest.yml
name: Run Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: pytest --cov --cov-report=xml
      - uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
```

### Pre-commit Integration

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: pytest
        name: pytest
        entry: pytest
        language: system
        pass_filenames: false
        always_run: true
```

## Debugging Tests

### Verbose Output

```bash
pytest -v -s tests/test_heating_curve_offset_sensor.py
```

`-s` shows print statements

### Debug Specific Test

```bash
pytest tests/test_heating_curve_offset_sensor.py::test_optimization_algorithm -vv
```

### PDB Debugging

```python
@pytest.mark.asyncio
async def test_with_debugging(hass):
    """Test with breakpoint."""
    sensor = HeatLossSensor(hass, entry)

    import pdb; pdb.set_trace()  # Debugger breakpoint

    await sensor.async_update()
```

### Logging in Tests

```python
import logging

def test_with_logging():
    """Test with logging enabled."""
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger("custom_components.heating_curve_optimizer")

    # Test code
    ...

    # Logs will be visible
```

## Performance Testing

### Optimization Benchmark

```python
import time

@pytest.mark.asyncio
async def test_optimization_performance(hass):
    """Benchmark optimization speed."""
    sensor = HeatingCurveOffsetSensor(hass, entry)

    demand = [8.0] * 6
    prices = [0.25] * 6

    start = time.time()
    offsets, _ = sensor._optimize_offsets(demand, prices, ...)
    elapsed = time.time() - start

    assert elapsed < 1.0  # Should complete within 1 second
    print(f"Optimization took {elapsed*1000:.2f}ms")
```

### Memory Profiling

```python
import tracemalloc

def test_memory_usage():
    """Profile memory usage."""
    tracemalloc.start()

    # Run optimization
    sensor._optimize_offsets(...)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert peak < 50 * 1024 * 1024  # Less than 50 MB
    print(f"Peak memory: {peak / 1024 / 1024:.2f} MB")
```

## Best Practices

### Test Naming

```python
def test_<component>_<scenario>_<expected_result>():
    """Descriptive test name."""
```

Examples:
- `test_heat_loss_cold_weather_high_value()`
- `test_optimization_variable_prices_temporal_shift()`
- `test_buffer_negative_demand_accumulation()`

### Test Documentation

```python
@pytest.mark.asyncio
async def test_complex_scenario(hass):
    """
    Test optimization with complex scenario.

    Scenario:
        - Cold morning with low prices
        - Sunny midday with high prices
        - Evening with medium prices

    Expected:
        - Pre-heat in morning (high offset)
        - Coast during midday (low offset, use buffer)
        - Resume heating in evening (medium offset)
    """
    ...
```

### Arrange-Act-Assert Pattern

```python
async def test_sensor_update():
    # Arrange (setup)
    sensor = SomeSensor(hass, entry)
    hass.states.async_set("sensor.input", "10")

    # Act (execute)
    await sensor.async_update()

    # Assert (verify)
    assert sensor.state == expected_value
```

## Common Issues

### Async Tests Hanging

**Cause**: Missing `@pytest.mark.asyncio`

**Solution**: Always decorate async tests

### Mock Not Working

**Cause**: Patching wrong import path

**Solution**: Patch where used, not where defined

```python
# Wrong
@patch('custom_components.heating_curve_optimizer.sensor.aiohttp')

# Correct (if sensor imports from .sensor)
@patch('custom_components.heating_curve_optimizer.sensor.aiohttp')
```

### State Not Restored

**Cause**: Missing state setup in fixtures

**Solution**: Ensure hass.data is initialized

```python
hass.data = {
    "heating_curve_optimizer": {
        "runtime": {},
        entry_id: entry.data
    }
}
```

---

## Writing New Tests

### Checklist

- [ ] Test file created (or added to existing)
- [ ] Import statements correct
- [ ] Fixtures used appropriately
- [ ] Async decorator applied (if async)
- [ ] Arrange-Act-Assert structure
- [ ] Assertions verify expected behavior
- [ ] Edge cases covered
- [ ] Documentation/docstring added
- [ ] Test passes locally
- [ ] Pre-commit hooks pass

### Example Template

```python
"""Tests for NEW_COMPONENT."""
import pytest
from unittest.mock import Mock, patch
from custom_components.heating_curve_optimizer.sensor import NewSensor


@pytest.fixture
def hass():
    """Home Assistant mock."""
    hass = Mock()
    hass.data = {"heating_curve_optimizer": {"runtime": {}}}
    return hass


@pytest.fixture
def config_entry():
    """Config entry mock."""
    entry = Mock()
    entry.entry_id = "test"
    entry.data = {...}
    return entry


@pytest.mark.asyncio
async def test_new_sensor_basic(hass, config_entry):
    """Test basic functionality of new sensor."""
    # Arrange
    sensor = NewSensor(hass, config_entry)

    # Act
    await sensor.async_update()

    # Assert
    assert sensor.state is not None
    assert sensor.available is True


@pytest.mark.asyncio
async def test_new_sensor_edge_case(hass, config_entry):
    """Test edge case handling."""
    # Your test here
    ...
```

---

**Ready to contribute?** See [Contributing Guide](contributing.md) for the full development workflow!
