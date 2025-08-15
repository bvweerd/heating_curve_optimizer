import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.sensor import QuadraticCopSensor


@pytest.mark.asyncio
async def test_quadratic_cop_sensor_computes_value(hass):
    hass.states.async_set("sensor.supply", "35")
    hass.states.async_set("sensor.outdoor", "5")
    sensor = QuadraticCopSensor(
        hass=hass,
        name="COP",
        unique_id="cop1",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    await sensor.async_update()
    assert sensor.native_value == 4.6
    assert sensor.available is True
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_quadratic_cop_sensor_handles_unavailable(hass):
    hass.states.async_set("sensor.supply", "unknown")
    hass.states.async_set("sensor.outdoor", "5")
    sensor = QuadraticCopSensor(
        hass=hass,
        name="COP",
        unique_id="cop2",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=DeviceInfo(identifiers={("test", "2")}),
    )
    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_quadratic_cop_sensor_handles_missing_outdoor(hass):
    hass.states.async_set("sensor.supply", "35")
    hass.states.async_set("sensor.outdoor", "unknown")
    sensor = QuadraticCopSensor(
        hass=hass,
        name="COP",
        unique_id="cop3",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=DeviceInfo(identifiers={("test", "3")}),
    )
    await sensor.async_update()
    assert sensor.available is False
    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_quadratic_cop_sensor_uses_config_values(hass):
    hass.states.async_set("sensor.supply", "40")
    hass.states.async_set("sensor.outdoor", "10")
    sensor = QuadraticCopSensor(
        hass=hass,
        name="COP",
        unique_id="cop4",
        supply_sensor="sensor.supply",
        outdoor_sensor="sensor.outdoor",
        device=DeviceInfo(identifiers={("test", "4")}),
        k_factor=0.2,
        base_cop=5.0,
        outdoor_temp_coefficient=0.05,
        cop_compensation_factor=0.9,
    )
    await sensor.async_update()
    assert sensor.native_value == 4.05
    await sensor.async_will_remove_from_hass()
