import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer import sensor
from custom_components.heating_curve_optimizer.const import DOMAIN
from custom_components.heating_curve_optimizer.sensor import (
    HeatingCurveOffsetSensor,
    NetHeatLossSensor,
    OutdoorTemperatureSensor,
)
from homeassistant.components.sensor import SensorStateClass

from datetime import datetime
from unittest.mock import patch


@pytest.mark.asyncio
async def test_offset_sensor_handles_sensor_instance(hass):
    hass.states.async_set("sensor.outdoor", "5")
    net = NetHeatLossSensor(
        hass=hass,
        name="Hourly Net Heat Loss",
        unique_id="test_net",
        area_m2=10.0,
        energy_label="A",
        indoor_sensor=None,
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
        outdoor_sensor="sensor.outdoor",
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
    hass.states.async_set("sensor.net_heat", "1", {"forecast": [0.5] * 6})
    hass.states.async_set(
        "sensor.price",
        "0.0",
        {"raw_today": [0.0] * 24, "raw_tomorrow": []},
    )
    hass.states.async_set("sensor.outdoor_temperature", "0")

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
        0.5,
        1.5,
        3.0,
        5.0,
        7.5,
        10.5,
    ]
    assert sensor.extra_state_attributes["buffer_evolution_offsets"] == [
        0,
        1,
        3,
        6,
        10,
        15,
    ]
    assert "future_supply_temperatures" in sensor.extra_state_attributes
    assert sensor.extra_state_attributes["time_base_minutes"] == 60
    assert sensor.extra_state_attributes["time_base_issues"] == []
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_offset_sensor_uses_solar_buffer(hass):
    hass.states.async_set(
        "sensor.net_heat",
        "1",
        {"forecast": [-1.0, 2.0, 1.0]},
    )
    hass.states.async_set(
        "sensor.price",
        "0.0",
        {"raw_today": [0.0] * 24, "raw_tomorrow": []},
    )
    hass.states.async_set("sensor.outdoor_temperature", "0")

    sensor = HeatingCurveOffsetSensor(
        hass=hass,
        name="Heating Curve Offset",
        unique_id="offset_solar",
        net_heat_sensor="sensor.net_heat",
        price_sensor="sensor.price",
        device=DeviceInfo(identifiers={("test", "solar")}),
        planning_window=3,
        time_base=60,
    )

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0, 0, 0], [0.0, 0.0, 0.0]),
    ) as mocked_optimize:
        await sensor.async_update()

    assert mocked_optimize.call_args is not None
    optimized_demand = mocked_optimize.call_args[0][0]
    assert optimized_demand == [0.0, 1.0, 1.0]

    attrs = sensor.extra_state_attributes
    assert attrs["raw_net_heat_forecast"][:3] == [-1.0, 2.0, 1.0]
    assert attrs["net_heat_forecast_after_solar"] == [0.0, 1.0, 1.0]
    assert attrs["solar_buffer_evolution"] == [1.0, 0.0, 0.0]
    assert attrs["solar_gain_available_kwh"] == 1.0
    assert attrs["solar_gain_remaining_kwh"] == 0.0

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
    hass.states.async_set("sensor.outdoor_temperature", "0")

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
    assert sensor.extra_state_attributes["time_base_minutes"] == 30
    assert sensor.extra_state_attributes["time_base_issues"] == []


@pytest.mark.asyncio
async def test_offset_sensor_uses_internal_outdoor_sensor(hass):
    hass.states.async_set("sensor.net_heat", "1", {"forecast": [0.0] * 6})
    hass.states.async_set(
        "sensor.price",
        "0.0",
        {"raw_today": [0.0] * 24, "raw_tomorrow": []},
    )

    outdoor_sensor = OutdoorTemperatureSensor(
        hass=hass,
        name="Outdoor Temperature",
        unique_id="outdoor_test",
        device=DeviceInfo(identifiers={("test", "outdoor")}),
    )
    outdoor_sensor.entity_id = "sensor.heating_curve_optimizer_outdoor_temperature"
    hass.states.async_set(outdoor_sensor.entity_id, "3.5")

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0] * 6, [0] * 6),
    ):
        sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id="offset_outdoor",
            net_heat_sensor="sensor.net_heat",
            price_sensor="sensor.price",
            device=DeviceInfo(identifiers={("test", "5")}),
            outdoor_sensor=outdoor_sensor,
        )

        await sensor.async_update()

    assert sensor.available is True
    assert sensor.extra_state_attributes["base_supply_temperature"] is not None


@pytest.mark.asyncio
async def test_offset_sensor_flags_time_base_mismatch(hass):
    hass.states.async_set(
        "sensor.net_heat",
        "1",
        {"forecast": [0.5] * 8, "forecast_time_base": 30},
    )
    hass.states.async_set(
        "sensor.price",
        "0.0",
        {"raw_today": [0.1] * 24, "raw_tomorrow": []},
    )
    hass.states.async_set("sensor.outdoor_temperature", "5")

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0] * 6, [0.0] * 6),
    ):
        sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id="offset_timebase_mismatch",
            net_heat_sensor="sensor.net_heat",
            price_sensor="sensor.price",
            device=DeviceInfo(identifiers={("test", "tbm")}),
        )

        await sensor.async_update()

    issues = sensor.extra_state_attributes["time_base_issues"]
    assert any("net_heat" in issue for issue in issues)


@pytest.mark.asyncio
async def test_offset_sensor_reports_no_positive_demand_reason(hass):
    hass.states.async_set(
        "sensor.net_heat",
        "1",
        {"forecast": [-0.2, -0.1, 0.0, -0.3, 0.0, -0.2]},
    )
    hass.states.async_set(
        "sensor.price",
        "0.0",
        {"raw_today": [0.0] * 24, "raw_tomorrow": []},
    )
    hass.states.async_set("sensor.outdoor_temperature", "5")

    sensor = HeatingCurveOffsetSensor(
        hass=hass,
        name="Heating Curve Offset",
        unique_id="offset_reason_demand",
        net_heat_sensor="sensor.net_heat",
        price_sensor="sensor.price",
        device=DeviceInfo(identifiers={("test", "reason1")}),
    )

    await sensor.async_update()

    attrs = sensor.extra_state_attributes
    assert all(v == 0 for v in attrs["future_offsets"])
    assert "totale warmtevraag" in attrs["optimization_status"]


@pytest.mark.asyncio
async def test_offset_sensor_reports_no_offset_range_reason(hass):
    hass.states.async_set(
        "sensor.net_heat",
        "1",
        {"forecast": [0.5] * 6},
    )
    hass.states.async_set(
        "sensor.price",
        "0.0",
        {"raw_today": [0.0] * 24, "raw_tomorrow": []},
    )
    hass.states.async_set("sensor.outdoor_temperature", "5")

    hass.data.setdefault(DOMAIN, {})["heat_curve_min"] = 30.0
    hass.data[DOMAIN]["heat_curve_max"] = 30.0
    hass.data[DOMAIN]["heat_curve_min_outdoor"] = -20.0
    hass.data[DOMAIN]["heat_curve_max_outdoor"] = 15.0

    sensor = HeatingCurveOffsetSensor(
        hass=hass,
        name="Heating Curve Offset",
        unique_id="offset_reason_range",
        net_heat_sensor="sensor.net_heat",
        price_sensor="sensor.price",
        device=DeviceInfo(identifiers={("test", "reason2")}),
    )

    await sensor.async_update()

    attrs = sensor.extra_state_attributes
    assert all(v == 0 for v in attrs["future_offsets"])
    assert "stooklijn" in attrs["optimization_status"]
