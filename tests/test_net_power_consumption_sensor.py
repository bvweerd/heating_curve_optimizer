import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import NetPowerConsumptionSensor


@pytest.mark.asyncio
async def test_net_power_consumption_sensor_calculates_net(hass):
    hass.states.async_set("sensor.c1", "10")
    hass.states.async_set("sensor.c2", "5")
    hass.states.async_set("sensor.p1", "3")
    sensor = NetPowerConsumptionSensor(
        hass=hass,
        name="Net Power",
        unique_id="np1",
        consumption_sensors=["sensor.c1", "sensor.c2"],
        production_sensors=["sensor.p1"],
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    await sensor.async_update()
    assert sensor.native_value == 12.0
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_net_power_consumption_sensor_ignores_invalid(hass):
    hass.states.async_set("sensor.c1", "invalid")
    hass.states.async_set("sensor.p1", "2")
    sensor = NetPowerConsumptionSensor(
        hass=hass,
        name="Net Power",
        unique_id="np2",
        consumption_sensors=["sensor.c1"],
        production_sensors=["sensor.p1"],
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "2")}),
    )
    await sensor.async_update()
    assert sensor.native_value == -2.0
    await sensor.async_will_remove_from_hass()
