import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import (
    HeatingCurveOffsetSensor,
    NetHeatDemandSensor,
)


@pytest.mark.asyncio
async def test_offset_sensor_handles_sensor_instance(hass):
    net = NetHeatDemandSensor(
        hass=hass,
        name="Hourly Net Heat Demand",
        unique_id="test_net",
        area_m2=10.0,
        energy_label="A",
        indoor_sensor=None,
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    hass.states.async_set("sensor.price", "0.0")

    sensor = HeatingCurveOffsetSensor(
        hass=hass,
        name="Heating Curve Offset",
        unique_id="offset",
        net_heat_sensor=net,
        price_sensor="sensor.price",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()
    await net.async_will_remove_from_hass()
