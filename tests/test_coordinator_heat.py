"""Tests for HeatCalculationCoordinator (coordinator_heat.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.heating_curve_optimizer.const import DOMAIN
from custom_components.heating_curve_optimizer.coordinator import (
    HeatCalculationCoordinator,
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
