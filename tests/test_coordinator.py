"""Test the coordinator module."""

import pytest
from unittest.mock import MagicMock
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.coordinator import (
    WeatherDataCoordinator,
    HeatCalculationCoordinator,
    OptimizationCoordinator,
)


@pytest.mark.asyncio
async def test_weather_coordinator_initialization(hass: HomeAssistant):
    """Test WeatherDataCoordinator initialization."""
    coordinator = WeatherDataCoordinator(hass)

    assert coordinator.hass == hass
    assert coordinator.name == "Weather Data"


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

    coordinator = HeatCalculationCoordinator(hass, weather_coordinator, config)

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

    coordinator = HeatCalculationCoordinator(hass, weather_coordinator, config)
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
