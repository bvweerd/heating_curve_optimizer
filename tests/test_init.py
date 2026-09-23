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
from custom_components.heating_curve_optimizer.const import (
    DOMAIN,
    PLATFORMS,
    ZONE_SUBENTRY_TYPE,
)


def _mock_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()
    return coordinator


def _zone_subentry(title: str = "Living room", **data: object) -> MagicMock:
    """A mock ZONE_SUBENTRY_TYPE subentry - the primary-zone-selection
    logic (_find_primary_zone_subentry) only reads `.subentry_type`,
    `.title` and `.data`, the same shape every real HA subentry has."""
    subentry = MagicMock()
    subentry.subentry_type = ZONE_SUBENTRY_TYPE
    subentry.title = title
    subentry.data = {"area_m2": 20.0, "energy_label": "C", **data}
    return subentry


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
            "latitude": 52.0,
            "longitude": 5.0,
        },
        options={},
    )
    entry.add_to_hass(hass)
    entry.subentries = {"zone1": _zone_subentry()}

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
            assert runtime_data.config == {
                **entry.data,
                **entry.options,
                "pv_arrays": [],
            }
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
async def test_async_setup_entry_zero_zones_leaves_coordinators_none(
    hass: HomeAssistant,
):
    """No heating-zone subentry configured yet is a valid, if useless,
    state (matches battery_controller's own "zero batteries" precedent) -
    setup must not raise, `device` must still exist so the user has
    somewhere to add their first zone from, but heat_coordinator/
    optimization_coordinator stay None since there is no zone to build
    them from."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"latitude": 52.0, "longitude": 5.0},
        options={},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.heating_curve_optimizer.WeatherDataCoordinator"
    ) as mock_weather, patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ):
        weather_instance = MagicMock()
        weather_instance.async_config_entry_first_refresh = AsyncMock()
        mock_weather.return_value = weather_instance

        result = await async_setup_entry(hass, entry)

        assert result is True
        assert entry.runtime_data.heat_coordinator is None
        assert entry.runtime_data.optimization_coordinator is None
        assert entry.runtime_data.device is not None
        assert entry.runtime_data.zones == {}


@pytest.mark.asyncio
async def test_async_setup_entry_one_zone_becomes_primary_with_entry_identity(
    hass: HomeAssistant,
):
    """A single zone subentry becomes the primary zone and keeps the
    entry's own (non-zone-suffixed) identity - unique_ids/calibration
    storage keys stay exactly as they were before this zone lived in a
    subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"latitude": 52.0, "longitude": 5.0},
        options={},
    )
    entry.add_to_hass(hass)
    entry.subentries = {"zone1": _zone_subentry(area_m2=42.0)}

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

        assert entry.runtime_data.heat_coordinator is heat_instance
        assert entry.runtime_data.optimization_coordinator is opt_instance
        # entry.entry_id, not a zone-suffixed one:
        call_args = mock_heat.call_args
        assert call_args.args[0] is hass
        assert call_args.args[1] is weather_instance
        assert call_args.args[2]["area_m2"] == 42.0
        assert call_args.args[3] == entry.entry_id
        # The primary zone must not also appear in the "extra zones" loop.
        assert entry.runtime_data.zones == {}


@pytest.mark.asyncio
async def test_async_setup_entry_second_zone_goes_through_extra_zone_loop(
    hass: HomeAssistant,
):
    """The first zone subentry becomes primary; a second one goes through
    the ordinary "extra zone" loop, unchanged - its own suffixed identity,
    its own device, its own coordinator pair."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"latitude": 52.0, "longitude": 5.0},
        options={},
    )
    entry.add_to_hass(hass)
    entry.subentries = {
        "zone1": _zone_subentry(title="Living room"),
        "zone2": _zone_subentry(title="Bedroom"),
    }

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

        for mock_cls in (mock_heat, mock_opt):
            instance = MagicMock()
            instance.async_setup = AsyncMock()
            instance.async_config_entry_first_refresh = AsyncMock()
            mock_cls.return_value = instance

        await async_setup_entry(hass, entry)

        # Primary zone ("zone1") is excluded from the extra-zone loop -
        # only "zone2" ends up there.
        assert list(entry.runtime_data.zones.keys()) == ["zone2"]
        zone2 = entry.runtime_data.zones["zone2"]
        assert zone2["name"] == "Bedroom"
        assert zone2["device"]["via_device"] == (DOMAIN, entry.entry_id)


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


@pytest.mark.asyncio
async def test_async_setup_entry_gas_boiler_absent_by_default(hass: HomeAssistant):
    """Core zero-impact regression guard: without a gas_boiler subentry,
    async_setup_entry must not create a GasBoilerCoordinator or device at
    all - nothing changes for the overwhelming majority of installations
    that haven't configured the hybrid comparison."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C", "latitude": 52.0, "longitude": 5.0},
        options={},
    )
    entry.add_to_hass(hass)

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

        assert entry.runtime_data.gas_boiler_coordinator is None
        assert entry.runtime_data.gas_boiler_device is None
        assert entry.runtime_data.gas_boiler_subentry_id is None


@pytest.mark.asyncio
async def test_async_setup_entry_creates_gas_boiler_coordinator_when_subentry_present(
    hass: HomeAssistant,
):
    """A configured gas_boiler subentry must produce a GasBoilerCoordinator
    with the merged (main entry + subentry) config."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C", "latitude": 52.0, "longitude": 5.0},
        options={},
    )
    entry.add_to_hass(hass)

    mock_subentry = MagicMock()
    mock_subentry.subentry_type = "gas_boiler"
    mock_subentry.title = "Gas Boiler"
    mock_subentry.data = {
        "gas_price_sensor": "sensor.gas_price",
        "gas_boiler_efficiency": 0.9,
        "gas_calorific_value_kwh_per_m3": 9.77,
    }
    entry.subentries = {"gas_sub_1": mock_subentry, "zone1": _zone_subentry()}

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

        assert entry.runtime_data.gas_boiler_coordinator is not None
        assert entry.runtime_data.gas_boiler_device is not None
        assert entry.runtime_data.gas_boiler_subentry_id == "gas_sub_1"
        assert (
            entry.runtime_data.gas_boiler_coordinator.config["gas_price_sensor"]
            == "sensor.gas_price"
        )


@pytest.mark.asyncio
async def test_async_unload_entry_shuts_down_gas_boiler_coordinator_when_present(
    hass: HomeAssistant,
):
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)

    gas_boiler_coordinator = _mock_coordinator()
    entry.runtime_data = _make_runtime_data(
        gas_boiler_coordinator=gas_boiler_coordinator
    )

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ):
        await async_unload_entry(hass, entry)

        gas_boiler_coordinator.async_shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_async_unload_entry_noop_when_gas_boiler_coordinator_absent(
    hass: HomeAssistant,
):
    """Must not raise when gas_boiler_coordinator is None (the default)."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)

    entry.runtime_data = _make_runtime_data()

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ):
        result = await async_unload_entry(hass, entry)

        assert result is True
