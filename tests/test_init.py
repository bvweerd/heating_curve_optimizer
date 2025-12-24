"""Test the __init__ module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_curve_optimizer import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
    _update_listener,
)
from custom_components.heating_curve_optimizer.const import DOMAIN, PLATFORMS


@pytest.mark.asyncio
async def test_async_setup(hass: HomeAssistant):
    """Test async_setup initializes domain data."""
    result = await async_setup(hass, {})
    assert result is True
    assert DOMAIN in hass.data
    assert isinstance(hass.data[DOMAIN], dict)


@pytest.mark.asyncio
async def test_async_setup_entry(hass: HomeAssistant):
    """Test async_setup_entry sets up coordinators and platforms."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "area_m2": 150,
            "energy_label": "C",
            "latitude": 52.0,
            "longitude": 5.0,
        },
        options={},
    )
    entry.add_to_hass(hass)

    # Mock coordinators
    with patch(
        "custom_components.heating_curve_optimizer.WeatherDataCoordinator"
    ) as mock_weather, patch(
        "custom_components.heating_curve_optimizer.HeatCalculationCoordinator"
    ) as mock_heat, patch(
        "custom_components.heating_curve_optimizer.OptimizationCoordinator"
    ) as mock_opt:

        # Setup mock coordinators
        weather_instance = MagicMock()
        weather_instance.async_config_entry_first_refresh = AsyncMock()
        mock_weather.return_value = weather_instance

        heat_instance = MagicMock()
        heat_instance.async_setup = AsyncMock()
        heat_instance.async_config_entry_first_refresh = AsyncMock()
        mock_heat.return_value = heat_instance

        opt_instance = MagicMock()
        opt_instance.async_setup = AsyncMock()
        opt_instance.async_request_refresh = AsyncMock()
        mock_opt.return_value = opt_instance

        # Mock platform forwarding
        with patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ) as mock_forward:

            result = await async_setup_entry(hass, entry)

            assert result is True
            assert entry.entry_id in hass.data[DOMAIN]
            assert "weather_coordinator" in hass.data[DOMAIN][entry.entry_id]
            assert "heat_coordinator" in hass.data[DOMAIN][entry.entry_id]
            assert "optimization_coordinator" in hass.data[DOMAIN][entry.entry_id]
            assert "config" in hass.data[DOMAIN][entry.entry_id]
            assert "entry" in hass.data[DOMAIN][entry.entry_id]
            assert "device" in hass.data[DOMAIN][entry.entry_id]

            # Verify coordinators were initialized
            weather_instance.async_config_entry_first_refresh.assert_called_once()
            heat_instance.async_setup.assert_called_once()
            heat_instance.async_config_entry_first_refresh.assert_called_once()
            opt_instance.async_setup.assert_called_once()

            # Verify platforms were forwarded
            mock_forward.assert_called_once_with(entry, PLATFORMS)


@pytest.mark.asyncio
async def test_async_unload_entry(hass: HomeAssistant):
    """Test async_unload_entry unloads coordinators and platforms."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
    )
    entry.add_to_hass(hass)

    # Setup mock data
    mock_heat_coordinator = MagicMock()
    mock_heat_coordinator.async_shutdown = AsyncMock()

    mock_opt_coordinator = MagicMock()
    mock_opt_coordinator.async_shutdown = AsyncMock()

    hass.data[DOMAIN] = {
        entry.entry_id: {
            "heat_coordinator": mock_heat_coordinator,
            "optimization_coordinator": mock_opt_coordinator,
        },
        "runtime": {entry.entry_id: {}},
    }

    # Mock platform unloading
    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ) as mock_unload:

        result = await async_unload_entry(hass, entry)

        assert result is True
        mock_heat_coordinator.async_shutdown.assert_called_once()
        mock_opt_coordinator.async_shutdown.assert_called_once()
        mock_unload.assert_called_once_with(entry, PLATFORMS)
        # After successful unload with runtime data, DOMAIN may still exist
        # Check that entry_id is removed
        if DOMAIN in hass.data:
            assert entry.entry_id not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_unload_entry_cleanup(hass: HomeAssistant):
    """Test async_unload_entry cleans up hass.data completely."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
    )
    entry.add_to_hass(hass)

    # Setup minimal data
    hass.data[DOMAIN] = {entry.entry_id: {}}

    # Mock platform unloading
    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ):
        result = await async_unload_entry(hass, entry)

        assert result is True
        # DOMAIN should be removed when empty
        assert DOMAIN not in hass.data


@pytest.mark.asyncio
async def test_async_unload_entry_failure(hass: HomeAssistant):
    """Test async_unload_entry when platform unload fails."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
    )
    entry.add_to_hass(hass)

    hass.data[DOMAIN] = {entry.entry_id: {}}

    # Mock platform unloading failure
    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=False)
    ):
        result = await async_unload_entry(hass, entry)

        assert result is False
        # Data should still be there on failure
        assert entry.entry_id in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_update_listener(hass: HomeAssistant):
    """Test _update_listener reloads the config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock()
    ) as mock_reload:
        await _update_listener(hass, entry)
        mock_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_async_setup_entry_merges_options_and_data(hass: HomeAssistant):
    """Test that options override data when present."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "area_m2": 100,
            "energy_label": "A",
        },
        options={
            "area_m2": 150,  # This should override data
        },
    )
    entry.add_to_hass(hass)

    # Mock coordinators
    with patch(
        "custom_components.heating_curve_optimizer.WeatherDataCoordinator"
    ) as mock_weather, patch(
        "custom_components.heating_curve_optimizer.HeatCalculationCoordinator"
    ) as mock_heat, patch(
        "custom_components.heating_curve_optimizer.OptimizationCoordinator"
    ) as mock_opt, patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ):

        weather_instance = MagicMock()
        weather_instance.async_config_entry_first_refresh = AsyncMock()
        mock_weather.return_value = weather_instance

        heat_instance = MagicMock()
        heat_instance.async_setup = AsyncMock()
        heat_instance.async_config_entry_first_refresh = AsyncMock()
        mock_heat.return_value = heat_instance

        opt_instance = MagicMock()
        opt_instance.async_setup = AsyncMock()
        mock_opt.return_value = opt_instance

        await async_setup_entry(hass, entry)

        # Verify merged config
        config = hass.data[DOMAIN][entry.entry_id]["config"]
        assert config["area_m2"] == 150  # From options
        assert config["energy_label"] == "A"  # From data


@pytest.mark.asyncio
async def test_async_unload_entry_with_entities(hass: HomeAssistant):
    """Test unload removes entities key."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
    )
    entry.add_to_hass(hass)

    hass.data[DOMAIN] = {
        entry.entry_id: {},
        "entities": {"some": "data"},
    }

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ):
        await async_unload_entry(hass, entry)

        # entities should be removed
        assert "entities" not in hass.data.get(DOMAIN, {})
