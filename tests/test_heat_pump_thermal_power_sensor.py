"""Test HeatPumpThermalPowerSensor (sensor/event_driven.py)."""

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.sensor.event_driven import (
    HeatPumpThermalPowerSensor,
)


@pytest.fixture
def mock_device_info():
    return {
        "identifiers": {("heating_curve_optimizer", "test")},
        "name": "Test Device",
    }


@pytest.mark.asyncio
async def test_thermal_power_respects_configured_outdoor_temp_coefficient(
    hass: HomeAssistant, mock_device_info
):
    """The COP calc used to hardcode 0.08 for the outdoor-temperature
    coefficient instead of accepting/using the configured
    outdoor_temp_coefficient (CONF_OUTDOOR_TEMP_COEFFICIENT) - a code
    review found it silently ignoring that config value. A different
    coefficient must produce a different thermal-power reading for the
    same sensor states."""
    hass.states.async_set("sensor.power", "1000")  # W
    hass.states.async_set("sensor.supply", "40.0")
    hass.states.async_set("sensor.outdoor", "10.0")

    def make_sensor(outdoor_temp_coefficient: float) -> HeatPumpThermalPowerSensor:
        return HeatPumpThermalPowerSensor(
            hass=hass,
            name="Heat Pump Thermal Power",
            unique_id="test_thermal_power",
            power_sensor="sensor.power",
            supply_sensor="sensor.supply",
            outdoor_sensor="sensor.outdoor",
            device=mock_device_info,
            k_factor=0.11,
            base_cop=4.2,
            outdoor_temp_coefficient=0.025,
        )

    sensor_default_like = make_sensor(0.08)
    await sensor_default_like.async_update()
    value_at_008 = sensor_default_like.native_value

    sensor_other = HeatPumpThermalPowerSensor(
        hass=hass,
        name="Heat Pump Thermal Power",
        unique_id="test_thermal_power",
        power_sensor="sensor.power",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=mock_device_info,
        k_factor=0.11,
        base_cop=4.2,
        outdoor_temp_coefficient=0.5,
    )
    await sensor_other.async_update()
    value_at_05 = sensor_other.native_value

    assert value_at_008 is not None
    assert value_at_05 is not None
    assert value_at_008 != value_at_05


@pytest.mark.asyncio
async def test_thermal_power_respects_cop_compensation_factor(
    hass: HomeAssistant, mock_device_info
):
    """cop_compensation_factor must also actually be applied (it used to
    not even be a constructor parameter on this sensor)."""
    hass.states.async_set("sensor.power", "1000")
    hass.states.async_set("sensor.supply", "40.0")
    hass.states.async_set("sensor.outdoor", "10.0")

    sensor_full = HeatPumpThermalPowerSensor(
        hass=hass,
        name="Heat Pump Thermal Power",
        unique_id="test_thermal_power",
        power_sensor="sensor.power",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=mock_device_info,
        k_factor=0.11,
        base_cop=4.2,
        outdoor_temp_coefficient=0.025,
        cop_compensation_factor=1.0,
    )
    await sensor_full.async_update()
    value_full = sensor_full.native_value

    sensor_half = HeatPumpThermalPowerSensor(
        hass=hass,
        name="Heat Pump Thermal Power",
        unique_id="test_thermal_power",
        power_sensor="sensor.power",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=mock_device_info,
        k_factor=0.11,
        base_cop=4.2,
        outdoor_temp_coefficient=0.025,
        cop_compensation_factor=0.5,
    )
    await sensor_half.async_update()
    value_half = sensor_half.native_value

    assert value_full is not None
    assert value_half is not None
    assert value_half == pytest.approx(value_full * 0.5, rel=1e-6)
