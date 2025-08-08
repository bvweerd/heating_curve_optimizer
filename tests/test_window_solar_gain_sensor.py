import aiohttp
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import WindowSolarGainSensor


@pytest.mark.asyncio
async def test_window_solar_gain_sensor_computes_gain(hass):
    hass.states.async_set("sun.sun", "above_horizon", {"azimuth": 180, "elevation": 45})
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        sensor = WindowSolarGainSensor(
            hass=hass,
            name="Solar Gain",
            unique_id="sg1",
            east_m2=0.0,
            west_m2=0.0,
            south_m2=10.0,
            u_value=1.2,
            icon="mdi:test",
            device=DeviceInfo(identifiers={("test", "1")}),
        )
        with patch.object(
            sensor, "_fetch_radiation", AsyncMock(return_value=[100.0, 50.0])
        ):
            await sensor.async_update()
    assert sensor.native_value == pytest.approx(0.589, rel=1e-3)
    assert sensor.extra_state_attributes["forecast"][1] == pytest.approx(
        0.295, rel=1e-3
    )
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_window_solar_gain_sensor_handles_no_data(hass):
    hass.states.async_set("sun.sun", "above_horizon", {"azimuth": 180, "elevation": 45})
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        sensor = WindowSolarGainSensor(
            hass=hass,
            name="Solar Gain",
            unique_id="sg2",
            east_m2=1.0,
            west_m2=1.0,
            south_m2=1.0,
            u_value=1.2,
            icon="mdi:test",
            device=DeviceInfo(identifiers={("test", "2")}),
        )
        with patch.object(sensor, "_fetch_radiation", AsyncMock(return_value=[])):
            await sensor.async_update()
    assert sensor.native_value == 0.0
    assert sensor.extra_state_attributes["forecast"] == []
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
    def __init__(self, get_exc: Exception | None = None, json_exc: Exception | None = None):
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
async def test_window_solar_gain_sensor_handles_fetch_errors(hass, session):
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=session,
    ):
        sensor = WindowSolarGainSensor(
            hass=hass,
            name="Solar Gain",
            unique_id="sg_err",
            east_m2=1.0,
            west_m2=1.0,
            south_m2=1.0,
            u_value=1.2,
            icon="mdi:test",
            device=DeviceInfo(identifiers={("test", "err")}),
        )
        await sensor.async_update()
    assert sensor.available is False
    assert sensor.native_value == 0.0
    assert sensor.extra_state_attributes == {}
    await sensor.async_will_remove_from_hass()
