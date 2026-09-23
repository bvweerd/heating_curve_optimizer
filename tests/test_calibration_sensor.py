"""Test the calibration sensor with degree_days analysis."""

import pytest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.heating_curve_optimizer.calibration_sensor import (
    CalibrationSensor,
)
from custom_components.heating_curve_optimizer.const import (
    CONF_AREA_M2,
    CONF_ENERGY_LABEL,
)


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry.

    area_m2/energy_label are zone-specific settings now (every zone,
    including the first, is a subentry - see __init__.py's
    _find_primary_zone_subentry), read by CalibrationSensor via
    `runtime_data.heat_coordinator.config` rather than `entry.data`/
    `.options` directly (see `_get_zone_value`).
    """
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {}
    entry.options = {}
    entry.runtime_data.heat_coordinator.config = {
        CONF_AREA_M2: 159,
        CONF_ENERGY_LABEL: "A+",
    }
    return entry


@pytest.fixture
def mock_device_info():
    """Create mock device info."""
    return {
        "identifiers": {("heating_curve_optimizer", "test")},
        "name": "Test Device",
    }


@pytest.mark.asyncio
async def test_calibration_sensor_init(
    hass: HomeAssistant, mock_config_entry, mock_device_info
):
    """Test calibration sensor initialization."""
    sensor = CalibrationSensor(
        hass=hass,
        name="Test Calibration",
        unique_id="test_calibration",
        device=mock_device_info,
        entry=mock_config_entry,
        heat_loss_sensor="sensor.heat_loss",
        thermal_power_sensor="sensor.thermal_power",
        outdoor_sensor="sensor.outdoor_temp",
        indoor_sensor="sensor.indoor_temp",
        supply_temp_sensor="sensor.supply_temp",
        cop_sensor="sensor.cop",
    )

    assert sensor.name == "Test Calibration"
    assert sensor.unique_id == "test_calibration"
    assert sensor.entity_registry_enabled_default is False
    assert sensor.heat_loss_sensor == "sensor.heat_loss"
    assert sensor.thermal_power_sensor == "sensor.thermal_power"


@pytest.mark.asyncio
async def test_native_value_is_none_not_zero_when_no_data_yet(
    hass: HomeAssistant, mock_config_entry, mock_device_info
):
    """Regression test: before enough history exists, native_value must be
    None (shown as "Unknown"), never 0.0 - a real 0% calibration match and
    "nothing calculated yet" must not be indistinguishable on the primary
    state. None of the source sensors exist in `hass.states`, so every
    validation step returns None and calibration_quality stays None."""
    sensor = CalibrationSensor(
        hass=hass,
        name="Test Calibration",
        unique_id="test_calibration",
        device=mock_device_info,
        entry=mock_config_entry,
        heat_loss_sensor="sensor.does_not_exist",
        thermal_power_sensor="sensor.does_not_exist_either",
        outdoor_sensor=None,
        indoor_sensor=None,
        supply_temp_sensor=None,
        cop_sensor=None,
    )

    await sensor.async_update()

    assert sensor.native_value is None
    assert sensor.available is True


def test_status_message_flags_missing_calibration_quality(
    hass: HomeAssistant, mock_config_entry, mock_device_info
):
    """Regression test: when calibration_quality (the primary state) is
    None but a side metric like storage efficiency did produce a message,
    the status text must say the primary score is still missing - a
    message list built purely from side metrics ("Thermal storage: OK")
    must not read as if calibration is complete while the main state is
    "Unknown"."""
    sensor = CalibrationSensor(
        hass=hass,
        name="Test Calibration",
        unique_id="test_calibration",
        device=mock_device_info,
        entry=mock_config_entry,
    )

    status = sensor._get_status_message(
        heat_loss_accuracy=None,
        cop_accuracy=None,
        storage_recommendation=0.20,
        calibration_quality=None,
    )

    assert status.startswith("Calibration score: insufficient data")
    assert "Thermal storage" in status


@pytest.mark.asyncio
async def test_degree_days_analysis(
    hass: HomeAssistant, mock_config_entry, mock_device_info
):
    """Test degree_days correlation analysis."""
    sensor = CalibrationSensor(
        hass=hass,
        name="Test Calibration",
        unique_id="test_calibration",
        device=mock_device_info,
        entry=mock_config_entry,
        heat_loss_sensor="sensor.heat_loss",
        thermal_power_sensor="sensor.thermal_power",
        outdoor_sensor="sensor.outdoor_temp",
        indoor_sensor="sensor.indoor_temp",
    )

    # Mock historical data
    now = dt_util.utcnow()

    # Create mock thermal power history (kW)
    thermal_states = []
    outdoor_states = []
    indoor_states = []

    # Simulate 7 days of data
    # Expected U-value for label C (0.80) with 159 m²:
    # Q = U × A × ΔT = 0.80 × 159 × ΔT = 127.2 × ΔT (W)
    # For ΔT=12°C: Q = 1.53 kW

    for day in range(7):
        day_start = now - timedelta(days=7 - day)

        # Simulate hourly measurements
        for hour in range(24):
            timestamp = day_start + timedelta(hours=hour)

            # Simulate conditions:
            # Outdoor: 8°C average
            # Indoor: 20°C (ΔT = 12°C)
            # Expected thermal power for label C: ~1.5 kW

            thermal_state = MagicMock()
            thermal_state.state = "1.5"  # kW
            thermal_state.last_updated = timestamp
            thermal_states.append(thermal_state)

            outdoor_state = MagicMock()
            outdoor_state.state = "8.0"  # °C
            outdoor_state.last_updated = timestamp
            outdoor_states.append(outdoor_state)

            indoor_state = MagicMock()
            indoor_state.state = "20.0"  # °C
            indoor_state.last_updated = timestamp
            indoor_states.append(indoor_state)

    mock_thermal_history = {"sensor.thermal_power": thermal_states}
    mock_outdoor_history = {"sensor.outdoor_temp": outdoor_states}
    mock_indoor_history = {"sensor.indoor_temp": indoor_states}

    # Mock recorder
    with patch(
        "custom_components.heating_curve_optimizer.calibration_sensor.recorder"
    ) as mock_recorder:
        mock_recorder.is_entity_recorded.return_value = True
        mock_instance = MagicMock()
        mock_recorder.get_instance.return_value = mock_instance

        # Mock async_add_executor_job to return our mock data
        async def mock_executor_job(func, *args, **kwargs):
            entity_id = args[3] if len(args) > 3 else None
            if entity_id == "sensor.thermal_power":
                return mock_thermal_history
            elif entity_id == "sensor.outdoor_temp":
                return mock_outdoor_history
            elif entity_id == "sensor.indoor_temp":
                return mock_indoor_history
            return {}

        mock_instance.async_add_executor_job = mock_executor_job

        # Run analysis
        start_time = now - timedelta(days=7)
        result = await sensor._analyze_degree_days_correlation(start_time, now)

        # Verify results
        assert result is not None
        assert "measured_u_value" in result
        assert "recommended_label" in result
        assert "sample_count" in result
        assert "correlation" in result

        # Check if measured U-value is reasonable for label C
        # Q = U × A × ΔT × 24h
        # 1.5 kW × 24h = 36 kWh/day
        # U × A = 36000 / (12 × 24) = 125 W/K
        # U = 125 / 159 = 0.79 W/(m²·K) ≈ label C (0.80)
        measured_u = result["measured_u_value"]
        assert 0.7 < measured_u < 0.9, f"Expected U ≈ 0.79, got {measured_u}"

        # Should recommend label C (current is A+ which is too optimistic)
        assert result["recommended_label"] in [
            "B",
            "C",
        ], f"Expected label B or C, got {result['recommended_label']}"

        # Should have analyzed 7 days
        assert (
            result["sample_count"] >= 3
        ), f"Expected at least 3 days, got {result['sample_count']}"

        print("✅ Graaddagen analysis successful:")
        print(f"   Measured U-value: {measured_u:.2f} W/(m²·K)")
        print(f"   Recommended label: {result['recommended_label']}")
        print(f"   Sample count: {result['sample_count']} days")
        print(f"   Correlation: {result['correlation']:.2f}")


@pytest.mark.asyncio
async def test_energy_label_recommendation(
    hass: HomeAssistant, mock_config_entry, mock_device_info
):
    """Test energy label recommendation in status message."""
    sensor = CalibrationSensor(
        hass=hass,
        name="Test Calibration",
        unique_id="test_calibration",
        device=mock_device_info,
        entry=mock_config_entry,
        heat_loss_sensor="sensor.heat_loss",
        thermal_power_sensor="sensor.thermal_power",
    )

    # Test with different recommendation
    status = sensor._get_status_message(
        heat_loss_accuracy=85.0,
        cop_accuracy=90.0,
        storage_recommendation=None,
        energy_label_recommendation="C",  # Different from configured A+
        trend_analysis={"direction": "stable", "change_pct": 2.0},
    )

    assert "Energy label: Recommended C (current: A+)" in status
    assert "Heat loss: Good" in status
    assert "COP: Good" in status  # 90% = Good (85-95%)
    assert "Trend: Stable" in status

    print(f"✅ Status message: {status}")


@pytest.mark.asyncio
async def test_validate_cop_uses_configured_base_cop(
    hass: HomeAssistant, mock_device_info
):
    """_validate_cop must compare against the user's configured base_cop,
    not the module default.

    A code review found this hardcoding DEFAULT_COP_AT_35 (4.2) instead of
    reading CONF_BASE_COP like every other parameter in the same function
    - so a user with a real base_cop far from 4.2 would see this
    diagnostic report a permanent mismatch even when the live COP sensor
    is perfectly correct.
    """
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {
        CONF_AREA_M2: 150,
        CONF_ENERGY_LABEL: "C",
        "base_cop": 3.0,
        "k_factor": 0.0,
        "outdoor_temp_coefficient": 0.0,
        "cop_compensation_factor": 1.0,
    }
    entry.options = {}

    sensor = CalibrationSensor(
        hass=hass,
        name="Test Calibration",
        unique_id="test_calibration",
        device=mock_device_info,
        entry=entry,
        heat_loss_sensor="sensor.heat_loss",
        thermal_power_sensor="sensor.thermal_power",
        outdoor_sensor="sensor.outdoor_temp",
        indoor_sensor="sensor.indoor_temp",
        supply_temp_sensor="sensor.supply_temp",
        cop_sensor="sensor.cop",
    )

    # With k_factor=0 and outdoor_temp_coefficient=0, theoretical COP
    # reduces to exactly base_cop - so a cop_sensor reading that exactly
    # matches the configured base_cop (3.0, not the 4.2 default) must
    # score 100% accuracy.
    hass.states.async_set("sensor.cop", "3.0")
    hass.states.async_set("sensor.outdoor_temp", "7.0")
    hass.states.async_set("sensor.supply_temp", "35.0")

    accuracy = await sensor._validate_cop(dt_util.utcnow(), dt_util.utcnow())

    assert accuracy == pytest.approx(100.0, abs=0.1)


@pytest.mark.asyncio
async def test_trend_analysis(hass: HomeAssistant, mock_config_entry, mock_device_info):
    """Test long-term trend analysis."""
    sensor = CalibrationSensor(
        hass=hass,
        name="Test Calibration",
        unique_id="test_calibration",
        device=mock_device_info,
        entry=mock_config_entry,
        heat_loss_sensor="sensor.heat_loss",
        thermal_power_sensor="sensor.thermal_power",
    )

    # Mock _validate_heat_loss to return different values for first/second half
    now = dt_util.utcnow()
    start_time = now - timedelta(days=7)

    call_count = [0]

    async def mock_validate_heat_loss(start, end):
        # First call (first half) returns lower accuracy
        # Second call (second half) returns higher accuracy
        call_count[0] += 1
        if call_count[0] == 1:
            return 75.0  # First half: lower accuracy
        else:
            return 85.0  # Second half: higher accuracy (+10%)

    with patch.object(
        sensor, "_validate_heat_loss", side_effect=mock_validate_heat_loss
    ):
        result = await sensor._analyze_long_term_trend(start_time, now)

        assert result is not None
        assert result["direction"] == "improving"
        assert result["change_pct"] == 10.0

        print(
            f"✅ Trend analysis: {result['direction']} ({result['change_pct']:+.1f}%)"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
