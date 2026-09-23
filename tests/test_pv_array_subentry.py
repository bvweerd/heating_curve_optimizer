"""Tests for the PV-array subentry (config_flow.py).

Same HA-version constraint as test_zone_subentry.py/test_gas_boiler_subentry.py:
`HeatingPvArraySubentryFlow` subclasses `homeassistant.config_entries.
ConfigSubentryFlow`, which does not exist in the HA release this repo's test
environment can install (2024.3.x). So `HeatingPvArraySubentryFlow` is `None`
here, and its step methods are unverified by this suite - what *is* verified
is the pure validation logic, the title-generation logic, the graceful-
degradation contract, and that all three subentry types are registered
together.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.heating_curve_optimizer.config_flow import (
    HeatingCurveOptimizerConfigFlow,
    HeatingPvArraySubentryFlow,
    _pv_array_subentry_title,
    _validate_pv_array_subentry,
)
from custom_components.heating_curve_optimizer.const import (
    GAS_SUBENTRY_TYPE,
    PV_SUBENTRY_TYPE,
    ZONE_SUBENTRY_TYPE,
)


def test_validate_pv_array_subentry_normalizes_input():
    data = _validate_pv_array_subentry(
        {
            "peak_power_kwp": "3.5",
            "orientation": "180",
            "tilt": "35",
            "efficiency_factor": "0.9",
            "dc_coupled": True,
        }
    )
    assert data["peak_power_kwp"] == 3.5
    assert data["orientation"] == 180.0
    assert data["tilt"] == 35.0
    assert data["efficiency_factor"] == 0.9
    assert data["dc_coupled"] is True
    assert "name" not in data


def test_validate_pv_array_subentry_defaults():
    data = _validate_pv_array_subentry({})
    assert data["peak_power_kwp"] == 1.0
    assert data["orientation"] == 180.0
    assert data["tilt"] == 35.0
    assert data["efficiency_factor"] == 0.85
    assert data["dc_coupled"] is False


def test_validate_pv_array_subentry_keeps_stripped_name():
    data = _validate_pv_array_subentry({"name": "  East roof  ", "peak_power_kwp": 2.0})
    assert data["name"] == "East roof"


def test_validate_pv_array_subentry_blank_name_omitted():
    data = _validate_pv_array_subentry({"name": "   "})
    assert "name" not in data


def test_validate_pv_array_subentry_rejects_non_positive_peak_power():
    with pytest.raises(vol.Invalid):
        _validate_pv_array_subentry({"peak_power_kwp": 0})
    with pytest.raises(vol.Invalid):
        _validate_pv_array_subentry({"peak_power_kwp": -1})


def test_validate_pv_array_subentry_rejects_orientation_out_of_range():
    with pytest.raises(vol.Invalid):
        _validate_pv_array_subentry({"orientation": -1})
    with pytest.raises(vol.Invalid):
        _validate_pv_array_subentry({"orientation": 361})


def test_validate_pv_array_subentry_rejects_tilt_out_of_range():
    with pytest.raises(vol.Invalid):
        _validate_pv_array_subentry({"tilt": -1})
    with pytest.raises(vol.Invalid):
        _validate_pv_array_subentry({"tilt": 91})


def test_pv_array_subentry_title_uses_name_when_given():
    assert (
        _pv_array_subentry_title({"name": "South roof", "peak_power_kwp": 3.0})
        == "South roof"
    )


def test_pv_array_subentry_title_falls_back_to_kwp_and_coupling():
    assert (
        _pv_array_subentry_title({"peak_power_kwp": 4.2, "dc_coupled": True})
        == "4.2 kWp DC"
    )
    assert (
        _pv_array_subentry_title({"peak_power_kwp": 2.0, "dc_coupled": False})
        == "2.0 kWp AC"
    )


def test_heating_pv_array_subentry_flow_is_none_on_this_ha_release():
    """Documents the environment constraint explained in the module
    docstring, so a future upgrade of pytest-homeassistant-custom-component
    that starts providing ConfigSubentryFlow is a visible, deliberate
    change here rather than a silent one."""
    assert HeatingPvArraySubentryFlow is None


def test_async_get_supported_subentry_types_returns_empty_dict_gracefully():
    result = HeatingCurveOptimizerConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert result == {}


def test_all_three_subentry_types_share_the_same_ha_version_gate():
    """ZONE_SUBENTRY_TYPE, GAS_SUBENTRY_TYPE and PV_SUBENTRY_TYPE are
    distinct keys, and all three subentry flow classes are None-gated by
    the exact same HA-version check, so
    async_get_supported_subentry_types is consistent (empty for all
    three, or populated for all three) rather than mixing states."""
    assert len({ZONE_SUBENTRY_TYPE, GAS_SUBENTRY_TYPE, PV_SUBENTRY_TYPE}) == 3
    result = HeatingCurveOptimizerConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert ZONE_SUBENTRY_TYPE not in result
    assert GAS_SUBENTRY_TYPE not in result
    assert PV_SUBENTRY_TYPE not in result
