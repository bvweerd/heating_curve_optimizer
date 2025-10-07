import pytest
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import HeatLossSensor


class DummyOutdoorSensor(SensorEntity):
    """Minimal sensor without an assigned entity_id."""


@pytest.mark.asyncio
async def test_heat_loss_sensor_uses_outdoor_sensor(hass):
    hass.states.async_set("sensor.outdoor", "5", {"forecast": [4, 6]})
    sensor = HeatLossSensor(
        hass=hass,
        name="Heat Loss",
        unique_id="hl1",
        area_m2=10.0,
        energy_label="A",
        indoor_sensor=None,
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
        outdoor_sensor="sensor.outdoor",
    )
    await sensor.async_update()
    assert sensor.native_value == 0.096
    assert sensor.extra_state_attributes["forecast"] == [0.102, 0.09]
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_heat_loss_sensor_handles_missing_outdoor(hass):
    hass.states.async_set("sensor.outdoor", "unknown")
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
    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_heat_loss_sensor_supports_sensor_entity_without_id(hass):
    sensor = HeatLossSensor(
        hass=hass,
        name="Heat Loss",
        unique_id="hl3",
        area_m2=10.0,
        energy_label="A",
        indoor_sensor=None,
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "3")}),
        outdoor_sensor=DummyOutdoorSensor(),
    )
    await sensor.async_update()
    assert sensor.available is False
