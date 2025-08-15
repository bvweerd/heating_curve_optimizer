import pytest
from homeassistant.helpers.device_registry import DeviceInfo
from unittest.mock import AsyncMock, patch

from custom_components.heating_curve_optimizer.sensor import QuadraticCopSensor


@pytest.mark.asyncio
async def test_quadratic_cop_sensor_computes_value(hass):
    hass.states.async_set("sensor.supply", "35")
    hass.states.async_set("sensor.outdoor", "5")
    sensor = QuadraticCopSensor(
        hass=hass,
        name="COP",
        unique_id="cop1",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    await sensor.async_update()
    assert sensor.native_value == 4.6
    assert sensor.available is True
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_quadratic_cop_sensor_handles_unavailable(hass):
    hass.states.async_set("sensor.supply", "unknown")
    hass.states.async_set("sensor.outdoor", "5")
    sensor = QuadraticCopSensor(
        hass=hass,
        name="COP",
        unique_id="cop2",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=DeviceInfo(identifiers={("test", "2")}),
    )
    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_quadratic_cop_sensor_fetches_weather_when_no_sensor(hass):
    hass.states.async_set("sensor.supply", "35")
    sensor = QuadraticCopSensor(
        hass=hass,
        name="COP",
        unique_id="cop3",
        supply_sensor="sensor.supply",
        outdoor_sensor=None,
        device=DeviceInfo(identifiers={("test", "3")}),
    )
    with patch.object(sensor, "_fetch_outdoor_temp", AsyncMock(return_value=5.0)):
        await sensor.async_update()
    assert sensor.native_value == 4.6
    await sensor.async_will_remove_from_hass()
