"""Test the diagnostics module."""

import pytest
from unittest.mock import MagicMock
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer import HeatingCurveOptimizerData
from custom_components.heating_curve_optimizer.calibration import (
    RESULT_FITTED,
    ThermalCalibrationState,
)
from custom_components.heating_curve_optimizer.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.heating_curve_optimizer.const import DOMAIN


def _default_coordinator():
    """A coordinator mock with no calibration set up - `_calibration` must
    be explicitly None rather than a MagicMock auto-attribute, so
    diagnostics' "never set up" branch is exercised, not a stub object."""
    coordinator = MagicMock()
    coordinator.data = None
    coordinator.last_update_success = False
    coordinator._calibration = None
    return coordinator


def _make_runtime_data(
    weather=None, heat=None, optimization=None, config=None, zones=None
) -> HeatingCurveOptimizerData:
    """Build a HeatingCurveOptimizerData from the coordinator mocks a test
    cares about, defaulting missing ones to an unavailable MagicMock."""

    return HeatingCurveOptimizerData(
        weather_coordinator=weather or _default_coordinator(),
        heat_coordinator=heat or _default_coordinator(),
        optimization_coordinator=optimization or _default_coordinator(),
        config=config or {},
        device=MagicMock(),
        zones=zones or {},
    )


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

    entry.runtime_data = _make_runtime_data(
        weather=mock_weather, heat=mock_heat, optimization=mock_opt, config=entry.data
    )

    # Get diagnostics
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None
    # Diagnostics should contain entry and coordinator info
    assert isinstance(diagnostics, dict)
    assert diagnostics["stored_data"]["weather"]["current_temperature"] == 10.0


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

    entry.runtime_data = _make_runtime_data(
        weather=mock_weather, heat=mock_heat, optimization=mock_opt, config=entry.data
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    # Diagnostics should be returned
    assert diagnostics is not None
    assert isinstance(diagnostics, dict)
    # The actual entity IDs must never appear in the redacted config data -
    # TO_REDACT is populated (was an empty set before this alignment pass).
    assert diagnostics["config_entry"]["data"]["consumption_price_sensor"] != (
        "sensor.price"
    )
    assert diagnostics["config_entry"]["data"]["production_price_sensor"] != (
        "sensor.price_production"
    )


@pytest.mark.asyncio
async def test_diagnostics_subentries_are_redacted_and_typed(hass: HomeAssistant):
    """Zone/gas-boiler/PV-array subentries must appear in the diagnostics
    dump (previously entirely absent) with their own entity-id fields
    redacted the same way the main entry's are."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C"},
        options={},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data(config=entry.data)

    class _FakeSubentry:
        def __init__(self, title, subentry_type, data):
            self.title = title
            self.subentry_type = subentry_type
            self.data = data

    entry.subentries = {
        "sub1": _FakeSubentry(
            "Living room",
            "heating_zone",
            {
                "name": "Living room",
                "area_m2": 20,
                "indoor_temperature_sensor": "sensor.living_room_temp",
            },
        ),
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    subentries = diagnostics["config_entry"]["subentries"]
    assert "Living room" in subentries
    assert subentries["Living room"]["type"] == "heating_zone"
    assert (
        subentries["Living room"]["data"]["indoor_temperature_sensor"]
        != "sensor.living_room_temp"
    )


@pytest.mark.asyncio
async def test_diagnostics_calibration_section_reflects_state(hass: HomeAssistant):
    """The `calibration` section must surface ThermalCalibrationState's own
    fields directly, without requiring a reader to cross-reference the
    `stored_data.optimization` blob by hand."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C"},
        options={},
    )
    entry.add_to_hass(hass)

    store = MagicMock()
    calibration = ThermalCalibrationState(store=store)
    calibration.last_result = RESULT_FITTED
    calibration.learned_ua_w_per_k = 410.0
    calibration.learned_thermal_mass_kwh_per_k = 11.5

    mock_opt = MagicMock()
    mock_opt.data = {"optimal_offset": 1.0}
    mock_opt.last_update_success = True
    mock_opt._calibration = calibration

    entry.runtime_data = _make_runtime_data(optimization=mock_opt, config=entry.data)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    primary_calibration = diagnostics["calibration"]["primary_zone"]
    assert primary_calibration["last_result"] == RESULT_FITTED
    assert primary_calibration["learned_ua_w_per_k"] == 410.0
    assert primary_calibration["learned_thermal_mass_kwh_per_k"] == 11.5
    assert primary_calibration["sample_count"] == 0
    assert primary_calibration["applied"] is False


@pytest.mark.asyncio
async def test_diagnostics_no_crash_without_primary_zone(hass: HomeAssistant):
    """No heating-zone subentry configured yet (see __init__.py's
    _find_primary_zone_subentry) - heat_coordinator/optimization_coordinator
    are None, a valid, if useless, state - diagnostics must not raise
    AttributeError on `.data`/`._calibration` and must report empty
    heat/optimization/calibration sections rather than crashing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = HeatingCurveOptimizerData(
        weather_coordinator=_default_coordinator(),
        heat_coordinator=None,
        optimization_coordinator=None,
        config={},
        device=MagicMock(),
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["stored_data"]["heat"] == {}
    assert diagnostics["stored_data"]["optimization"] == {}
    assert diagnostics["calibration"] == {}


@pytest.mark.asyncio
async def test_diagnostics_calibration_section_omits_unset_coordinators(
    hass: HomeAssistant,
):
    """A coordinator that never set up calibration (no real indoor sensor)
    must not appear in the `calibration` section at all - "never had the
    chance to learn" is distinct from "set up but empty"."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150},
        options={},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data(config=entry.data)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["calibration"] == {}


@pytest.mark.asyncio
async def test_diagnostics_with_missing_coordinators(hass: HomeAssistant):
    """Test diagnostics when some coordinators are missing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150},
        options={},
    )
    entry.add_to_hass(hass)

    # Only weather coordinator has data
    mock_weather = MagicMock()
    mock_weather.data = {"current_temperature": 10.0}
    mock_weather.last_update_success = True

    entry.runtime_data = _make_runtime_data(weather=mock_weather, config=entry.data)

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

    # Coordinators with failed status (data=None, last_update_success=False)
    entry.runtime_data = _make_runtime_data(config=entry.data)

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

    entry.runtime_data = _make_runtime_data(
        weather=mock_weather, heat=mock_heat, optimization=mock_opt, config=entry.data
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None


@pytest.mark.asyncio
async def test_diagnostics_no_runtime_data(hass: HomeAssistant):
    """Test diagnostics when the entry has no runtime_data (setup never
    completed) - should not raise, just report empty stored_data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150},
        options={},
    )
    entry.add_to_hass(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None
    assert diagnostics["stored_data"] == {}


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

    entry.runtime_data = _make_runtime_data(
        weather=mock_weather, heat=mock_heat, optimization=mock_opt, config=entry.data
    )

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

    entry.runtime_data = _make_runtime_data(weather=mock_weather, config=entry.data)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics is not None
    assert isinstance(diagnostics, dict)
    assert diagnostics["config_entry"]["title"] == "Heating Curve Optimizer"
