"""Tests for the PV-array subentry (config_flow.py).

`HeatingPvArraySubentryFlow` subclasses `homeassistant.config_entries.
ConfigSubentryFlow`, which IS available in this HA version. So
`HeatingPvArraySubentryFlow` is a real class here, and
`async_get_supported_subentry_types` returns all three subentry types.

What is verified here:
- HeatingPvArraySubentryFlow is defined (not None)
- async_get_supported_subentry_types includes PV_SUBENTRY_TYPE
- the pure validation logic and title-generation logic
- that all three subentry types are registered together
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.heating_curve_optimizer.companion_integrations import (
    DetectedPvArray,
)
from custom_components.heating_curve_optimizer.config_flow import (
    HeatingCurveOptimizerConfigFlow,
    HeatingPvArraySubentryFlow,
    _PV_IMPORT_CHOICE_MANUAL,
    _build_pv_array_import_schema,
    _detected_pv_array_to_defaults,
    _pv_array_import_choice_label,
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


def _array(
    name: str = "South roof",
    peak_power_kwp: float = 3.0,
    orientation: float = 180.0,
    tilt: float = 35.0,
    efficiency_factor: float = 0.85,
    dc_coupled: bool = False,
) -> DetectedPvArray:
    return DetectedPvArray(
        name=name,
        peak_power_kwp=peak_power_kwp,
        orientation=orientation,
        tilt=tilt,
        efficiency_factor=efficiency_factor,
        dc_coupled=dc_coupled,
        source="Battery Controller",
    )


def test_detected_pv_array_to_defaults_maps_fields_directly():
    """A straight pass-through onto this integration's own field names -
    no remapping - except `name`, which is deliberately not copied (see
    _detected_pv_array_to_defaults's docstring)."""
    defaults = _detected_pv_array_to_defaults(
        _array(
            name="East roof",
            peak_power_kwp=4.2,
            orientation=90.0,
            tilt=30.0,
            efficiency_factor=0.9,
            dc_coupled=True,
        )
    )
    assert defaults == {
        "peak_power_kwp": 4.2,
        "orientation": 90.0,
        "tilt": 30.0,
        "efficiency_factor": 0.9,
        "dc_coupled": True,
    }
    assert "name" not in defaults


def test_pv_array_import_choice_label_uses_name_and_kwp():
    label = _pv_array_import_choice_label(
        0, _array(name="South roof", peak_power_kwp=3.5)
    )
    assert "South roof" in label
    assert "3.5" in label
    assert "Battery Controller" in label


def test_pv_array_import_choice_label_falls_back_when_unnamed():
    label = _pv_array_import_choice_label(2, _array(name=""))
    assert "Array 3" in label


def test_build_pv_array_import_schema_always_offers_manual_entry():
    schema = _build_pv_array_import_schema([_array()])
    # vol.Schema wraps a dict of {marker: validator}; the "import_choice"
    # key must be present and default to manual entry, not to importing
    # the (only) detected array - importing should be an explicit choice.
    (marker,) = (k for k in schema.schema if str(k) == "import_choice")
    assert marker.default() == _PV_IMPORT_CHOICE_MANUAL


def test_heating_pv_array_subentry_flow_is_available():
    """ConfigSubentryFlow is available in this HA version, so
    HeatingPvArraySubentryFlow must be a real class (not None)."""
    assert HeatingPvArraySubentryFlow is not None


def test_async_get_supported_subentry_types_includes_pv_array():
    result = HeatingCurveOptimizerConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert PV_SUBENTRY_TYPE in result


def test_all_three_subentry_types_share_the_same_ha_version_gate():
    """ZONE_SUBENTRY_TYPE, GAS_SUBENTRY_TYPE and PV_SUBENTRY_TYPE are
    distinct keys, and all three subentry flow classes are gated by the
    exact same HA-version check, so async_get_supported_subentry_types
    is consistent (populated for all three) rather than mixing states."""
    assert len({ZONE_SUBENTRY_TYPE, GAS_SUBENTRY_TYPE, PV_SUBENTRY_TYPE}) == 3
    result = HeatingCurveOptimizerConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert ZONE_SUBENTRY_TYPE in result
    assert GAS_SUBENTRY_TYPE in result
    assert PV_SUBENTRY_TYPE in result
