import pytest
from homeassistant.helpers.entity import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import ExternalForecastSensor


@pytest.mark.asyncio
async def test_external_forecast_sensor_copies_attributes(hass):
    hass.states.async_set("sensor.test_source", "5", {"forecast": [1, 2, 3]})

    sensor = ExternalForecastSensor(
        hass=hass,
        name="External Forecast",
        unique_id="ext_test",
        source_entity="sensor.test_source",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    await sensor.async_update()
    assert sensor.available is True
    assert sensor.native_value == 5.0
    assert sensor.extra_state_attributes["forecast"] == [1.0, 2.0, 3.0]

    await sensor.async_will_remove_from_hass()
