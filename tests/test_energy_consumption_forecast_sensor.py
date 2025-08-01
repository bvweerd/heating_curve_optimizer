import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import EnergyConsumptionForecastSensor


@pytest.mark.asyncio
async def test_fetch_history_multiple_entities(hass):
    sensor = EnergyConsumptionForecastSensor(
        hass=hass,
        name="test",
        unique_id="test",
        consumption_sensors=["sensor.c1", "sensor.c2"],
        production_sensors=[],
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    start = dt.datetime.utcnow()
    end = start

    with patch(
        "homeassistant.components.recorder.history.get_significant_states",
        return_value={},
    ) as mock_get, patch(
        "homeassistant.components.recorder.get_instance",
    ) as mock_get_instance:
        mock_recorder = AsyncMock()
        mock_recorder.async_add_executor_job.side_effect = (
            lambda func, *args: func(*args)
        )
        mock_get_instance.return_value = mock_recorder
        data = await sensor._fetch_history(["sensor.c1", "sensor.c2"], start, end)

    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == hass
    assert mock_get.call_args.args[1] == start
    assert mock_get.call_args.args[2] == end
    assert mock_get.call_args.args[3] == ["sensor.c1", "sensor.c2"]
    assert data == {}


@pytest.mark.asyncio
async def test_sensor_initializes_with_multiple_sources(hass):
    hass.states.async_set("sensor.c1", "0")
    hass.states.async_set("sensor.c2", "0")
    hass.states.async_set("sensor.p1", "0")
    hass.states.async_set("sensor.p2", "0")

    sensor = EnergyConsumptionForecastSensor(
        hass=hass,
        name="test",
        unique_id="test",
        consumption_sensors=["sensor.c1", "sensor.c2"],
        production_sensors=["sensor.p1", "sensor.p2"],
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    with patch.object(sensor, "_fetch_history", AsyncMock(return_value={})):
        await sensor.async_added_to_hass()
        await sensor.async_update()

    assert sensor.native_value == 0.0
