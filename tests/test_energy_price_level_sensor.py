import pytest
from homeassistant.helpers.entity import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import EnergyPriceLevelSensor


@pytest.mark.asyncio
async def test_energy_price_level_sensor(hass):
    hass.states.async_set(
        "sensor.current_consumption_price",
        "0.1",
        {"forecast": [0.1, 0.2, 0.3]},
    )
    hass.states.async_set(
        "sensor.expected_energy_consumption",
        "0",
        {"standby_forecast_net": [1.0, 1.0, 1.0]},
    )
    sensor = EnergyPriceLevelSensor(
        hass=hass,
        name="Available Energy by Price",
        unique_id="avail1",
        price_sensor="sensor.current_consumption_price",
        forecast_sensor="sensor.expected_energy_consumption",
        price_levels={"low": 0.15, "mid": 0.25, "high": 0.35},
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    await sensor.async_update()
    attrs = sensor.extra_state_attributes
    assert attrs["low"] == 1.0
    assert attrs["mid"] == 2.0
    assert attrs["high"] == 3.0
    assert sensor.native_value == 1.0
    await sensor.async_will_remove_from_hass()
