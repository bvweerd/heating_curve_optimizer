import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer import sensor
from custom_components.heating_curve_optimizer.sensor import (
    HeatingCurveOffsetSensor,
    NetHeatDemandSensor,
)
from homeassistant.components.sensor import SensorStateClass

from unittest.mock import patch
from datetime import datetime


@pytest.mark.asyncio
async def test_offset_sensor_handles_sensor_instance(hass):
    with patch(
        "custom_components.heating_curve_optimizer.sensor.async_get_clientsession",
        return_value=None,
    ):
        net = NetHeatDemandSensor(
            hass=hass,
            name="Hourly Net Heat Demand",
            unique_id="test_net",
            area_m2=10.0,
            energy_label="A",
            indoor_sensor=None,
            icon="mdi:test",
            device=DeviceInfo(identifiers={("test", "1")}),
        )

    hass.states.async_set("sensor.price", "0.0")

    sensor = HeatingCurveOffsetSensor(
        hass=hass,
        name="Heating Curve Offset",
        unique_id="offset",
        net_heat_sensor=net,
        price_sensor="sensor.price",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()
    await net.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_offset_sensor_sets_future_offsets_attribute(hass):
    hass.states.async_set("sensor.net_heat", "1", {"forecast": [0.0] * 6})
    hass.states.async_set(
        "sensor.price",
        "0.0",
        {"raw_today": [0.0] * 24, "raw_tomorrow": []},
    )

    sensor = HeatingCurveOffsetSensor(
        hass=hass,
        name="Heating Curve Offset",
        unique_id="offset2",
        net_heat_sensor="sensor.net_heat",
        price_sensor="sensor.price",
        device=DeviceInfo(identifiers={("test", "2")}),
    )

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([1, 2, 3, 4, 5, 6], [0, 1, 3, 6, 10, 15]),
    ):
        await sensor.async_update()

    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["future_offsets"] == [1, 2, 3, 4, 5, 6]
    assert sensor.extra_state_attributes["prices"] == [0.0] * 6
    assert sensor.extra_state_attributes["buffer_evolution"] == [
        0,
        1,
        3,
        6,
        10,
        15,
    ]
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_offset_sensor_has_measurement_state_class(hass):
    sensor = HeatingCurveOffsetSensor(
        hass=hass,
        name="Heating Curve Offset",
        unique_id="offset3",
        net_heat_sensor="sensor.net_heat",
        price_sensor="sensor.price",
        device=DeviceInfo(identifiers={("test", "3")}),
    )

    assert sensor.state_class == SensorStateClass.MEASUREMENT
    await sensor.async_will_remove_from_hass()


def test_optimize_offsets_balances_buffer():
    demand = [1.0] * 6
    prices = [1.0] * 6
    offsets, evolution = sensor._optimize_offsets(demand, prices, buffer=2)
    assert len(offsets) == 6
    # final buffer should be zero
    assert evolution[-1] == 0
    # ensure step changes are at most 1
    assert all(abs(offsets[i] - offsets[i - 1]) <= 1 for i in range(1, 6))


@pytest.mark.asyncio
async def test_offset_sensor_respects_time_base(hass):
    hass.states.async_set("sensor.net_heat", "1", {"forecast": [0.0] * 24})
    hass.states.async_set(
        "sensor.price",
        "0.0",
        {"raw_today": [0.0] * 24, "raw_tomorrow": []},
    )

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0, 0, 0, 0], [0, 0, 0, 0]),
    ), patch(
        "homeassistant.util.dt.utcnow",
        return_value=datetime(2020, 1, 1, 0, 0, 0),
    ):
        sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id="offset_timebase",
            net_heat_sensor="sensor.net_heat",
            price_sensor="sensor.price",
            device=DeviceInfo(identifiers={("test", "4")}),
            planning_window=2,
            time_base=30,
        )

        await sensor.async_update()

    assert len(sensor.extra_state_attributes["future_offsets"]) == 4
    assert len(sensor.extra_state_attributes["prices"]) == 4
