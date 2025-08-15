import pytest
from homeassistant.helpers.entity import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import (
    CalculatedSupplyTemperatureSensor,
    OptimizedSupplyTemperatureSensor,
)
from custom_components.heating_curve_optimizer.const import DOMAIN


@pytest.mark.asyncio
async def test_calculated_supply_temperature_sensor(hass):
    hass.states.async_set("sensor.outdoor_temp", "0")
    hass.states.async_set("number.heating_curve_offset", "2")
    hass.data[DOMAIN] = {
        "heat_curve_min": 30.0,
        "heat_curve_max": 50.0,
        "heat_curve_min_outdoor": -20.0,
        "heat_curve_max_outdoor": 15.0,
    }

    sensor = CalculatedSupplyTemperatureSensor(
        hass=hass,
        name="Calculated Supply Temperature",
        unique_id="calc_sup_temp",
        outdoor_sensor="sensor.outdoor_temp",
        offset_entity="number.heating_curve_offset",
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    await sensor.async_update()
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(40.571, rel=1e-3)


@pytest.mark.asyncio
async def test_optimized_supply_temperature_sensor(hass):
    hass.states.async_set("sensor.outdoor_temp", "0")
    hass.states.async_set("sensor.heating_curve_offset", "-1")
    hass.data[DOMAIN] = {
        "heat_curve_min": 30.0,
        "heat_curve_max": 50.0,
        "heat_curve_min_outdoor": -20.0,
        "heat_curve_max_outdoor": 15.0,
    }

    sensor = OptimizedSupplyTemperatureSensor(
        hass=hass,
        name="Optimized Supply Temperature",
        unique_id="opt_sup_temp",
        outdoor_sensor="sensor.outdoor_temp",
        offset_entity="sensor.heating_curve_offset",
        device=DeviceInfo(identifiers={("test", "2")}),
    )

    await sensor.async_update()
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(37.571, rel=1e-3)
