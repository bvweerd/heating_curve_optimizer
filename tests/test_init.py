"""Test the __init__ module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.heating_curve_optimizer import (
    HeatingCurveOptimizerData,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    _update_listener,
)
from custom_components.heating_curve_optimizer.const import DOMAIN, PLATFORMS


def _mock_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()
    return coordinator


def _make_runtime_data(**overrides) -> HeatingCurveOptimizerData:
    """Build a minimal HeatingCurveOptimizerData for tests that only care
    about a subset of its fields."""
    defaults = dict(
        weather_coordinator=_mock_coordinator(),
        heat_coordinator=_mock_coordinator(),
        optimization_coordinator=_mock_coordinator(),
        config={},
        device=MagicMock(),
    )
    defaults.update(overrides)
    return HeatingCurveOptimizerData(**defaults)


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
            runtime_data = entry.runtime_data
            assert runtime_data.weather_coordinator is weather_instance
            assert runtime_data.heat_coordinator is heat_instance
            assert runtime_data.optimization_coordinator is opt_instance
            assert runtime_data.config == {**entry.data, **entry.options}
            assert runtime_data.device is not None

            # Verify coordinators were initialized
            weather_instance.async_config_entry_first_refresh.assert_called_once()
            heat_instance.async_setup.assert_called_once()
            heat_instance.async_config_entry_first_refresh.assert_called_once()
            opt_instance.async_setup.assert_called_once()

            # Verify platforms were forwarded
            mock_forward.assert_called_once_with(entry, PLATFORMS)


@pytest.mark.asyncio
async def test_async_setup_entry_raises_config_entry_not_ready_on_weather_failure(
    hass: HomeAssistant,
):
    """quality_scale's test-before-setup rule: a coordinator failure during
    first refresh must surface as ConfigEntryNotReady (which HA retries
    later), not a raw exception or a silently broken entry.

    WeatherDataCoordinator/HeatCalculationCoordinator are both set up with
    `async_config_entry_first_refresh()`, which already converts an
    `UpdateFailed` from `_async_update_data` into `ConfigEntryNotReady`
    (homeassistant.helpers.update_coordinator, verified against the
    installed HA release) - this test proves __init__.py lets that
    exception propagate out of async_setup_entry rather than swallowing it.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C"},
        options={},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.heating_curve_optimizer.coordinator."
        "WeatherDataCoordinator._async_update_data",
        new=AsyncMock(side_effect=UpdateFailed("simulated open-meteo.com outage")),
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


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

    entry.runtime_data = _make_runtime_data(
        heat_coordinator=mock_heat_coordinator,
        optimization_coordinator=mock_opt_coordinator,
    )
    hass.data[DOMAIN] = {"runtime": {entry.entry_id: {}}}

    # Mock platform unloading
    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ) as mock_unload:
        result = await async_unload_entry(hass, entry)

        assert result is True
        mock_heat_coordinator.async_shutdown.assert_called_once()
        mock_opt_coordinator.async_shutdown.assert_called_once()
        mock_unload.assert_called_once_with(entry, PLATFORMS)
        # The "runtime" manual-override dict is popped for this entry_id too.
        if DOMAIN in hass.data:
            assert entry.entry_id not in hass.data[DOMAIN].get("runtime", {})


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
    entry.runtime_data = _make_runtime_data()
    hass.data[DOMAIN] = {}

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

    entry.runtime_data = _make_runtime_data()
    hass.data[DOMAIN] = {}

    # Mock platform unloading failure
    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=False)
    ):
        result = await async_unload_entry(hass, entry)

        assert result is False
        # runtime_data should still be there on failure
        assert entry.runtime_data is not None


@pytest.mark.asyncio
async def test_update_listener(hass: HomeAssistant):
    """Test _update_listener reloads the config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={"area_m2": 150},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data(options={"area_m2": 100})

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
        config = entry.runtime_data.config
        assert config["area_m2"] == 150  # From options
        assert config["energy_label"] == "A"  # From data


@pytest.mark.asyncio
async def test_async_unload_entry_clears_runtime_override(hass: HomeAssistant):
    """Test unload removes this entry's manual-override runtime dict."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
    )
    entry.add_to_hass(hass)

    entry.runtime_data = _make_runtime_data()
    hass.data[DOMAIN] = {"runtime": {entry.entry_id: {"target_indoor_temp": 20.0}}}

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ):
        await async_unload_entry(hass, entry)

        assert entry.entry_id not in hass.data.get(DOMAIN, {}).get("runtime", {})
