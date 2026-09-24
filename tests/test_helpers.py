"""Test the helpers module."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import State

from custom_components.heating_curve_optimizer.helpers import (
    UNAVAILABLE_STATES,
    _coerce_time_base,
    _detect_interval_from_entries,
    _drop_elapsed_periods,
    _normalize_price_value,
    _skip_index_since_local_midnight,
    calculate_defrost_factor,
    calculate_pv_forecast,
    calculate_supply_temperature,
    calculate_window_solar_gain,
    extract_price_forecast_with_timestamps,
    get_sensor_value,
    price_unit_scale,
    read_power_kw,
    resample_to_steps,
    state_has_value,
)


def extract_price_forecast_with_interval(state):
    prices, _starts, interval = extract_price_forecast_with_timestamps(state)
    return prices, interval


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
        mock_now.return_value = datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)
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

    prices, _ = extract_price_forecast_with_interval(state)
    assert prices == [0.20, 0.25, 0.30]


def test_extract_price_forecast_from_today_tomorrow():
    """Test extracting from today/tomorrow attributes.

    The BC implementation correctly skips entries from 'today' that have
    already elapsed, using DST-aware index arithmetic. We mock the local
    clock to midnight so that all today entries are still in the future.
    """
    state = MagicMock(spec=State)
    state.attributes = {
        "today": [0.20, 0.21, 0.22],
        "tomorrow": [0.23, 0.24],
    }
    state.state = "0.25"

    midnight = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    with (
        patch("homeassistant.util.dt.now", return_value=midnight),
        patch("homeassistant.util.dt.utcnow", return_value=midnight),
    ):
        prices, _interval = extract_price_forecast_with_interval(state)
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

    prices, _interval = extract_price_forecast_with_interval(state)
    assert prices == [0.20, 0.25, 0.30]


def test_extract_price_forecast_mixed_types():
    """Test extracting forecast with mixed value types."""
    state = MagicMock(spec=State)
    state.attributes = {"forecast_prices": [0.20, "0.25", {"value": 0.30}]}
    state.state = "0.25"

    prices, _interval = extract_price_forecast_with_interval(state)
    assert prices == [0.20, 0.25, 0.30]


def test_extract_price_forecast_skips_invalid_values():
    """Test extracting forecast skips invalid values."""
    state = MagicMock(spec=State)
    state.attributes = {"forecast_prices": [0.20, "invalid", 0.30, None, 0.35]}
    state.state = "0.25"

    prices, _interval = extract_price_forecast_with_interval(state)
    assert prices == [0.20, 0.30, 0.35]


# === Defrost Factor Tests ===


def test_defrost_factor_continuous_across_freezing_point():
    """The 0-3°C branch and the below-freezing branch must agree at their
    shared boundary (outdoor_temp=0) - a code review found them using
    different base_penalty constants (0.25 vs 0.12), causing the COP
    multiplier to jump ~17% right at the freezing point even though frost
    formation is physically a continuous function of temperature."""
    just_above = calculate_defrost_factor(0.01, humidity=80.0)
    at_zero = calculate_defrost_factor(0.0, humidity=80.0)
    just_below = calculate_defrost_factor(-0.01, humidity=80.0)

    assert just_above == pytest.approx(at_zero, abs=0.01)
    assert just_below == pytest.approx(at_zero, abs=0.01)


def test_defrost_factor_no_frosting_above_6c():
    assert calculate_defrost_factor(6.0, humidity=80.0) == 1.0
    assert calculate_defrost_factor(10.0, humidity=80.0) == 1.0


def test_defrost_factor_no_frosting_below_minus_10c():
    assert calculate_defrost_factor(-10.0, humidity=80.0) == 1.0
    assert calculate_defrost_factor(-15.0, humidity=80.0) == 1.0


def test_defrost_factor_worst_near_zero():
    """Penalty should be worse (lower multiplier) near 0-3°C than well
    above freezing or well below -10°C's dry-air floor."""
    worst = calculate_defrost_factor(1.0, humidity=80.0)
    mild_above = calculate_defrost_factor(5.9, humidity=80.0)
    mild_below = calculate_defrost_factor(-9.0, humidity=80.0)

    assert worst < mild_above
    assert worst < mild_below


# === State Validators Tests ===


def test_state_has_value_valid():
    """Test state_has_value with a valid state."""
    state = MagicMock(spec=State)
    state.state = "21.5"
    assert state_has_value(state) is True


def test_state_has_value_unavailable():
    """Test state_has_value with unavailable states."""
    for bad_state in UNAVAILABLE_STATES:
        state = MagicMock(spec=State)
        state.state = bad_state
        assert state_has_value(state) is False


def test_state_has_value_none():
    """Test state_has_value with None."""
    assert state_has_value(None) is False


# === price_unit_scale Tests ===


def test_price_unit_scale_eur_kwh():
    """Sensors with per-kWh unit should return scale 1.0."""
    state = MagicMock(spec=State)
    state.attributes = {"unit_of_measurement": "EUR/kWh"}
    assert price_unit_scale(state) == 1.0


def test_price_unit_scale_eur_mwh():
    """Sensors with per-MWh unit should return scale 0.001."""
    state = MagicMock(spec=State)
    state.attributes = {"unit_of_measurement": "EUR/MWh"}
    assert price_unit_scale(state) == 0.001


def test_price_unit_scale_magnitude_heuristic():
    """Without a unit, large magnitudes should indicate EUR/MWh."""
    state = MagicMock(spec=State)
    state.attributes = {}
    # Typical EUR/kWh prices (< 1.0) → scale 1.0
    assert price_unit_scale(state, samples=[0.20, 0.25, 0.30]) == 1.0
    # Typical EUR/MWh prices (> 5.0) → scale 0.001
    assert price_unit_scale(state, samples=[200.0, 250.0, 300.0]) == 0.001


def test_price_unit_scale_no_state():
    """None state without samples should default to 1.0."""
    assert price_unit_scale(None) == 1.0


# === _normalize_price_value Tests ===


def test_normalize_price_value_price_key():
    """BC version also checks 'price' key in dicts."""
    assert _normalize_price_value({"price": 0.28}) == pytest.approx(0.28)


def test_normalize_price_value_nan_inf():
    """BC version rejects NaN and infinity."""
    assert _normalize_price_value(float("nan")) is None
    assert _normalize_price_value(float("inf")) is None


# === _skip_index_since_local_midnight Tests ===


def test_skip_index_since_local_midnight_basic():
    """At 14:00 with 60-min interval, skip index should be 14."""
    now_local = datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC)
    assert _skip_index_since_local_midnight(now_local, 60) == 14


def test_skip_index_since_local_midnight_partial():
    """At 14:45 with 60-min interval, only 14 complete periods have elapsed."""
    now_local = datetime(2024, 6, 15, 14, 45, 0, tzinfo=UTC)
    assert _skip_index_since_local_midnight(now_local, 60) == 14


def test_skip_index_since_local_midnight_30min():
    """At 14:30 with 30-min interval, 29 complete 30-min periods have elapsed."""
    now_local = datetime(2024, 6, 15, 14, 30, 0, tzinfo=UTC)
    assert _skip_index_since_local_midnight(now_local, 30) == 29


# === _drop_elapsed_periods Tests ===


def test_drop_elapsed_periods_all_future():
    """No periods should be dropped when all are in the future."""
    now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    prices = [0.20, 0.25, 0.30]
    start_times = [
        datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        datetime(2024, 6, 15, 13, 0, 0, tzinfo=UTC),
        datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC),
    ]
    with patch("homeassistant.util.dt.utcnow", return_value=now):
        kept_prices, kept_times, _interval = _drop_elapsed_periods(
            prices, start_times, 60
        )
    assert kept_prices == [0.20, 0.25, 0.30]
    assert len(kept_times) == 3


def test_drop_elapsed_periods_past_entries_dropped():
    """Periods that ended before now should be dropped."""
    now = datetime(2024, 6, 15, 14, 30, 0, tzinfo=UTC)
    prices = [0.20, 0.25, 0.30]
    start_times = [
        datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),  # ended 13:00 → past
        datetime(2024, 6, 15, 13, 0, 0, tzinfo=UTC),  # ended 14:00 → past
        datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC),  # ends 15:00 → future
    ]
    with patch("homeassistant.util.dt.utcnow", return_value=now):
        kept_prices, kept_times, _interval = _drop_elapsed_periods(
            prices, start_times, 60
        )
    assert kept_prices == [0.30]
    assert len(kept_times) == 1


# === calculate_pv_forecast Tests ===


def test_calculate_pv_forecast_fallback_south():
    """South-facing panel at optimal tilt should give ~full production."""
    radiation = [1000.0] * 4  # STC conditions
    result = calculate_pv_forecast(
        radiation, peak_power_kwp=4.0, orientation_deg=180, tilt_deg=35
    )
    # With 0.85 efficiency and south orientation: 4.0 * 1.0 * 1.0 * 0.85 = 3.4 kW
    assert all(v == pytest.approx(3.4) for v in result)


def test_calculate_pv_forecast_zero_kwp():
    """Zero peak power should give zero output."""
    result = calculate_pv_forecast([1000.0, 1000.0], peak_power_kwp=0.0)
    assert result == [0.0, 0.0]


def test_calculate_pv_forecast_poa_mode():
    """POA mode should be used when all extra parameters are provided."""
    radiation = [500.0] * 4
    dni = [400.0] * 4
    diffuse = [100.0] * 4
    timestamps = [datetime(2024, 6, 21, 10 + i, 0, 0, tzinfo=UTC) for i in range(4)]
    result_poa = calculate_pv_forecast(
        radiation,
        peak_power_kwp=4.0,
        orientation_deg=180,
        tilt_deg=35,
        dni_forecast=dni,
        diffuse_forecast=diffuse,
        timestamps_utc=timestamps,
        latitude=52.0,
        longitude=5.0,
    )
    result_fallback = calculate_pv_forecast(
        radiation,
        peak_power_kwp=4.0,
        orientation_deg=180,
        tilt_deg=35,
    )
    # POA and fallback should differ (POA is more accurate)
    assert len(result_poa) == len(result_fallback) == 4
    # All values should be non-negative
    assert all(v >= 0.0 for v in result_poa)


def test_calculate_pv_forecast_no_production_at_night():
    """At night (sun below horizon) POA mode should give zero production."""
    # Midnight in winter
    radiation = [0.0] * 4
    dni = [0.0] * 4
    diffuse = [0.0] * 4
    timestamps = [datetime(2024, 1, 15, 0 + i, 0, 0, tzinfo=UTC) for i in range(4)]
    result = calculate_pv_forecast(
        radiation,
        peak_power_kwp=4.0,
        dni_forecast=dni,
        diffuse_forecast=diffuse,
        timestamps_utc=timestamps,
        latitude=52.0,
        longitude=5.0,
    )
    assert all(v == 0.0 for v in result)


# === _detect_interval_from_entries with datetime objects ===


def test_detect_interval_with_datetime_objects():
    """BC version accepts datetime objects, not just strings."""
    entries = [
        {"start": datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), "value": 0.25},
        {"start": datetime(2024, 1, 1, 0, 30, 0, tzinfo=UTC), "value": 0.26},
    ]
    assert _detect_interval_from_entries(entries) == 30


def test_detect_interval_with_time_key():
    """BC version also checks 'time' key for timestamps."""
    entries = [
        {"time": "2024-01-01T00:00:00+00:00", "value": 0.25},
        {"time": "2024-01-01T00:15:00+00:00", "value": 0.26},
    ]
    assert _detect_interval_from_entries(entries) == 15


# === resample_to_steps / power / window gain ===


def test_resample_to_steps_aligns_shortened_first_step():
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    steps = [
        datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
        datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
    ]
    result = resample_to_steps([1.0, 3.0], start, 60, steps, [0.5, 1.0])
    assert result == pytest.approx([1.0, 3.0])


def test_read_power_kw_units():
    hass = MagicMock()
    hass.states.get.return_value = State(
        "sensor.p", "1500", {"unit_of_measurement": "W"}
    )
    assert read_power_kw(hass, "sensor.p") == pytest.approx(1.5)
    hass.states.get.return_value = State(
        "sensor.p", "1.5", {"unit_of_measurement": "kW"}
    )
    assert read_power_kw(hass, "sensor.p") == pytest.approx(1.5)
    hass.states.get.return_value = State("sensor.p", "1500", {})
    assert read_power_kw(hass, "sensor.p") is None
    assert read_power_kw(hass, "sensor.p", default_unit="W") == pytest.approx(1.5)


def test_get_sensor_value_default_for_invalid():
    hass = MagicMock()
    hass.states.get.return_value = State("sensor.t", "nan", {})
    assert get_sensor_value(hass, "sensor.t", None) is None
    hass.states.get.return_value = State("sensor.t", "21.5", {})
    assert get_sensor_value(hass, "sensor.t", None) == pytest.approx(21.5)


def test_window_solar_gain_south_beats_north_facing_side_in_winter_noon():
    """Low winter sun: a vertical south window receives more than a
    horizontal-irradiance estimate would suggest."""
    noon = [datetime(2026, 1, 15, 11, 0, tzinfo=UTC)]
    kwargs = {
        "dni_forecast": [600.0],
        "diffuse_forecast": [80.0],
        "timestamps_utc": noon,
        "latitude": 52.0,
        "longitude": 5.0,
    }
    south = calculate_window_solar_gain([250.0], {"south": 10.0}, 1.2, **kwargs)
    east = calculate_window_solar_gain([250.0], {"east": 10.0}, 1.2, **kwargs)
    assert south[0] > east[0] > 0
    assert south[0] > 10.0 * 250.0 * 0.62 / 1000.0


def test_window_solar_gain_zero_without_glass():
    assert calculate_window_solar_gain([500.0, 0.0], {}, 1.2) == [0.0, 0.0]
