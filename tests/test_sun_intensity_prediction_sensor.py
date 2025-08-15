import aiohttp
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from homeassistant.helpers.entity import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import (
    SunIntensityPredictionSensor,
)


@pytest.mark.asyncio
async def test_sun_intensity_prediction_sensor_forecast_and_history(hass):
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        sensor = SunIntensityPredictionSensor(
            hass=hass,
            name="Sun Intensity",
            unique_id="sun1",
            device=DeviceInfo(identifiers={("test", "1")}),
        )
        with patch.object(
            sensor, "_fetch_radiation", AsyncMock(return_value=[100.0, 50.0])
        ):
            await sensor.async_update()
    assert sensor.native_value == 100.0
    assert sensor.extra_state_attributes["forecast"] == [100.0, 50.0]
    assert sensor.extra_state_attributes["history"] == [100.0]
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_sun_intensity_prediction_sensor_handles_no_data(hass):
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        sensor = SunIntensityPredictionSensor(
            hass=hass,
            name="Sun Intensity",
            unique_id="sun2",
            device=DeviceInfo(identifiers={("test", "2")}),
        )
        with patch.object(sensor, "_fetch_radiation", AsyncMock(return_value=[])):
            await sensor.async_update()
    assert sensor.native_value == 0.0
    assert sensor.extra_state_attributes["forecast"] == []
    assert sensor.extra_state_attributes["history"] == [0.0]
    await sensor.async_will_remove_from_hass()


class _ErrorResponse:
    def __init__(self, json_exc: Exception | None = None):
        self._json_exc = json_exc

    async def json(self):
        if self._json_exc:
            raise self._json_exc
        return {}

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
async def test_sun_intensity_prediction_sensor_handles_fetch_errors(hass, session):
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=session,
    ):
        sensor = SunIntensityPredictionSensor(
            hass=hass,
            name="Sun Intensity",
            unique_id="sun_err",
            device=DeviceInfo(identifiers={("test", "err")}),
        )
        await sensor.async_update()
    assert sensor.available is False
    assert sensor.native_value == 0.0
    assert sensor.extra_state_attributes == {}
    await sensor.async_will_remove_from_hass()
