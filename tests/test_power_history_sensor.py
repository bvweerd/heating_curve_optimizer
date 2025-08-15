import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import PowerHistorySensor


@pytest.mark.asyncio
async def test_power_history_sensor_keeps_last_24h(hass):
    sensor = PowerHistorySensor(
        hass=hass,
        name="History",
        unique_id="ph1",
        source_entity="sensor.source",
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    now = datetime(2024, 1, 1, 0, 0, 0)

    class FakeDT(datetime):
        @classmethod
        def utcnow(cls):  # noqa: D401 - simple override
            return now

    with patch("custom_components.heating_curve_optimizer.sensor.datetime", FakeDT):
        hass.states.async_set("sensor.source", "10")
        await sensor.async_added_to_hass()
        assert sensor.extra_state_attributes["history"] == [10.0]

        now += timedelta(hours=23)
        hass.states.async_set("sensor.source", "20")
        await hass.async_block_till_done()
        assert sensor.extra_state_attributes["history"] == [10.0, 20.0]

        now += timedelta(hours=2)
        hass.states.async_set("sensor.source", "30")
        await hass.async_block_till_done()
        assert sensor.extra_state_attributes["history"] == [20.0, 30.0]

    await sensor.async_will_remove_from_hass()
