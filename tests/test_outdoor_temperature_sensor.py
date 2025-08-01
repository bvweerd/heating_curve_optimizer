import pytest
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.components.sensor import SensorStateClass

from custom_components.heating_curve_optimizer.sensor import OutdoorTemperatureSensor


@pytest.mark.asyncio
async def test_outdoor_temperature_sensor_has_measurement_state_class(hass):
    sensor = OutdoorTemperatureSensor(
        hass=hass,
        name="test",
        unique_id="test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    await sensor.async_will_remove_from_hass()
