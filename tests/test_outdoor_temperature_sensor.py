import aiohttp
import pytest
from types import SimpleNamespace
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.components.sensor import SensorStateClass

from custom_components.heating_curve_optimizer.coordinator_sensors import (
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


class _ErrorResponse:
    def __init__(self, json_exc: Exception | None = None):
        self._json_exc = json_exc

    async def json(self):
        if self._json_exc:
            raise self._json_exc
        return {}  # pragma: no cover

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ErrorSession:
    def __init__(
        self, get_exc: Exception | None = None, json_exc: Exception | None = None
    ):
        self._get_exc = get_exc
        self._json_exc = json_exc

    def get(self, *args, **kwargs):
        if self._get_exc:
            raise self._get_exc
        return _ErrorResponse(self._json_exc)


request_info = SimpleNamespace(real_url="http://test")


@pytest.mark.skip(
    reason="OutdoorTemperatureSensor is now coordinator-based, fetch errors are handled in WeatherDataCoordinator"
)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        _ErrorSession(get_exc=aiohttp.ClientError("network")),
        _ErrorSession(
            json_exc=aiohttp.ContentTypeError(
                request_info=request_info, history=(), message="bad"
            )
        ),
    ],
    ids=["network_error", "json_error"],
)
async def test_outdoor_temperature_sensor_handles_fetch_errors(hass, session):
    """Test error handling - skipped as sensor is now coordinator-based."""
    pass
