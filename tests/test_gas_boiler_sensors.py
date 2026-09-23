"""Tests for the hybrid gas-boiler cost comparison entities
(sensor/gas_boiler/*.py and binary_sensor.py's GasBoilerPreferredBinarySensor)."""

import pytest
from unittest.mock import MagicMock
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.binary_sensor import (
    GasBoilerPreferredBinarySensor,
)
from custom_components.heating_curve_optimizer.sensor.gas_boiler.cost_savings import (
    GasBoilerCostSavingsSensor,
)
from custom_components.heating_curve_optimizer.sensor.gas_boiler.gas_cost import (
    GasBoilerGasCostSensor,
)
from custom_components.heating_curve_optimizer.sensor.gas_boiler.heat_pump_cost import (
    GasBoilerHeatPumpCostSensor,
)


@pytest.fixture
def device_info():
    return DeviceInfo(identifiers={("heating_curve_optimizer", "gas_boiler_test")})


def _mock_coordinator(data: dict | None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.config = {
        "gas_boiler_efficiency": 0.9,
        "gas_calorific_value_kwh_per_m3": 9.77,
    }
    return coordinator


FULL_DATA = {
    "available": True,
    "gas_price_eur_per_m3": 1.20,
    "electricity_price_eur_per_kwh": 0.35,
    "heat_pump_cop": 3.5,
    "supply_temp": 40.0,
    "outdoor_temp": -5.0,
    "heat_pump_cost_eur_per_kwh": 0.10,
    "gas_cost_eur_per_kwh": 0.1366,
    "savings_eur_per_kwh": -0.0366,
    "savings_pct": -36.6,
    "heat_currently_needed": True,
    "heat_currently_needed_source": "power_sensor",
    "prefer_gas_boiler": False,
}


@pytest.mark.asyncio
async def test_heat_pump_cost_sensor_reports_value_and_attrs(hass, device_info):
    sensor = GasBoilerHeatPumpCostSensor(
        coordinator=_mock_coordinator(FULL_DATA),
        name="Heat Pump Cost",
        unique_id="test_hp_cost",
        icon="mdi:heat-pump",
        device=device_info,
    )
    assert sensor.native_value == pytest.approx(0.10)
    assert sensor.available is True
    assert sensor.extra_state_attributes["heat_pump_cop"] == 3.5


@pytest.mark.asyncio
async def test_gas_cost_sensor_reports_value_and_attrs(hass, device_info):
    sensor = GasBoilerGasCostSensor(
        coordinator=_mock_coordinator(FULL_DATA),
        name="Gas Cost",
        unique_id="test_gas_cost",
        icon="mdi:fire",
        device=device_info,
    )
    assert sensor.native_value == pytest.approx(0.1366)
    assert sensor.available is True
    assert sensor.extra_state_attributes["efficiency"] == 0.9


@pytest.mark.asyncio
async def test_cost_savings_sensor_reports_value_and_attrs(hass, device_info):
    sensor = GasBoilerCostSavingsSensor(
        coordinator=_mock_coordinator(FULL_DATA),
        name="Gas Boiler Cost Savings",
        unique_id="test_savings",
        icon="mdi:piggy-bank-outline",
        device=device_info,
    )
    assert sensor.native_value == pytest.approx(-0.0366)
    assert sensor.available is True
    assert sensor.extra_state_attributes["prefer_gas_boiler"] is False
    assert sensor.extra_state_attributes["heat_currently_needed"] is True


@pytest.mark.asyncio
async def test_binary_sensor_is_on_reflects_prefer_gas_boiler(hass, device_info):
    data = {**FULL_DATA, "prefer_gas_boiler": True}
    sensor = GasBoilerPreferredBinarySensor(
        _mock_coordinator(data), "test_entry", device_info
    )
    assert sensor.is_on is True
    assert sensor.available is True
    assert sensor.extra_state_attributes["heat_currently_needed_source"] == (
        "power_sensor"
    )


@pytest.mark.asyncio
async def test_binary_sensor_is_off_when_prefer_gas_boiler_false(hass, device_info):
    sensor = GasBoilerPreferredBinarySensor(
        _mock_coordinator(FULL_DATA), "test_entry", device_info
    )
    assert sensor.is_on is False


@pytest.mark.parametrize(
    "sensor_cls,kwargs",
    [
        (
            GasBoilerHeatPumpCostSensor,
            {"name": "Heat Pump Cost", "unique_id": "u1", "icon": "mdi:heat-pump"},
        ),
        (
            GasBoilerGasCostSensor,
            {"name": "Gas Cost", "unique_id": "u2", "icon": "mdi:fire"},
        ),
        (
            GasBoilerCostSavingsSensor,
            {
                "name": "Gas Boiler Cost Savings",
                "unique_id": "u3",
                "icon": "mdi:piggy-bank-outline",
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_sensors_unavailable_when_data_none(
    hass, device_info, sensor_cls, kwargs
):
    sensor = sensor_cls(
        coordinator=_mock_coordinator(None), device=device_info, **kwargs
    )
    assert sensor.available is False
    assert sensor.native_value is None


@pytest.mark.parametrize(
    "sensor_cls,kwargs",
    [
        (
            GasBoilerHeatPumpCostSensor,
            {"name": "Heat Pump Cost", "unique_id": "u1", "icon": "mdi:heat-pump"},
        ),
        (
            GasBoilerGasCostSensor,
            {"name": "Gas Cost", "unique_id": "u2", "icon": "mdi:fire"},
        ),
        (
            GasBoilerCostSavingsSensor,
            {
                "name": "Gas Boiler Cost Savings",
                "unique_id": "u3",
                "icon": "mdi:piggy-bank-outline",
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_sensors_unavailable_when_coordinator_data_not_available(
    hass, device_info, sensor_cls, kwargs
):
    """Data present but its own "available" flag is False (a soft,
    self-healing UpdateFailed left the previous cycle's data in place) -
    the entity must still report unavailable."""
    stale_data = {**FULL_DATA, "available": False}
    sensor = sensor_cls(
        coordinator=_mock_coordinator(stale_data), device=device_info, **kwargs
    )
    assert sensor.available is False


@pytest.mark.asyncio
async def test_binary_sensor_unavailable_when_data_none(hass, device_info):
    sensor = GasBoilerPreferredBinarySensor(
        _mock_coordinator(None), "test_entry", device_info
    )
    assert sensor.available is False
    assert sensor.is_on is False
