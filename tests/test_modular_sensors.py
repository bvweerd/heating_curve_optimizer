"""Test all modular sensors."""

import pytest
from unittest.mock import MagicMock
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.components.sensor import SensorStateClass

# Weather sensors
from custom_components.heating_curve_optimizer.sensor.weather.outdoor_temperature import (
    CoordinatorOutdoorTemperatureSensor,
)

# Heat sensors
from custom_components.heating_curve_optimizer.sensor.heat.heat_loss import (
    CoordinatorHeatLossSensor,
)
from custom_components.heating_curve_optimizer.sensor.heat.solar_gain import (
    CoordinatorWindowSolarGainSensor,
)
from custom_components.heating_curve_optimizer.sensor.heat.pv_production import (
    CoordinatorPVProductionForecastSensor,
)
from custom_components.heating_curve_optimizer.sensor.heat.net_heat_loss import (
    CoordinatorNetHeatLossSensor,
)

# Optimization sensors
from custom_components.heating_curve_optimizer.sensor.optimization.heating_curve_offset import (
    CoordinatorHeatingCurveOffsetSensor,
)
from custom_components.heating_curve_optimizer.sensor.optimization.optimized_supply_temperature import (
    CoordinatorOptimizedSupplyTemperatureSensor,
)
from custom_components.heating_curve_optimizer.sensor.optimization.heat_buffer import (
    CoordinatorHeatBufferSensor,
)
from custom_components.heating_curve_optimizer.sensor.optimization.cost_savings import (
    CoordinatorCostSavingsSensor,
)

# COP sensors
from custom_components.heating_curve_optimizer.sensor.cop.quadratic_cop import (
    CoordinatorQuadraticCopSensor,
)
from custom_components.heating_curve_optimizer.sensor.cop.calculated_supply_temperature import (
    CoordinatorCalculatedSupplyTemperatureSensor,
)

# Diagnostics sensor
from custom_components.heating_curve_optimizer.sensor.diagnostics_sensor import (
    CoordinatorDiagnosticsSensor,
)


@pytest.fixture
def device_info():
    """Create mock device info."""
    return DeviceInfo(identifiers={("heating_curve_optimizer", "test")})


# === Weather Sensor Tests ===


@pytest.mark.asyncio
async def test_outdoor_temperature_sensor(hass, device_info):
    """Test outdoor temperature sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"current_temperature": 12.5}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorOutdoorTemperatureSensor(
        coordinator=mock_coordinator,
        name="Outdoor Temperature",
        unique_id="test_outdoor",
        device=device_info,
    )

    assert sensor.native_value == 12.5
    assert sensor.available is True
    assert sensor.state_class == SensorStateClass.MEASUREMENT


# === Heat Sensor Tests ===


@pytest.mark.asyncio
async def test_heat_loss_sensor(hass, device_info):
    """Test heat loss sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"heat_loss": 2.5}
    mock_coordinator.last_update_success = True
    mock_coordinator.config = {"area_m2": 150, "energy_label": "C"}

    sensor = CoordinatorHeatLossSensor(
        coordinator=mock_coordinator,
        name="Heat Loss",
        unique_id="test_heat_loss",
        icon="mdi:fire",
        device=device_info,
    )

    assert sensor.native_value == 2.5
    assert sensor.available is True


@pytest.mark.asyncio
async def test_solar_gain_sensor(hass, device_info):
    """Test solar gain sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"solar_gain": 0.8}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorWindowSolarGainSensor(
        coordinator=mock_coordinator,
        name="Solar Gain",
        unique_id="test_solar_gain",
        icon="mdi:weather-sunny",
        device=device_info,
    )

    assert sensor.native_value == 0.8
    assert sensor.available is True


@pytest.mark.asyncio
async def test_pv_production_sensor(hass, device_info):
    """Test PV production forecast sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"pv_production_forecast": [1.2, 1.3, 1.4]}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorPVProductionForecastSensor(
        coordinator=mock_coordinator,
        name="PV Production",
        unique_id="test_pv_production",
        icon="mdi:solar-power",
        device=device_info,
    )

    assert sensor.native_value == 1.2  # First item in forecast
    assert sensor.available is True


@pytest.mark.asyncio
async def test_net_heat_loss_sensor(hass, device_info):
    """Test net heat loss sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"net_heat_loss": 1.7}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorNetHeatLossSensor(
        coordinator=mock_coordinator,
        name="Net Heat Loss",
        unique_id="test_net_heat_loss",
        icon="mdi:thermometer-minus",
        device=device_info,
    )

    assert sensor.native_value == 1.7
    assert sensor.available is True


@pytest.mark.asyncio
async def test_net_heat_loss_sensor_unavailable(hass, device_info):
    """Test net heat loss sensor when coordinator fails."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = None
    mock_coordinator.last_update_success = False

    sensor = CoordinatorNetHeatLossSensor(
        coordinator=mock_coordinator,
        name="Net Heat Loss",
        unique_id="test_net_heat_loss",
        icon="mdi:thermometer-minus",
        device=device_info,
    )

    assert sensor.available is False


# === Optimization Sensor Tests ===


@pytest.mark.asyncio
async def test_heating_curve_offset_sensor(hass, device_info):
    """Test heating curve offset sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"optimized_offset": 2.0}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorHeatingCurveOffsetSensor(
        coordinator=mock_coordinator,
        name="Heating Curve Offset",
        unique_id="test_offset",
        icon="mdi:tune",
        device=device_info,
    )

    assert sensor.native_value == 2.0
    assert sensor.available is True


@pytest.mark.asyncio
async def test_optimized_supply_temperature_sensor(hass, device_info):
    """Test optimized supply temperature sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"future_supply_temperatures": [42.5, 43.0, 43.5]}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorOptimizedSupplyTemperatureSensor(
        coordinator=mock_coordinator,
        name="Optimized Supply Temperature",
        unique_id="test_supply_temp",
        icon="mdi:thermometer",
        device=device_info,
    )

    assert sensor.native_value == 42.5  # First item
    assert sensor.available is True


@pytest.mark.asyncio
async def test_heat_buffer_sensor(hass, device_info):
    """Test heat buffer sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"buffer_evolution": [3.5, 3.6, 3.7]}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorHeatBufferSensor(
        coordinator=mock_coordinator,
        name="Heat Buffer",
        unique_id="test_buffer",
        icon="mdi:battery",
        device=device_info,
    )

    assert sensor.native_value == 3.5  # First item
    assert sensor.available is True


@pytest.mark.asyncio
async def test_cost_savings_sensor(hass, device_info):
    """Test cost savings sensor."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"cost_savings": 0.15}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorCostSavingsSensor(
        coordinator=mock_coordinator,
        name="Cost Savings",
        unique_id="test_savings",
        icon="mdi:currency-eur",
        device=device_info,
    )

    assert sensor.native_value == 0.15
    assert sensor.available is True


# === COP Sensor Tests ===


@pytest.mark.asyncio
async def test_quadratic_cop_sensor(hass, device_info):
    """Test quadratic COP sensor."""
    # COP sensor doesn't use coordinator, it reads from supply/outdoor sensors
    hass.states.async_set("sensor.supply_temp", "40.0")

    mock_weather_coordinator = MagicMock()
    mock_weather_coordinator.data = {"current_temperature": 10.0}

    sensor = CoordinatorQuadraticCopSensor(
        hass=hass,
        weather_coordinator=mock_weather_coordinator,
        name="Heat Pump COP",
        unique_id="test_cop",
        supply_sensor="sensor.supply_temp",
        device=device_info,
    )

    # Trigger update
    await sensor.async_update()

    assert sensor.native_value is not None  # COP should be calculated
    assert sensor.native_value > 0


@pytest.mark.asyncio
async def test_calculated_supply_temperature_sensor(hass, device_info):
    """Test calculated supply temperature sensor."""
    mock_weather_coordinator = MagicMock()
    mock_weather_coordinator.data = {"current_temperature": 10.0}
    mock_weather_coordinator.last_update_success = True

    sensor = CoordinatorCalculatedSupplyTemperatureSensor(
        coordinator=mock_weather_coordinator,
        name="Calculated Supply Temperature",
        unique_id="test_calc_supply",
        device=device_info,
    )

    assert sensor.native_value is not None  # Should calculate supply temp
    assert sensor.native_value > 0


# === Diagnostics Sensor Tests ===


@pytest.mark.asyncio
async def test_diagnostics_sensor(hass, device_info):
    """Test diagnostics sensor."""
    mock_weather = MagicMock()
    mock_weather.data = {"current_temperature": 10.0}
    mock_weather.last_update_success = True

    mock_heat = MagicMock()
    mock_heat.data = {"heat_loss_kw": 2.0}
    mock_heat.last_update_success = True

    mock_opt = MagicMock()
    mock_opt.data = {"optimal_offset": 1.0}
    mock_opt.last_update_success = True

    sensor = CoordinatorDiagnosticsSensor(
        weather_coordinator=mock_weather,
        heat_coordinator=mock_heat,
        optimization_coordinator=mock_opt,
        name="Diagnostics",
        unique_id="test_diagnostics",
        device=device_info,
    )

    # State should be "OK" when all coordinators succeed
    assert sensor.native_value == "OK"
    assert sensor.available is True


@pytest.mark.asyncio
async def test_diagnostics_sensor_with_errors(hass, device_info):
    """Test diagnostics sensor when coordinators fail."""
    mock_weather = MagicMock()
    mock_weather.data = None
    mock_weather.last_update_success = False

    mock_heat = MagicMock()
    mock_heat.data = None
    mock_heat.last_update_success = False

    mock_opt = MagicMock()
    mock_opt.data = None
    mock_opt.last_update_success = False

    sensor = CoordinatorDiagnosticsSensor(
        weather_coordinator=mock_weather,
        heat_coordinator=mock_heat,
        optimization_coordinator=mock_opt,
        name="Diagnostics",
        unique_id="test_diagnostics",
        device=device_info,
    )

    # Sensor should still be available but show error state
    assert sensor.available is True


# === Sensor Attribute Tests ===


@pytest.mark.asyncio
async def test_heat_loss_sensor_with_attributes(hass, device_info):
    """Test heat loss sensor includes detailed attributes."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "heat_loss": 2.5,
        "heat_loss_forecast": [2.5, 2.6, 2.7],
    }
    mock_coordinator.last_update_success = True
    mock_coordinator.config = {"area_m2": 150, "energy_label": "C"}

    sensor = CoordinatorHeatLossSensor(
        coordinator=mock_coordinator,
        name="Heat Loss",
        unique_id="test_heat_loss",
        icon="mdi:fire",
        device=device_info,
    )

    assert sensor.native_value == 2.5
    # Extra attributes should be available
    assert hasattr(sensor, "extra_state_attributes")


@pytest.mark.asyncio
async def test_sensor_with_none_data(hass, device_info):
    """Test sensors handle None data gracefully."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = None
    mock_coordinator.last_update_success = True

    sensor = CoordinatorHeatLossSensor(
        coordinator=mock_coordinator,
        name="Heat Loss",
        unique_id="test_heat_loss",
        icon="mdi:fire",
        device=device_info,
    )

    assert sensor.native_value is None
    assert sensor.available is False


@pytest.mark.asyncio
async def test_sensor_with_missing_key(hass, device_info):
    """Test sensors handle missing data keys."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"other_value": 123}  # Missing expected key
    mock_coordinator.last_update_success = True
    mock_coordinator.config = {"area_m2": 150, "energy_label": "C"}

    sensor = CoordinatorHeatLossSensor(
        coordinator=mock_coordinator,
        name="Heat Loss",
        unique_id="test_heat_loss",
        icon="mdi:fire",
        device=device_info,
    )

    # Should return None when key is missing
    assert sensor.native_value is None
