import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from custom_components.heating_curve_optimizer.sensor import (
    CurrentElectricityPriceSensor,
)


@pytest.mark.asyncio
async def test_price_sensor_updates_value(hass):
    hass.states.async_set("sensor.price", "0.123")
    sensor = CurrentElectricityPriceSensor(
        hass=hass,
        name="Electricity Price",
        unique_id="price1",
        price_sensor="sensor.price",
        source_type="Electricity consumption",
        price_settings={},
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    await sensor.async_update()
    assert sensor.native_value == 0.123
    assert sensor.available is True
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_price_sensor_unavailable_state(hass):
    hass.states.async_set("sensor.price", "unavailable")
    sensor = CurrentElectricityPriceSensor(
        hass=hass,
        name="Electricity Price",
        unique_id="price2",
        price_sensor="sensor.price",
        source_type="Electricity consumption",
        price_settings={},
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "2")}),
    )
    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_price_sensor_has_measurement_state_class(hass):
    sensor = CurrentElectricityPriceSensor(
        hass=hass,
        name="Electricity Price",
        unique_id="price3",
        price_sensor="sensor.price",
        source_type="Electricity consumption",
        price_settings={},
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "3")}),
    )
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_price_change_updates_sensor_state(hass):
    """Verify that changes in the price sensor update the entity state."""
    hass.states.async_set("sensor.price", "0.1")
    sensor = CurrentElectricityPriceSensor(
        hass=hass,
        name="Electricity Price",
        unique_id="price4",
        price_sensor="sensor.price",
        source_type="Electricity consumption",
        price_settings={},
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "4")}),
    )
    await sensor.async_added_to_hass()
    await sensor.async_update()
    assert sensor.native_value == 0.1

    hass.states.async_set("sensor.price", "0.2")
    await hass.async_block_till_done()
    assert sensor.native_value == 0.2
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_price_sensor_uses_net_price_forecast(hass, monkeypatch):
    now = dt_util.parse_datetime("2025-10-07T05:30:00+02:00")
    assert now is not None
    monkeypatch.setattr(dt_util, "utcnow", lambda: dt_util.as_utc(now))

    hass.states.async_set(
        "sensor.price",
        "0.25",
        {
            "net_prices_today": [
                {
                    "start": "2025-10-07T04:45:00+02:00",
                    "end": "2025-10-07T05:00:00+02:00",
                    "value": 0.18,
                },
                {
                    "start": "2025-10-07T05:30:00+02:00",
                    "end": "2025-10-07T05:45:00+02:00",
                    "value": 0.2,
                },
            ],
            "net_prices_tomorrow": [
                {
                    "start": "2025-10-08T00:00:00+02:00",
                    "end": "2025-10-08T00:15:00+02:00",
                    "value": 0.3,
                },
                {
                    "start": "2025-10-08T00:15:00+02:00",
                    "end": "2025-10-08T00:30:00+02:00",
                    "value": 0.4,
                },
            ],
        },
    )

    sensor = CurrentElectricityPriceSensor(
        hass=hass,
        name="Electricity Price",
        unique_id="price5",
        price_sensor="sensor.price",
        source_type="Electricity consumption",
        price_settings={},
        icon="mdi:test",
        device=DeviceInfo(identifiers={("test", "5")}),
    )

    await sensor.async_update()

    attrs = sensor.extra_state_attributes
    assert attrs["forecast_prices"] == [0.2, 0.3, 0.4]
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_detect_interval_from_entries_15min():
    """Test interval detection from 15-minute price entries."""
    from custom_components.heating_curve_optimizer.sensor import (
        _detect_interval_from_entries,
    )

    entries = [
        {"start": "2025-10-07T00:00:00+02:00", "value": 0.1},
        {"start": "2025-10-07T00:15:00+02:00", "value": 0.2},
        {"start": "2025-10-07T00:30:00+02:00", "value": 0.3},
    ]
    assert _detect_interval_from_entries(entries) == 15


@pytest.mark.asyncio
async def test_detect_interval_from_entries_30min():
    """Test interval detection from 30-minute price entries."""
    from custom_components.heating_curve_optimizer.sensor import (
        _detect_interval_from_entries,
    )

    entries = [
        {"start": "2025-10-07T00:00:00+02:00", "value": 0.1},
        {"start": "2025-10-07T00:30:00+02:00", "value": 0.2},
        {"start": "2025-10-07T01:00:00+02:00", "value": 0.3},
    ]
    assert _detect_interval_from_entries(entries) == 30


@pytest.mark.asyncio
async def test_detect_interval_from_entries_60min():
    """Test interval detection from 60-minute price entries."""
    from custom_components.heating_curve_optimizer.sensor import (
        _detect_interval_from_entries,
    )

    entries = [
        {"start": "2025-10-07T00:00:00+02:00", "value": 0.1},
        {"start": "2025-10-07T01:00:00+02:00", "value": 0.2},
        {"start": "2025-10-07T02:00:00+02:00", "value": 0.3},
    ]
    assert _detect_interval_from_entries(entries) == 60


@pytest.mark.asyncio
async def test_detect_interval_from_entries_defaults_to_60():
    """Test interval detection defaults to 60 when not detected."""
    from custom_components.heating_curve_optimizer.sensor import (
        _detect_interval_from_entries,
    )

    # No timestamps
    entries = [{"value": 0.1}, {"value": 0.2}]
    assert _detect_interval_from_entries(entries) == 60

    # Empty list
    assert _detect_interval_from_entries([]) == 60

    # Single entry
    assert _detect_interval_from_entries([{"start": "2025-10-07T00:00:00+02:00"}]) == 60


@pytest.mark.asyncio
async def test_extract_price_forecast_with_interval_detects_15min(hass, monkeypatch):
    """Test price extraction detects 15-minute intervals."""
    from homeassistant.core import State

    from custom_components.heating_curve_optimizer.sensor import (
        extract_price_forecast_with_interval,
    )

    now = dt_util.parse_datetime("2025-10-07T00:00:00+02:00")
    assert now is not None
    monkeypatch.setattr(dt_util, "utcnow", lambda: dt_util.as_utc(now))

    state = State(
        "sensor.price",
        "0.25",
        {
            "net_prices_today": [
                {"start": "2025-10-07T00:00:00+02:00", "value": 0.1},
                {"start": "2025-10-07T00:15:00+02:00", "value": 0.2},
                {"start": "2025-10-07T00:30:00+02:00", "value": 0.3},
                {"start": "2025-10-07T00:45:00+02:00", "value": 0.4},
            ],
        },
    )

    prices, interval = extract_price_forecast_with_interval(state)
    assert interval == 15
    assert prices == [0.1, 0.2, 0.3, 0.4]


@pytest.mark.asyncio
async def test_extract_price_forecast_with_interval_hourly_default(hass):
    """Test price extraction returns 60 for hourly data."""
    from homeassistant.core import State

    from custom_components.heating_curve_optimizer.sensor import (
        extract_price_forecast_with_interval,
    )

    state = State(
        "sensor.price",
        "0.25",
        {
            "raw_today": [0.1, 0.2, 0.3],
        },
    )

    prices, interval = extract_price_forecast_with_interval(state)
    assert interval == 60
    assert prices == [0.1, 0.2, 0.3]
