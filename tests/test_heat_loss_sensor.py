import pytest
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
