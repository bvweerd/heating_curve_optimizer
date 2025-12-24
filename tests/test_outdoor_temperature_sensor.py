import pytest
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.components.sensor import SensorStateClass

from custom_components.heating_curve_optimizer.sensor.weather.outdoor_temperature import (
    CoordinatorOutdoorTemperatureSensor,
)
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_outdoor_temperature_sensor_has_measurement_state_class(hass):
    """Test that CoordinatorOutdoorTemperatureSensor has MEASUREMENT state class."""
    # Create a mock coordinator with minimal data
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"current_temperature": 10.0}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorOutdoorTemperatureSensor(
        coordinator=mock_coordinator,
        name="test",
        unique_id="test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    assert sensor.state_class == SensorStateClass.MEASUREMENT


@pytest.mark.asyncio
async def test_outdoor_temperature_sensor_native_value(hass):
    """Test that sensor returns correct native value from coordinator data."""
    # Create a mock coordinator with temperature data
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"current_temperature": 15.5}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorOutdoorTemperatureSensor(
        coordinator=mock_coordinator,
        name="Outdoor Temperature",
        unique_id="test_outdoor_temp",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    assert sensor.native_value == 15.5


@pytest.mark.asyncio
async def test_outdoor_temperature_sensor_availability(hass):
    """Test sensor availability based on coordinator status."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"current_temperature": 10.0}
    mock_coordinator.last_update_success = True

    sensor = CoordinatorOutdoorTemperatureSensor(
        coordinator=mock_coordinator,
        name="Outdoor Temperature",
        unique_id="test_outdoor_temp",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    # Sensor should be available when coordinator succeeds
    assert sensor.available is True

    # Sensor should be unavailable when coordinator fails
    mock_coordinator.last_update_success = False
    assert sensor.available is False

    # Sensor should be unavailable when data is None
    mock_coordinator.last_update_success = True
    mock_coordinator.data = None
    assert sensor.available is False
