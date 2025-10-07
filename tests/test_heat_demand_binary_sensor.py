import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.binary_sensor import (
    HeatDemandBinarySensor,
)
from custom_components.heating_curve_optimizer.const import DOMAIN


@pytest.mark.asyncio
async def test_heat_demand_binary_sensor_tracks_net_heat(hass):
    entry_id = "test_entry"
    device = DeviceInfo(identifiers={(DOMAIN, entry_id)})
    sensor = HeatDemandBinarySensor(hass, entry_id, device)
    sensor.entity_id = "binary_sensor.test_heat_demand"

    await sensor.async_update()
    assert sensor.available is False

    hass.data.setdefault(DOMAIN, {}).setdefault("runtime", {}).setdefault(entry_id, {})[
        "net_heat_entity"
    ] = "sensor.net_heat"

    hass.states.async_set("sensor.net_heat", "2.5")
    await sensor.async_update()
    assert sensor.available is True
    assert sensor.is_on is True
    assert sensor.extra_state_attributes["net_heat_kW"] == 2.5

    hass.states.async_set("sensor.net_heat", "0")
    await sensor.async_update()
    assert sensor.is_on is False

    hass.states.async_set("sensor.net_heat", "-0.5")
    await sensor.async_update()
    assert sensor.is_on is False

    hass.states.async_set("sensor.net_heat", "unknown")
    await sensor.async_update()
    assert sensor.available is False
    assert sensor.extra_state_attributes["net_heat_entity_id"] == "sensor.net_heat"
