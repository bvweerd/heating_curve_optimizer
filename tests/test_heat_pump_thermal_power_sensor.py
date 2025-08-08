import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import HeatPumpThermalPowerSensor


@pytest.mark.asyncio
async def test_heat_pump_thermal_power_sensor_computes_value(hass):
    hass.states.async_set("sensor.power", "1000")
    hass.states.async_set("sensor.supply", "35")
    hass.states.async_set("sensor.outdoor", "5")
    sensor = HeatPumpThermalPowerSensor(
        hass=hass,
        name="Thermal Power",
        unique_id="tp1",
        power_sensor="sensor.power",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    await sensor.async_update()
    assert sensor.native_value == pytest.approx(4.6, rel=1e-3)
    assert sensor.available is True
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_heat_pump_thermal_power_sensor_handles_unavailable(hass):
    hass.states.async_set("sensor.power", "unavailable")
    hass.states.async_set("sensor.supply", "35")
    hass.states.async_set("sensor.outdoor", "5")
    sensor = HeatPumpThermalPowerSensor(
        hass=hass,
        name="Thermal Power",
        unique_id="tp2",
        power_sensor="sensor.power",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=DeviceInfo(identifiers={("test", "2")}),
    )
    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()
