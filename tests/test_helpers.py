"""Test the helpers module."""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from homeassistant.core import State

from custom_components.heating_curve_optimizer.helpers import (
    _coerce_time_base,
    _normalize_price_value,
    _detect_interval_from_entries,
    extract_price_forecast_with_interval,
    extract_price_forecast,
    calculate_supply_temperature,
    calculate_defrost_factor,
)


# === Time Base Tests ===


def test_coerce_time_base_valid_integer():
    """Test coercing valid integer time base."""
    assert _coerce_time_base(60) == 60
    assert _coerce_time_base(30) == 30


def test_coerce_time_base_valid_float():
    """Test coercing valid float time base."""
    assert _coerce_time_base(60.0) == 60
    assert _coerce_time_base(30.7) == 31  # Rounds to nearest


def test_coerce_time_base_invalid():
    """Test coercing invalid time base."""
    assert _coerce_time_base(-5) is None
    assert _coerce_time_base(0) is None
    assert _coerce_time_base("invalid") is None
    assert _coerce_time_base(None) is None
    assert _coerce_time_base(float("nan")) is None


# === Price Normalization Tests ===


def test_normalize_price_value_float():
    """Test normalizing float price value."""
    assert _normalize_price_value(0.25) == 0.25
    assert _normalize_price_value(0.30) == 0.30


def test_normalize_price_value_string():
    """Test normalizing string price value."""
    assert _normalize_price_value("0.25") == 0.25
    assert _normalize_price_value("0.30") == 0.30


def test_normalize_price_value_dict():
    """Test normalizing dict price value."""
    assert _normalize_price_value({"value": 0.25}) == 0.25
    assert _normalize_price_value({"value": "0.30"}) == 0.30


def test_normalize_price_value_invalid():
    """Test normalizing invalid price value."""
    assert _normalize_price_value("invalid") is None
    assert _normalize_price_value({}) is None
    assert _normalize_price_value(None) is None


# === Interval Detection Tests ===


def test_detect_interval_hourly():
    """Test detecting hourly interval."""
    entries = [
        {"start": "2024-01-01T00:00:00+00:00", "value": 0.25},
        {"start": "2024-01-01T01:00:00+00:00", "value": 0.26},
        {"start": "2024-01-01T02:00:00+00:00", "value": 0.27},
    ]
    assert _detect_interval_from_entries(entries) == 60


def test_detect_interval_30min():
    """Test detecting 30-minute interval."""
    entries = [
        {"start": "2024-01-01T00:00:00+00:00", "value": 0.25},
        {"start": "2024-01-01T00:30:00+00:00", "value": 0.26},
        {"start": "2024-01-01T01:00:00+00:00", "value": 0.27},
    ]
    assert _detect_interval_from_entries(entries) == 30


def test_detect_interval_15min():
    """Test detecting 15-minute interval."""
    entries = [
        {"start": "2024-01-01T00:00:00+00:00", "value": 0.25},
        {"start": "2024-01-01T00:15:00+00:00", "value": 0.26},
        {"start": "2024-01-01T00:30:00+00:00", "value": 0.27},
    ]
    assert _detect_interval_from_entries(entries) == 15


def test_detect_interval_invalid():
    """Test detecting interval with invalid data."""
    assert _detect_interval_from_entries([]) == 60
    assert _detect_interval_from_entries(None) == 60
    assert _detect_interval_from_entries([{}]) == 60


def test_detect_interval_with_from_key():
    """Test detecting interval using 'from' key instead of 'start'."""
    entries = [
        {"from": "2024-01-01T00:00:00+00:00", "value": 0.25},
        {"from": "2024-01-01T01:00:00+00:00", "value": 0.26},
    ]
    assert _detect_interval_from_entries(entries) == 60


# === Price Forecast Extraction Tests ===


def test_extract_price_forecast_from_forecast_prices():
    """Test extracting forecast from forecast_prices attribute."""
    state = MagicMock(spec=State)
    state.attributes = {"forecast_prices": [0.20, 0.25, 0.30]}
    state.state = "0.25"

    prices, interval = extract_price_forecast_with_interval(state)
    assert prices == [0.20, 0.25, 0.30]
    assert interval == 60


def test_extract_price_forecast_from_net_prices():
    """Test extracting forecast from net_prices_today/tomorrow."""
    state = MagicMock(spec=State)
    state.attributes = {
        "net_prices_today": [
            {"start": "2024-01-01T12:00:00+00:00", "value": 0.20},
            {"start": "2024-01-01T13:00:00+00:00", "value": 0.25},
        ],
        "net_prices_tomorrow": [
            {"start": "2024-01-02T00:00:00+00:00", "value": 0.30},
        ],
    }
    state.state = "0.25"

    with patch("homeassistant.util.dt.utcnow") as mock_now:
        mock_now.return_value = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        prices, interval = extract_price_forecast_with_interval(state)
        assert len(prices) > 0
        assert interval == 60


def test_extract_price_forecast_from_raw_today_tomorrow():
    """Test extracting forecast from raw_today/raw_tomorrow."""
    state = MagicMock(spec=State)
    state.attributes = {
        "raw_today": [0.20, 0.21, 0.22, 0.23, 0.24, 0.25, 0.26],
        "raw_tomorrow": [0.27, 0.28],
    }
    state.state = "0.25"

    # Test with raw_today/raw_tomorrow
    # The function uses now.hour to slice raw_today
    # raw_today has 7 entries, raw_tomorrow has 2
    prices, interval = extract_price_forecast_with_interval(state)
    # Should get raw_today (all 7 elements starting from current hour) + raw_tomorrow (2 elements)
    assert len(prices) > 0
    assert interval == 60


def test_extract_price_forecast_fallback_to_state():
    """Test fallback to current state when no forecast available."""
    state = MagicMock(spec=State)
    state.attributes = {}
    state.state = "0.25"

    prices, interval = extract_price_forecast_with_interval(state)
    assert prices == [0.25]
    assert interval == 60


def test_extract_price_forecast_invalid_state():
    """Test handling invalid state value."""
    state = MagicMock(spec=State)
    state.attributes = {}
    state.state = "unavailable"

    prices, interval = extract_price_forecast_with_interval(state)
    assert prices == []
    assert interval == 60


def test_extract_price_forecast_wrapper():
    """Test extract_price_forecast wrapper function."""
    state = MagicMock(spec=State)
    state.attributes = {"forecast_prices": [0.20, 0.25, 0.30]}
    state.state = "0.25"

    prices = extract_price_forecast(state)
    assert prices == [0.20, 0.25, 0.30]


def test_extract_price_forecast_from_today_tomorrow():
    """Test extracting from today/tomorrow attributes."""
    state = MagicMock(spec=State)
    state.attributes = {
        "today": [0.20, 0.21, 0.22],
        "tomorrow": [0.23, 0.24],
    }
    state.state = "0.25"

    prices, interval = extract_price_forecast_with_interval(state)
    assert len(prices) == 5
    assert prices == [0.20, 0.21, 0.22, 0.23, 0.24]


# === Supply Temperature Calculation Tests ===


def test_calculate_supply_temperature_cold_outdoor():
    """Test supply temperature at minimum outdoor temp."""
    temp = calculate_supply_temperature(
        outdoor_temp=-10.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
    )
    assert temp == 50.0


def test_calculate_supply_temperature_warm_outdoor():
    """Test supply temperature at maximum outdoor temp."""
    temp = calculate_supply_temperature(
        outdoor_temp=15.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
    )
    assert temp == 25.0


def test_calculate_supply_temperature_mid_range():
    """Test supply temperature in mid-range."""
    temp = calculate_supply_temperature(
        outdoor_temp=2.5,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
    )
    # 2.5 is halfway between -10 and 15
    # Should give temp halfway between 50 and 25
    assert abs(temp - 37.5) < 0.1


def test_calculate_supply_temperature_below_min():
    """Test supply temperature below minimum outdoor temp."""
    temp = calculate_supply_temperature(
        outdoor_temp=-20.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
    )
    assert temp == 50.0


def test_calculate_supply_temperature_above_max():
    """Test supply temperature above maximum outdoor temp."""
    temp = calculate_supply_temperature(
        outdoor_temp=20.0,
        water_min=25.0,
        water_max=50.0,
        outdoor_min=-10.0,
        outdoor_max=15.0,
    )
    assert temp == 25.0


# === Defrost Factor Tests ===


def test_defrost_factor_warm_weather():
    """Test no defrost penalty above 6°C."""
    factor = calculate_defrost_factor(outdoor_temp=10.0, humidity=80.0)
    assert factor == 1.0

    factor = calculate_defrost_factor(outdoor_temp=6.0, humidity=80.0)
    assert factor == 1.0


def test_defrost_factor_extreme_cold():
    """Test no defrost penalty below -10°C."""
    factor = calculate_defrost_factor(outdoor_temp=-15.0, humidity=80.0)
    assert factor == 1.0

    factor = calculate_defrost_factor(outdoor_temp=-10.0, humidity=80.0)
    assert factor == 1.0


def test_defrost_factor_frosting_zone():
    """Test defrost penalty in frosting zone (0-6°C)."""
    factor = calculate_defrost_factor(outdoor_temp=2.0, humidity=80.0)
    assert 0.6 <= factor < 1.0  # Should have penalty

    factor = calculate_defrost_factor(outdoor_temp=0.0, humidity=80.0)
    assert 0.6 <= factor < 1.0  # Worst frosting zone


def test_defrost_factor_below_zero():
    """Test defrost penalty below zero but above -10°C."""
    factor = calculate_defrost_factor(outdoor_temp=-5.0, humidity=80.0)
    assert 0.6 <= factor < 1.0


def test_defrost_factor_humidity_effect():
    """Test humidity affects defrost factor."""
    low_humidity = calculate_defrost_factor(outdoor_temp=2.0, humidity=50.0)
    high_humidity = calculate_defrost_factor(outdoor_temp=2.0, humidity=90.0)

    # Higher humidity should give lower factor (more frosting)
    assert high_humidity < low_humidity


def test_defrost_factor_minimum():
    """Test defrost factor has minimum of 0.60."""
    # Even in worst conditions, should not go below 0.60
    factor = calculate_defrost_factor(outdoor_temp=0.0, humidity=100.0)
    assert factor >= 0.60


def test_defrost_factor_range_check():
    """Test defrost factor is always in valid range."""
    for temp in range(-20, 20):
        for humidity in range(50, 100, 10):
            factor = calculate_defrost_factor(
                outdoor_temp=float(temp), humidity=float(humidity)
            )
            assert 0.60 <= factor <= 1.0


# === Edge Cases ===


def test_extract_price_forecast_with_dict_values():
    """Test extracting forecast with dict-wrapped values."""
    state = MagicMock(spec=State)
    state.attributes = {
        "forecast_prices": [
            {"value": 0.20},
            {"value": 0.25},
            {"value": 0.30},
        ]
    }
    state.state = "0.25"

    prices, interval = extract_price_forecast_with_interval(state)
    assert prices == [0.20, 0.25, 0.30]


def test_extract_price_forecast_mixed_types():
    """Test extracting forecast with mixed value types."""
    state = MagicMock(spec=State)
    state.attributes = {"forecast_prices": [0.20, "0.25", {"value": 0.30}]}
    state.state = "0.25"

    prices, interval = extract_price_forecast_with_interval(state)
    assert prices == [0.20, 0.25, 0.30]


def test_extract_price_forecast_skips_invalid_values():
    """Test extracting forecast skips invalid values."""
    state = MagicMock(spec=State)
    state.attributes = {"forecast_prices": [0.20, "invalid", 0.30, None, 0.35]}
    state.state = "0.25"

    prices, interval = extract_price_forecast_with_interval(state)
    assert prices == [0.20, 0.30, 0.35]
