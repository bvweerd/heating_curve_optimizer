import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp.client_reqrep import RequestInfo
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.device_registry import DeviceInfo
from yarl import URL

from custom_components.heating_curve_optimizer.sensor import OutdoorTemperatureSensor


@pytest.mark.asyncio
async def test_outdoor_temperature_sensor_has_measurement_state_class(hass):
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        sensor = OutdoorTemperatureSensor(
            hass=hass,
            name="test",
            unique_id="test",
            device=DeviceInfo(identifiers={("test", "1")}),
        )
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_fetch_weather_http_error(hass, caplog):
    resp = MagicMock()
    resp.raise_for_status.side_effect = aiohttp.ClientError()
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    session = MagicMock()
    session.get.return_value = cm
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=session,
    ):
        sensor = OutdoorTemperatureSensor(
            hass=hass,
            name="test",
            unique_id="test",
            device=DeviceInfo(identifiers={("test", "1")}),
        )
    caplog.set_level(logging.ERROR)
    result = await sensor._fetch_weather()
    assert result == (0.0, [])
    assert not sensor._attr_available
    assert "Error fetching weather data" in caplog.text


@pytest.mark.asyncio
async def test_fetch_weather_content_type_error(hass, caplog):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    req = RequestInfo(
        URL("http://example.com"), "GET", headers={}, real_url=URL("http://example.com")
    )
    resp.json = AsyncMock(side_effect=aiohttp.ContentTypeError(req, ()))
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    session = MagicMock()
    session.get.return_value = cm
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=session,
    ):
        sensor = OutdoorTemperatureSensor(
            hass=hass,
            name="test",
            unique_id="test",
            device=DeviceInfo(identifiers={("test", "1")}),
        )
    caplog.set_level(logging.ERROR)
    result = await sensor._fetch_weather()
    assert result == (0.0, [])
    assert not sensor._attr_available
    assert "Error parsing weather data" in caplog.text


@pytest.mark.asyncio
async def test_fetch_weather_value_error(hass, caplog):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(side_effect=ValueError("boom"))
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    session = MagicMock()
    session.get.return_value = cm
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=session,
    ):
        sensor = OutdoorTemperatureSensor(
            hass=hass,
            name="test",
            unique_id="test",
            device=DeviceInfo(identifiers={("test", "1")}),
        )
    caplog.set_level(logging.ERROR)
    result = await sensor._fetch_weather()
    assert result == (0.0, [])
    assert not sensor._attr_available
    assert "Error parsing weather data" in caplog.text
