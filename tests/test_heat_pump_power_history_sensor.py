import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import (
    HeatPumpPowerHistorySensor,
)


@pytest.mark.asyncio
async def test_heat_pump_power_history_sensor_records_history(hass):
    hass.states.async_set("sensor.hp_power", "500")
    sensor = HeatPumpPowerHistorySensor(
        hass=hass,
        name="Heat Pump Power History",
        unique_id="hph1",
        power_sensor="sensor.hp_power",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    await sensor.async_added_to_hass()
    history = sensor.extra_state_attributes["history"]
    assert sensor.native_value == 500.0
    assert len(history) == 1
    assert history[0]["power"] == 500.0

    hass.states.async_set("sensor.hp_power", "600")
    await hass.async_block_till_done()
    history = sensor.extra_state_attributes["history"]
    assert sensor.native_value == 600.0
    assert len(history) == 2
    assert history[-1]["power"] == 600.0

    await sensor.async_will_remove_from_hass()
