import pytest
from types import SimpleNamespace
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import NetHeatLossSensor


@pytest.mark.asyncio
async def test_net_heat_loss_sensor_combines_sources(hass):
    # NetHeatLossSensor now simply combines heat_loss and window_gain values
    # It no longer recalculates - just subtracts solar gain from heat loss
    heat_loss = SimpleNamespace(
        native_value=0.1, extra_state_attributes={"forecast": [0.1, 0.2]}
    )
    window_gain = SimpleNamespace(
        native_value=0.05, extra_state_attributes={"forecast": [0.05, 0.05]}
    )
    sensor = NetHeatLossSensor(
        hass=hass,
        name="Net Heat",
        unique_id="nh1",
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
        heat_loss_sensor=heat_loss,
        window_gain_sensor=window_gain,
    )

    await sensor.async_update()
    # Net heat = 0.1 (heat loss) - 0.05 (solar gain) = 0.05
    assert sensor.native_value == pytest.approx(0.05, rel=1e-3)
    # Forecast: [0.1 - 0.05, 0.2 - 0.05] = [0.05, 0.15]
    assert sensor.extra_state_attributes["forecast"] == [0.05, 0.15]
    assert sensor.extra_state_attributes["forecast_time_base"] == 60
    await sensor.async_will_remove_from_hass()
