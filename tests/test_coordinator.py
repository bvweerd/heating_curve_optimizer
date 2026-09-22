"""Test the coordinator module."""

import pytest
from unittest.mock import MagicMock
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.heating_curve_optimizer.const import DOMAIN
from custom_components.heating_curve_optimizer.coordinator import (
    HeatCalculationCoordinator,
    OptimizationCoordinator,
    _update_failed,
)


@pytest.mark.asyncio
async def test_heat_coordinator_initialization(hass: HomeAssistant):
    """Test HeatCalculationCoordinator initialization."""
    weather_coordinator = MagicMock()
    weather_coordinator.data = {
        "current_temperature": 10.0,
        "temperature_forecast": [10.0, 9.0, 8.0],
    }

    config = {
        "area_m2": 150,
        "energy_label": "C",
        "glass_south_m2": 10,
        "glass_east_m2": 5,
        "glass_west_m2": 5,
        "glass_u_value": 1.2,
    }

    coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, config, "test_entry"
    )

    assert coordinator.weather_coordinator == weather_coordinator
    assert coordinator.config == config


@pytest.mark.asyncio
async def test_optimization_coordinator_initialization(hass: HomeAssistant):
    """Test OptimizationCoordinator initialization."""
    heat_coordinator = MagicMock()
    heat_coordinator.data = {
        "net_heat_loss_kw": 1.5,
        "heat_loss_forecast": [1.5] * 6,
    }

    config = {
        "consumption_price_sensor": "sensor.price_consumption",
        "k_factor": 0.025,
        "base_cop": 3.5,
    }

    coordinator = OptimizationCoordinator(hass, heat_coordinator, config)

    assert coordinator.heat_coordinator == heat_coordinator
    assert coordinator.config == config


@pytest.mark.asyncio
async def test_heat_coordinator_shutdown(hass: HomeAssistant):
    """Test HeatCalculationCoordinator shutdown."""
    weather_coordinator = MagicMock()
    config = {"area_m2": 150, "energy_label": "C"}

    coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, config, "test_entry"
    )
    await coordinator.async_setup()

    # Should complete without error
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_optimization_coordinator_shutdown(hass: HomeAssistant):
    """Test OptimizationCoordinator shutdown."""
    heat_coordinator = MagicMock()
    config = {}

    coordinator = OptimizationCoordinator(hass, heat_coordinator, config)
    await coordinator.async_setup()

    # Should complete without error
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_heat_coordinator_creates_and_clears_repair_issue(hass: HomeAssistant):
    """quality_scale's repair-issues rule: a persistent weather-data outage
    (not just a single failed refresh) must show up in Settings > Repairs,
    and clear itself once weather data is available again - mirroring
    battery_controller's ForecastCoordinator."""
    weather_coordinator = MagicMock()
    weather_coordinator.data = None
    config = {"area_m2": 150, "energy_label": "C"}
    coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, config, "issue_test_entry"
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    issue_id = "weather_data_unavailable_issue_test_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    weather_coordinator.data = {
        "current_temperature": 10.0,
        "temperature_forecast": [10.0] * 8,
        "radiation_forecast": [0.0] * 8,
    }
    await coordinator._async_update_data()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.asyncio
async def test_optimization_coordinator_creates_repair_issue_for_bad_price_sensor(
    hass: HomeAssistant,
):
    """Same rule, for a configured-but-unavailable price sensor - the
    optimizer cannot run at all without price data, which is exactly the
    kind of persistent, user-actionable problem repair issues are for."""
    heat_coordinator = MagicMock()
    heat_coordinator.data = {
        "net_heat_loss_forecast": [1.0] * 8,
        "heat_demand_factor": 1.0,
        "heat_pump_on": True,
        "net_heat_loss": 1.0,
    }
    heat_coordinator.weather_coordinator = MagicMock()
    heat_coordinator.weather_coordinator.data = {"temperature_forecast": [2.0] * 8}

    config = {"consumption_price_sensor": "sensor.missing_price"}
    coordinator = OptimizationCoordinator(
        hass, heat_coordinator, config, "issue_test_entry_2"
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    issue_id = "price_sensor_unavailable_issue_test_entry_2"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


def test_update_failed_falls_back_on_ha_versions_without_translation_kwargs():
    """This repo's own test environment (HA 2024.3.3) is exactly such a
    release: UpdateFailed still extends plain Exception there, so
    UpdateFailed(translation_domain=...) raises TypeError
    ("takes no keyword arguments"). _update_failed() must degrade to a
    formatted plain-string message instead of crashing every coordinator
    update on that release - this is exercised for real (not mocked) by
    every other UpdateFailed-raising test in this file, since this HA
    release always takes the fallback branch."""
    err = _update_failed("price_sensor_unavailable", {"sensor": "sensor.price"})
    assert isinstance(err, UpdateFailed)
    assert str(err) == "Price sensor sensor.price is unavailable."


def test_update_failed_without_placeholders():
    err = _update_failed("no_price_sensor")
    assert str(err) == "No electricity price sensor is configured."
