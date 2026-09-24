"""Tests for the sensor platform (sensor.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.const import DOMAIN
from custom_components.heating_curve_optimizer.sensor import (
    CoordinatorHeatingCurveOffsetSensor,
    CoordinatorHeatLossSensor,
    CoordinatorNetHeatLossSensor,
    CoordinatorOptimizedSupplyTemperatureSensor,
    CoordinatorOutdoorTemperatureSensor,
)


@pytest.fixture
def device_info() -> DeviceInfo:
    """Return a basic DeviceInfo for tests."""
    return DeviceInfo(
        identifiers={(DOMAIN, "test_entry_id")},
        name="Heating Curve Optimizer",
    )


@pytest.fixture
def weather_coordinator() -> MagicMock:
    """Return a mock weather coordinator with data."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "current_temperature": 7.5,
        "temperature_forecast": [7.0, 6.0, 5.0],
        "humidity_forecast": [80.0, 82.0, 85.0],
    }
    return coordinator


@pytest.fixture
def heat_coordinator() -> MagicMock:
    """Return a mock heat coordinator with data."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "heat_loss": 2.5,
        "solar_gain": 0.3,
        "net_heat_loss": 2.2,
        "heat_loss_forecast": [2.5, 2.4, 2.3],
        "net_heat_loss_forecast": [2.2, 2.1, 2.0],
    }
    return coordinator


@pytest.fixture
def optimization_coordinator() -> MagicMock:
    """Return a mock optimization coordinator with data."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "optimized_offset": 1.0,
        "optimized_offsets": [1.0, 0.0, -1.0],
        "future_supply_temperatures": [42.0, 40.0, 38.0],
        "total_cost": 1.20,
        "baseline_cost": 1.50,
        "cost_savings": 0.30,
        "buffer_evolution": [0.5, 0.4, 0.3],
    }
    return coordinator


class TestBaseUtilitySensorClassAttributes:
    """Verify class-level attribute patterns on sensor classes.

    HA's Entity base uses a property descriptor for _attr_translation_key
    and _attr_has_entity_name; the real values are read via instances or
    via the HA Entity's property accessors (has_entity_name/translation_key).
    """

    def test_outdoor_temperature_sensor_has_entity_name(
        self, weather_coordinator, device_info
    ):
        """has_entity_name must be True."""
        sensor = CoordinatorOutdoorTemperatureSensor(
            coordinator=weather_coordinator,
            name="Outdoor Temperature",
            unique_id="test_ot",
            device=device_info,
        )
        assert sensor.has_entity_name is True

    def test_outdoor_temperature_sensor_translation_key(
        self, weather_coordinator, device_info
    ):
        sensor = CoordinatorOutdoorTemperatureSensor(
            coordinator=weather_coordinator,
            name="Outdoor Temperature",
            unique_id="test_ot",
            device=device_info,
        )
        assert sensor.translation_key == "outdoor_temperature"

    def test_heat_loss_sensor_translation_key(self, heat_coordinator, device_info):
        sensor = CoordinatorHeatLossSensor(
            coordinator=heat_coordinator,
            name="Heat Loss",
            unique_id="test_hl",
            icon="mdi:fire",
            device=device_info,
        )
        assert sensor.translation_key == "heat_loss"

    def test_net_heat_loss_sensor_translation_key(self, heat_coordinator, device_info):
        sensor = CoordinatorNetHeatLossSensor(
            coordinator=heat_coordinator,
            name="Net Heat Loss",
            unique_id="test_nhl",
            icon="mdi:fire-off",
            device=device_info,
        )
        assert sensor.translation_key == "net_heat_loss"

    def test_heating_curve_offset_translation_key(
        self, optimization_coordinator, device_info
    ):
        sensor = CoordinatorHeatingCurveOffsetSensor(
            coordinator=optimization_coordinator,
            name="Heating Curve Offset",
            unique_id="test_hco",
            icon="mdi:chart-line",
            device=device_info,
        )
        assert sensor.translation_key == "heating_curve_offset"

    def test_optimized_supply_temperature_translation_key(
        self, optimization_coordinator, device_info
    ):
        sensor = CoordinatorOptimizedSupplyTemperatureSensor(
            coordinator=optimization_coordinator,
            name="Optimized Supply Temperature",
            unique_id="test_ost",
            icon="mdi:thermometer-chevron-up",
            device=device_info,
        )
        assert sensor.translation_key == "optimized_supply_temperature"


class TestSensorUniqueId:
    """Verify unique_id is set correctly on construction."""

    def test_outdoor_temperature_unique_id(self, weather_coordinator, device_info):
        sensor = CoordinatorOutdoorTemperatureSensor(
            coordinator=weather_coordinator,
            name="Outdoor Temperature",
            unique_id="abc123_outdoor_temperature",
            device=device_info,
        )
        assert sensor._attr_unique_id == "abc123_outdoor_temperature"

    def test_heat_loss_unique_id(self, heat_coordinator, device_info):
        sensor = CoordinatorHeatLossSensor(
            coordinator=heat_coordinator,
            name="Heat Loss",
            unique_id="abc123_heat_loss",
            icon="mdi:fire",
            device=device_info,
        )
        assert sensor._attr_unique_id == "abc123_heat_loss"

    def test_device_info_is_set(self, weather_coordinator, device_info):
        sensor = CoordinatorOutdoorTemperatureSensor(
            coordinator=weather_coordinator,
            name="Outdoor Temperature",
            unique_id="abc123_outdoor_temperature",
            device=device_info,
        )
        assert sensor._attr_device_info is device_info


class TestSensorNativeValue:
    """Verify native_value returns correct data from coordinator."""

    def test_outdoor_temperature_native_value(self, weather_coordinator, device_info):
        sensor = CoordinatorOutdoorTemperatureSensor(
            coordinator=weather_coordinator,
            name="Outdoor Temperature",
            unique_id="test_ot",
            device=device_info,
        )
        assert sensor.native_value == pytest.approx(7.5)

    def test_outdoor_temperature_native_value_no_data(self, device_info):
        coordinator = MagicMock()
        coordinator.last_update_success = False
        coordinator.data = None
        sensor = CoordinatorOutdoorTemperatureSensor(
            coordinator=coordinator,
            name="Outdoor Temperature",
            unique_id="test_ot",
            device=device_info,
        )
        assert sensor.native_value is None

    def test_heat_loss_native_value(self, heat_coordinator, device_info):
        sensor = CoordinatorHeatLossSensor(
            coordinator=heat_coordinator,
            name="Heat Loss",
            unique_id="test_hl",
            icon="mdi:fire",
            device=device_info,
        )
        assert sensor.native_value == pytest.approx(2.5)

    def test_net_heat_loss_native_value(self, heat_coordinator, device_info):
        sensor = CoordinatorNetHeatLossSensor(
            coordinator=heat_coordinator,
            name="Net Heat Loss",
            unique_id="test_nhl",
            icon="mdi:fire-off",
            device=device_info,
        )
        assert sensor.native_value == pytest.approx(2.2)

    def test_heating_curve_offset_native_value(
        self, optimization_coordinator, device_info
    ):
        sensor = CoordinatorHeatingCurveOffsetSensor(
            coordinator=optimization_coordinator,
            name="Heating Curve Offset",
            unique_id="test_hco",
            icon="mdi:chart-line",
            device=device_info,
        )
        assert sensor.native_value == pytest.approx(1.0)

    def test_optimized_supply_temperature_native_value(
        self, optimization_coordinator, device_info
    ):
        sensor = CoordinatorOptimizedSupplyTemperatureSensor(
            coordinator=optimization_coordinator,
            name="Optimized Supply Temperature",
            unique_id="test_ost",
            icon="mdi:thermometer-chevron-up",
            device=device_info,
        )
        assert sensor.native_value == pytest.approx(42.0)
