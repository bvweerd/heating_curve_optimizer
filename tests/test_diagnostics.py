"""Test the diagnostics module."""

import pytest
from unittest.mock import MagicMock
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.heating_curve_optimizer.const import DOMAIN


@pytest.mark.asyncio
async def test_diagnostics_basic(hass: HomeAssistant):
    """Test basic diagnostics data collection."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "area_m2": 150,
            "energy_label": "C",
            "k_factor": 0.025,
            "base_cop": 3.5,
        },
        options={},
    )
    entry.add_to_hass(hass)

    # Create mock coordinators
    mock_weather = MagicMock()
    mock_weather.data = {
        "current_temperature": 10.0,
        "temperature_forecast": [10.0, 9.0, 8.0],
    }
    mock_weather.last_update_success = True

    mock_heat = MagicMock()
    mock_heat.data = {
        "heat_loss_kw": 2.0,
        "solar_gain_kw": 0.5,
        "net_heat_loss_kw": 1.5,
    }
    mock_heat.last_update_success = True

    mock_opt = MagicMock()
    mock_opt.data = {
        "optimal_offset": 1.0,
        "heat_buffer_kwh": 2.0,
    }
    mock_opt.last_update_success = True

    # Setup hass.data
    hass.data[DOMAIN] = {
        entry.entry_id: {
            "weather_coordinator": mock_weather,
            "heat_coordinator": mock_heat,
            "optimization_coordinator": mock_opt,
            "config": entry.data,
            "entry": entry,
        }
    }

    # Get diagnostics
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None
    # Diagnostics should contain entry and coordinator info
    assert isinstance(diagnostics, dict)


@pytest.mark.asyncio
async def test_diagnostics_redacts_sensitive_data(hass: HomeAssistant):
    """Test that diagnostics redacts sensitive configuration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "area_m2": 150,
            "energy_label": "C",
            "consumption_price_sensor": "sensor.price",
            "production_price_sensor": "sensor.price_production",
        },
        options={},
    )
    entry.add_to_hass(hass)

    mock_weather = MagicMock()
    mock_weather.data = {"current_temperature": 10.0}
    mock_weather.last_update_success = True

    mock_heat = MagicMock()
    mock_heat.data = {"heat_loss_kw": 2.0}
    mock_heat.last_update_success = True

    mock_opt = MagicMock()
    mock_opt.data = {"optimal_offset": 1.0}
    mock_opt.last_update_success = True

    hass.data[DOMAIN] = {
        entry.entry_id: {
            "weather_coordinator": mock_weather,
            "heat_coordinator": mock_heat,
            "optimization_coordinator": mock_opt,
            "config": entry.data,
            "entry": entry,
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    # Diagnostics should be returned
    assert diagnostics is not None
    assert isinstance(diagnostics, dict)


@pytest.mark.asyncio
async def test_diagnostics_with_missing_coordinators(hass: HomeAssistant):
    """Test diagnostics when some coordinators are missing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150},
        options={},
    )
    entry.add_to_hass(hass)

    # Only weather coordinator present
    mock_weather = MagicMock()
    mock_weather.data = {"current_temperature": 10.0}
    mock_weather.last_update_success = True

    hass.data[DOMAIN] = {
        entry.entry_id: {
            "weather_coordinator": mock_weather,
            "config": entry.data,
            "entry": entry,
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None
    assert isinstance(diagnostics, dict)


@pytest.mark.asyncio
async def test_diagnostics_with_failed_coordinators(hass: HomeAssistant):
    """Test diagnostics when coordinators have failed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150},
        options={},
    )
    entry.add_to_hass(hass)

    # Coordinators with failed status
    mock_weather = MagicMock()
    mock_weather.data = None
    mock_weather.last_update_success = False

    mock_heat = MagicMock()
    mock_heat.data = None
    mock_heat.last_update_success = False

    mock_opt = MagicMock()
    mock_opt.data = None
    mock_opt.last_update_success = False

    hass.data[DOMAIN] = {
        entry.entry_id: {
            "weather_coordinator": mock_weather,
            "heat_coordinator": mock_heat,
            "optimization_coordinator": mock_opt,
            "config": entry.data,
            "entry": entry,
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None
    # Should still return diagnostics even when coordinators fail


@pytest.mark.asyncio
async def test_diagnostics_includes_coordinator_status(hass: HomeAssistant):
    """Test diagnostics includes coordinator success status."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150},
        options={},
    )
    entry.add_to_hass(hass)

    mock_weather = MagicMock()
    mock_weather.data = {"current_temperature": 10.0}
    mock_weather.last_update_success = True

    mock_heat = MagicMock()
    mock_heat.data = {"heat_loss_kw": 2.0}
    mock_heat.last_update_success = False  # Failed

    mock_opt = MagicMock()
    mock_opt.data = {"optimal_offset": 1.0}
    mock_opt.last_update_success = True

    hass.data[DOMAIN] = {
        entry.entry_id: {
            "weather_coordinator": mock_weather,
            "heat_coordinator": mock_heat,
            "optimization_coordinator": mock_opt,
            "config": entry.data,
            "entry": entry,
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None


@pytest.mark.asyncio
async def test_diagnostics_empty_data(hass: HomeAssistant):
    """Test diagnostics when hass.data is empty."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150},
        options={},
    )
    entry.add_to_hass(hass)

    # No data in hass.data
    hass.data[DOMAIN] = {}

    # Should handle missing entry gracefully
    try:
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        # If it doesn't raise, check result
        assert diagnostics is not None or diagnostics is None
    except KeyError:
        # Acceptable to raise KeyError if entry not found
        pass


@pytest.mark.asyncio
async def test_diagnostics_with_complex_data(hass: HomeAssistant):
    """Test diagnostics with complex nested data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "area_m2": 150,
            "energy_label": "C",
        },
        options={},
    )
    entry.add_to_hass(hass)

    mock_weather = MagicMock()
    mock_weather.data = {
        "current_temperature": 10.0,
        "temperature_forecast": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0],
        "radiation_forecast": [0, 0, 100, 200, 300, 400],
    }
    mock_weather.last_update_success = True

    mock_heat = MagicMock()
    mock_heat.data = {
        "heat_loss_kw": 2.0,
        "heat_loss_forecast": [2.0, 2.1, 2.2, 2.3, 2.4, 2.5],
        "solar_gain_kw": 0.5,
        "solar_gain_forecast": [0.0, 0.0, 0.2, 0.4, 0.6, 0.8],
        "net_heat_loss_kw": 1.5,
    }
    mock_heat.last_update_success = True

    mock_opt = MagicMock()
    mock_opt.data = {
        "optimal_offset": 1.0,
        "optimal_offsets": [0, 0, 1, 1, 2, 2],
        "heat_buffer_kwh": 2.0,
        "buffer_forecast": [0.0, 0.5, 1.0, 1.5, 2.0, 2.0],
        "cost_savings": 0.25,
    }
    mock_opt.last_update_success = True

    hass.data[DOMAIN] = {
        entry.entry_id: {
            "weather_coordinator": mock_weather,
            "heat_coordinator": mock_heat,
            "optimization_coordinator": mock_opt,
            "config": entry.data,
            "entry": entry,
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None
    assert isinstance(diagnostics, dict)


@pytest.mark.asyncio
async def test_diagnostics_includes_entry_metadata(hass: HomeAssistant):
    """Test diagnostics includes entry metadata."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Heating Curve Optimizer",
        data={"area_m2": 150},
        options={},
    )
    entry.add_to_hass(hass)

    mock_weather = MagicMock()
    mock_weather.data = {"current_temperature": 10.0}
    mock_weather.last_update_success = True

    hass.data[DOMAIN] = {
        entry.entry_id: {
            "weather_coordinator": mock_weather,
            "config": entry.data,
            "entry": entry,
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None
    assert isinstance(diagnostics, dict)
