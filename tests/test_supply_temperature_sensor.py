import pytest
from homeassistant.helpers.entity import DeviceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_curve_optimizer.sensor import (
    CalculatedSupplyTemperatureSensor,
    OptimizedSupplyTemperatureSensor,
)
from custom_components.heating_curve_optimizer.const import DOMAIN


@pytest.mark.asyncio
async def test_calculated_supply_temperature_sensor(hass):
    hass.states.async_set("sensor.outdoor_temp", "0")
    hass.states.async_set("number.heating_curve_offset", "2")

    entry = MockConfigEntry(domain=DOMAIN, data={})
    hass.data[DOMAIN] = {
        entry.entry_id: entry.data,
        "runtime": {
            "heat_curve_min": 30.0,
            "heat_curve_max": 50.0,
            "heat_curve_min_outdoor": -20.0,
            "heat_curve_max_outdoor": 15.0,
            "heating_curve_offset": 2.0,
        }
    }

    sensor = CalculatedSupplyTemperatureSensor(
        hass=hass,
        name="Calculated Supply Temperature",
        unique_id="calc_sup_temp",
        outdoor_sensor="sensor.outdoor_temp",
        entry=entry,
        device=DeviceInfo(identifiers={("test", "1")}),
    )

    await sensor.async_update()
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(30.714, rel=1e-3)


@pytest.mark.asyncio
async def test_optimized_supply_temperature_sensor(hass):
    hass.states.async_set("sensor.outdoor_temp", "0")
    hass.states.async_set("sensor.heating_curve_offset", "-1")

    entry = MockConfigEntry(domain=DOMAIN, data={})
    hass.data[DOMAIN] = {
        entry.entry_id: entry.data,
        "runtime": {
            "heat_curve_min": 30.0,
            "heat_curve_max": 50.0,
            "heat_curve_min_outdoor": -20.0,
            "heat_curve_max_outdoor": 15.0,
            "heating_curve_offset": -1.0,
        }
    }

    # Create a mock heating curve offset sensor
    hass.states.async_set("sensor.heating_curve_offset", "-1", {
        "future_supply_temperatures": [37.0, 36.5, 36.0]
    })

    sensor = OptimizedSupplyTemperatureSensor(
        hass=hass,
        name="Optimized Supply Temperature",
        unique_id="opt_sup_temp",
        outdoor_sensor="sensor.outdoor_temp",
        entry=entry,
        device=DeviceInfo(identifiers={("test", "2")}),
        offset_entity="sensor.heating_curve_offset",
    )

    await sensor.async_update()
    assert sensor.available is True
    # The base supply temperature calculation should work
    assert sensor.native_value is not None
