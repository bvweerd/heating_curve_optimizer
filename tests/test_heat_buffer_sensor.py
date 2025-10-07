import pytest
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.components.sensor import SensorStateClass

from custom_components.heating_curve_optimizer.sensor import HeatBufferSensor


@pytest.mark.asyncio
async def test_heat_buffer_sensor_reads_evolution(hass):
    hass.states.async_set(
        "sensor.heating_curve_offset",
        "0",
        {"buffer_evolution": [0.0, 1.5, 3.0]},
    )

    sensor = HeatBufferSensor(
        hass=hass,
        name="Heat Buffer",
        unique_id="buffer1",
        offset_entity="sensor.heating_curve_offset",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    await sensor.async_update()

    assert sensor.available is True
    assert sensor.native_value == 0.0
    assert sensor.extra_state_attributes["buffer_evolution"] == [0.0, 1.5, 3.0]
    assert sensor.extra_state_attributes["buffer_by_step"] == {
        "0": 0.0,
        "1": 1.5,
        "2": 3.0,
    }
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_heat_buffer_sensor_unavailable_without_offset(hass):
    sensor = HeatBufferSensor(
        hass=hass,
        name="Heat Buffer",
        unique_id="buffer2",
        offset_entity="sensor.missing_offset",
        device=DeviceInfo(identifiers={("test", "2")}),
    )

    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()
