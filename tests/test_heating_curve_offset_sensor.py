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
    # NetHeatLossSensor now only needs heat_loss_sensor and window_gain_sensor
    from types import SimpleNamespace

    heat_loss = SimpleNamespace(
        native_value=1.0, extra_state_attributes={"forecast": [1.0] * 6}
    )
    net = NetHeatLossSensor(
        hass=hass,
        name="Net Heat Loss",
        unique_id="test_net",
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
        heat_loss_sensor=heat_loss,
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
        0.075,
        0.225,
        0.45,
        0.75,
        1.125,
        1.575,
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

    hass.data.setdefault(DOMAIN, {})["runtime"] = {
        "heat_curve_min": 30.0,
        "heat_curve_max": 30.0,
        "heat_curve_min_outdoor": -20.0,
        "heat_curve_max_outdoor": 15.0,
    }

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


@pytest.mark.asyncio
async def test_offset_sensor_resamples_15min_to_60min_by_averaging(hass):
    """Test that 15-minute price intervals are averaged correctly to 60-minute intervals."""
    # Real user data: 15-minute intervals that should be averaged to hourly
    # 18:00-19:00: [0.2790986, 0.2823051, 0.2778039, 0.2709311] → avg ≈ 0.2775
    # 19:00-20:00: [0.2730486, 0.2649416, 0.2714877, 0.2636953] → avg ≈ 0.2683
    # 20:00-21:00: [0.2632234, 0.2622191, 0.2520551, 0.2439965] → avg ≈ 0.2554
    # 21:00-22:00: [0.2578026, 0.2555399, 0.2541484, 0.2488365] → avg ≈ 0.2541
    fifteen_min_prices = [
        {"value": 0.2790986},
        {"value": 0.2823051},
        {"value": 0.2778039},
        {"value": 0.2709311},
        {"value": 0.2730486},
        {"value": 0.2649416},
        {"value": 0.2714877},
        {"value": 0.2636953},
        {"value": 0.2632234},
        {"value": 0.2622191},
        {"value": 0.2520551},
        {"value": 0.2439965},
        {"value": 0.2578026},
        {"value": 0.2555399},
        {"value": 0.2541484},
        {"value": 0.2488365},
    ]

    hass.states.async_set(
        "sensor.net_heat",
        "1",
        {"forecast": [1.0, 1.0, 1.0, 1.0], "forecast_time_base": 60},
    )
    hass.states.async_set(
        "sensor.price",
        "0.25",
        {"forecast": fifteen_min_prices, "forecast_time_base": 15},
    )
    hass.states.async_set("sensor.outdoor_temperature", "5")

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0, 0, 0, 0], [0, 0, 0, 0]),
    ):
        sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id="offset_resample_15min",
            net_heat_sensor="sensor.net_heat",
            price_sensor="sensor.price",
            device=DeviceInfo(identifiers={("test", "resample15")}),
            planning_window=4,
            time_base=60,
        )

        await sensor.async_update()

    prices = sensor.extra_state_attributes["prices"]
    # Verify that prices are averaged correctly
    assert len(prices) == 4
    assert abs(prices[0] - 0.2775) < 0.001  # 18:00-19:00 avg
    assert abs(prices[1] - 0.2683) < 0.001  # 19:00-20:00 avg
    assert abs(prices[2] - 0.2554) < 0.001  # 20:00-21:00 avg
    assert abs(prices[3] - 0.2541) < 0.001  # 21:00-22:00 avg


@pytest.mark.asyncio
async def test_offset_sensor_uses_production_prices_when_net_producing(hass):
    """Test that production prices are used when household is net producing energy."""
    # Setup: Net production scenario (production > consumption)
    # Production: 3.0 kW, Baseline consumption: 1.0 kW → Net balance: +2.0 kW
    hass.states.async_set(
        "sensor.net_heat",
        "1",
        {"forecast": [1.0, 1.0, 1.0, 1.0], "forecast_time_base": 60},
    )
    # Consumption prices: 0.30 EUR/kWh
    hass.states.async_set(
        "sensor.consumption_price",
        "0.30",
        {"forecast": [{"value": 0.30}] * 4, "forecast_time_base": 60},
    )
    # Production prices: 0.10 EUR/kWh (much lower, as typical)
    hass.states.async_set(
        "sensor.production_price",
        "0.10",
        {"forecast": [{"value": 0.10}] * 4, "forecast_time_base": 60},
    )
    # Production: 3.0 kW (net producing)
    hass.states.async_set("sensor.pv_power", "3000")  # 3000 W = 3.0 kW
    # Baseline consumption: 1.0 kW
    hass.states.async_set("sensor.home_power", "1000")  # 1000 W = 1.0 kW
    hass.states.async_set("sensor.outdoor_temperature", "5")

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0, 0, 0, 0], [0, 0, 0, 0]),
    ):
        sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id="offset_production_price",
            net_heat_sensor="sensor.net_heat",
            price_sensor="sensor.consumption_price",
            production_price_sensor="sensor.production_price",
            device=DeviceInfo(identifiers={("test", "prod_price")}),
            planning_window=4,
            time_base=60,
            production_sensors=["sensor.pv_power"],
            consumption_sensors=["sensor.home_power"],
        )

        await sensor.async_update()

    attrs = sensor.extra_state_attributes
    # Should use production prices because net balance > 0
    assert attrs["prices"] == [0.10, 0.10, 0.10, 0.10]
    assert attrs["consumption_prices"] == [0.30, 0.30, 0.30, 0.30]
    assert attrs["production_prices"] == [0.10, 0.10, 0.10, 0.10]
    assert all(source == "production" for source in attrs["price_source"])
    # Net balance should be positive (production - consumption)
    assert all(balance > 0 for balance in attrs["net_balance_kw"])


@pytest.mark.asyncio
async def test_offset_sensor_uses_consumption_prices_when_net_consuming(hass):
    """Test that consumption prices are used when household is net consuming energy."""
    # Setup: Net consumption scenario (consumption > production)
    # Production: 0.5 kW, Baseline consumption: 2.0 kW → Net balance: -1.5 kW
    hass.states.async_set(
        "sensor.net_heat",
        "1",
        {"forecast": [1.0, 1.0, 1.0, 1.0], "forecast_time_base": 60},
    )
    hass.states.async_set(
        "sensor.consumption_price",
        "0.30",
        {"forecast": [{"value": 0.30}] * 4, "forecast_time_base": 60},
    )
    hass.states.async_set(
        "sensor.production_price",
        "0.10",
        {"forecast": [{"value": 0.10}] * 4, "forecast_time_base": 60},
    )
    hass.states.async_set("sensor.pv_power", "500")  # 500 W = 0.5 kW
    hass.states.async_set("sensor.home_power", "2000")  # 2000 W = 2.0 kW
    hass.states.async_set("sensor.outdoor_temperature", "5")

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0, 0, 0, 0], [0, 0, 0, 0]),
    ):
        sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id="offset_consumption_price",
            net_heat_sensor="sensor.net_heat",
            price_sensor="sensor.consumption_price",
            production_price_sensor="sensor.production_price",
            device=DeviceInfo(identifiers={("test", "cons_price")}),
            planning_window=4,
            time_base=60,
            production_sensors=["sensor.pv_power"],
            consumption_sensors=["sensor.home_power"],
        )

        await sensor.async_update()

    attrs = sensor.extra_state_attributes
    # Should use consumption prices because net balance < 0
    assert attrs["prices"] == [0.30, 0.30, 0.30, 0.30]
    assert attrs["consumption_prices"] == [0.30, 0.30, 0.30, 0.30]
    assert attrs["production_prices"] == [0.10, 0.10, 0.10, 0.10]
    assert all(source == "consumption" for source in attrs["price_source"])
    # Net balance should be negative (consumption > production)
    assert all(balance < 0 for balance in attrs["net_balance_kw"])


@pytest.mark.asyncio
async def test_offset_sensor_mixed_production_consumption_scenario(hass):
    """Test dynamic price selection when net balance varies across forecast."""
    # Setup: Mixed scenario - net producing early, net consuming later
    hass.states.async_set(
        "sensor.net_heat",
        "1",
        {"forecast": [1.0, 1.0, 1.0, 1.0], "forecast_time_base": 60},
    )
    # Varying consumption prices
    hass.states.async_set(
        "sensor.consumption_price",
        "0.30",
        {
            "forecast": [
                {"value": 0.25},
                {"value": 0.30},
                {"value": 0.35},
                {"value": 0.40},
            ],
            "forecast_time_base": 60,
        },
    )
    # Production prices (lower)
    hass.states.async_set(
        "sensor.production_price",
        "0.10",
        {
            "forecast": [
                {"value": 0.08},
                {"value": 0.10},
                {"value": 0.12},
                {"value": 0.14},
            ],
            "forecast_time_base": 60,
        },
    )
    # Production forecast (high early, low later)
    hass.states.async_set(
        "sensor.pv_power",
        "3000",
        {"forecast": [3.0, 2.0, 1.0, 0.5], "forecast_time_base": 60},
    )
    # Baseline consumption (constant)
    hass.states.async_set("sensor.home_power", "1500")  # 1.5 kW constant
    hass.states.async_set("sensor.outdoor_temperature", "5")

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0, 0, 0, 0], [0, 0, 0, 0]),
    ):
        sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id="offset_mixed",
            net_heat_sensor="sensor.net_heat",
            price_sensor="sensor.consumption_price",
            production_price_sensor="sensor.production_price",
            device=DeviceInfo(identifiers={("test", "mixed")}),
            planning_window=4,
            time_base=60,
            production_sensors=["sensor.pv_power"],
            consumption_sensors=["sensor.home_power"],
        )

        await sensor.async_update()

    attrs = sensor.extra_state_attributes
    # Step 0: 3.0 - 1.5 = +1.5 kW (producing) → use production price 0.08
    # Step 1: 2.0 - 1.5 = +0.5 kW (producing) → use production price 0.10
    # Step 2: 1.0 - 1.5 = -0.5 kW (consuming) → use consumption price 0.35
    # Step 3: 0.5 - 1.5 = -1.0 kW (consuming) → use consumption price 0.40
    expected_prices = [0.08, 0.10, 0.35, 0.40]
    expected_sources = ["production", "production", "consumption", "consumption"]

    assert attrs["prices"] == expected_prices
    assert attrs["price_source"] == expected_sources
    assert attrs["net_balance_kw"][0] > 0  # Producing
    assert attrs["net_balance_kw"][1] > 0  # Producing
    assert attrs["net_balance_kw"][2] < 0  # Consuming
    assert attrs["net_balance_kw"][3] < 0  # Consuming


@pytest.mark.asyncio
async def test_offset_sensor_resamples_15min_prices_to_hourly(hass, monkeypatch):
    """Test that 15-minute prices are correctly averaged into hourly prices."""
    from homeassistant.util import dt as dt_util

    now = dt_util.parse_datetime("2025-10-07T00:00:00+02:00")
    assert now is not None
    monkeypatch.setattr(dt_util, "utcnow", lambda: dt_util.as_utc(now))

    hass.states.async_set("sensor.net_heat", "1", {"forecast": [0.5] * 6})
    # 15-minute prices: 4 values per hour that should be averaged
    # Hour 0: 0.10, 0.20, 0.30, 0.40 → average = 0.25
    # Hour 1: 0.12, 0.14, 0.16, 0.18 → average = 0.15
    # Hour 2: 0.20, 0.20, 0.20, 0.20 → average = 0.20
    # Hour 3: 0.08, 0.12, 0.08, 0.12 → average = 0.10
    # Hour 4: 0.30, 0.30, 0.30, 0.30 → average = 0.30
    # Hour 5: 0.50, 0.50, 0.50, 0.50 → average = 0.50
    hass.states.async_set(
        "sensor.price",
        "0.1",
        {
            "net_prices_today": [
                {"start": "2025-10-07T00:00:00+02:00", "value": 0.10},
                {"start": "2025-10-07T00:15:00+02:00", "value": 0.20},
                {"start": "2025-10-07T00:30:00+02:00", "value": 0.30},
                {"start": "2025-10-07T00:45:00+02:00", "value": 0.40},
                {"start": "2025-10-07T01:00:00+02:00", "value": 0.12},
                {"start": "2025-10-07T01:15:00+02:00", "value": 0.14},
                {"start": "2025-10-07T01:30:00+02:00", "value": 0.16},
                {"start": "2025-10-07T01:45:00+02:00", "value": 0.18},
                {"start": "2025-10-07T02:00:00+02:00", "value": 0.20},
                {"start": "2025-10-07T02:15:00+02:00", "value": 0.20},
                {"start": "2025-10-07T02:30:00+02:00", "value": 0.20},
                {"start": "2025-10-07T02:45:00+02:00", "value": 0.20},
                {"start": "2025-10-07T03:00:00+02:00", "value": 0.08},
                {"start": "2025-10-07T03:15:00+02:00", "value": 0.12},
                {"start": "2025-10-07T03:30:00+02:00", "value": 0.08},
                {"start": "2025-10-07T03:45:00+02:00", "value": 0.12},
                {"start": "2025-10-07T04:00:00+02:00", "value": 0.30},
                {"start": "2025-10-07T04:15:00+02:00", "value": 0.30},
                {"start": "2025-10-07T04:30:00+02:00", "value": 0.30},
                {"start": "2025-10-07T04:45:00+02:00", "value": 0.30},
                {"start": "2025-10-07T05:00:00+02:00", "value": 0.50},
                {"start": "2025-10-07T05:15:00+02:00", "value": 0.50},
                {"start": "2025-10-07T05:30:00+02:00", "value": 0.50},
                {"start": "2025-10-07T05:45:00+02:00", "value": 0.50},
            ],
        },
    )
    hass.states.async_set("sensor.outdoor_temperature", "5")

    with patch(
        "custom_components.heating_curve_optimizer.sensor._optimize_offsets",
        return_value=([0] * 6, [0.0] * 6),
    ):
        sensor_obj = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id="offset_resample",
            net_heat_sensor="sensor.net_heat",
            price_sensor="sensor.price",
            device=DeviceInfo(identifiers={("test", "resample")}),
        )

        await sensor_obj.async_update()

    attrs = sensor_obj.extra_state_attributes
    expected_prices = [0.25, 0.15, 0.20, 0.10, 0.30, 0.50]
    # Check that prices are correctly averaged
    assert len(attrs["prices"]) == 6
    for i, (actual, expected) in enumerate(zip(attrs["prices"], expected_prices)):
        assert (
            abs(actual - expected) < 0.001
        ), f"Hour {i}: expected {expected}, got {actual}"

    await sensor_obj.async_will_remove_from_hass()
