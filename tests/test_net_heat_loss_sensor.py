import pytest
from types import SimpleNamespace
from unittest.mock import patch
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import NetHeatLossSensor


@pytest.mark.asyncio
async def test_net_heat_loss_sensor_combines_sources(hass):
    hass.states.async_set("sensor.outdoor", "10")
    heat_loss = SimpleNamespace(extra_state_attributes={"forecast": [0.1, 0.2]})
    window_gain = SimpleNamespace(
        native_value=0.0,
        extra_state_attributes={"forecast": [0.05, 0.05]},
    )
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        sensor = NetHeatLossSensor(
            hass=hass,
            name="Net Heat Loss",
            unique_id="nh1",
            area_m2=10.0,
            energy_label="A",
            indoor_sensor=None,
            icon="mdi:test",
            device=DeviceInfo(identifiers={("test", "1")}),
            heat_loss_sensor=heat_loss,
            window_gain_sensor=window_gain,
            outdoor_sensor="sensor.outdoor",
        )
    await sensor.async_update()
    assert sensor.native_value == pytest.approx(0.066, rel=1e-3)
    assert sensor.extra_state_attributes["forecast"] == [0.05, 0.15]
    await sensor.async_will_remove_from_hass()
