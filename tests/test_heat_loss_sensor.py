import aiohttp
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import HeatLossSensor


@pytest.mark.asyncio
async def test_heat_loss_sensor_fetches_weather_when_no_outdoor(hass):
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        sensor = HeatLossSensor(
            hass=hass,
            name="Heat Loss",
            unique_id="hl1",
            area_m2=20.0,
            energy_label="A",
            indoor_sensor=None,
            icon="mdi:test",
            device=DeviceInfo(identifiers={("test", "1")}),
        )
    with patch.object(
        sensor, "_fetch_weather", AsyncMock(return_value=(10.0, [11.0, 12.0]))
    ):
        await sensor.async_update()
    assert sensor.native_value == 0.132
    assert sensor.extra_state_attributes["forecast"] == [0.12, 0.108]
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_heat_loss_sensor_uses_outdoor_sensor_when_available(hass):
    hass.states.async_set("sensor.outdoor", "5")
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        sensor = HeatLossSensor(
            hass=hass,
            name="Heat Loss",
            unique_id="hl2",
            area_m2=10.0,
            energy_label="A",
            indoor_sensor=None,
            icon="mdi:test",
            device=DeviceInfo(identifiers={("test", "2")}),
            outdoor_sensor="sensor.outdoor",
        )
    with patch.object(sensor, "_fetch_weather", AsyncMock()) as mock_fetch:
        await sensor.async_update()
    mock_fetch.assert_not_called()
    assert sensor.native_value == 0.096
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
async def test_heat_loss_sensor_handles_fetch_errors(hass, session):
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=session,
    ):
        sensor = HeatLossSensor(
            hass=hass,
            name="Heat Loss",
            unique_id="hl_err",
            area_m2=5.0,
            energy_label="A",
            indoor_sensor=None,
            icon="mdi:test",
            device=DeviceInfo(identifiers={("test", "err")}),
        )
        await sensor.async_update()
    assert sensor.available is False
    assert sensor.native_value == 0.0
    assert sensor.extra_state_attributes == {}
    await sensor.async_will_remove_from_hass()
