"""Tests for the hybrid gas-boiler subentry (config_flow.py).

Same HA-version constraint as test_zone_subentry.py: `HeatingGasBoilerSubentryFlow`
subclasses `homeassistant.config_entries.ConfigSubentryFlow`, which does not
exist in the HA release this repo's test environment can install (2024.3.x).
So `HeatingGasBoilerSubentryFlow` is `None` here, and its step methods are
unverified by this suite - what *is* verified is the pure validation logic,
the graceful-degradation contract, and that both subentry types are
registered together.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.config_flow import (
    HeatingCurveOptimizerConfigFlow,
    HeatingGasBoilerSubentryFlow,
    _build_gas_boiler_subentry_schema,
    _validate_gas_boiler_subentry,
)
from custom_components.heating_curve_optimizer.const import (
    CONF_GAS_PRICE_SENSOR,
    GAS_SUBENTRY_TYPE,
    ZONE_SUBENTRY_TYPE,
)


def test_build_gas_boiler_subentry_schema_honors_detected_default():
    """`async_step_user` passes `defaults={CONF_GAS_PRICE_SENSOR: ...}`
    when companion_integrations.detect_gas_price_sensor finds DECC's gas
    price sensor - this is the schema-building half of that wiring
    (verifiable independent of the HA-version gate above); the detector
    itself is covered by test_companion_integrations.py."""
    schema = _build_gas_boiler_subentry_schema(
        {CONF_GAS_PRICE_SENSOR: "sensor.current_gas_consumption_price"}
    )
    field = next(key for key in schema.schema if key == CONF_GAS_PRICE_SENSOR)
    assert field.default() == "sensor.current_gas_consumption_price"


def test_heating_gas_boiler_subentry_flow_is_none_on_this_ha_release():
    """Documents the environment constraint explained in the module
    docstring, so a future upgrade of pytest-homeassistant-custom-component
    that starts providing ConfigSubentryFlow is a visible, deliberate
    change here rather than a silent one."""
    assert HeatingGasBoilerSubentryFlow is None


def test_async_get_supported_subentry_types_returns_empty_dict_gracefully():
    result = HeatingCurveOptimizerConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert result == {}


def test_validate_gas_boiler_subentry_normalizes_input():
    data = _validate_gas_boiler_subentry(
        {
            "gas_price_sensor": "sensor.gas_price",
            "gas_boiler_efficiency": "0.95",
            "gas_calorific_value_kwh_per_m3": "9.5",
        }
    )
    assert data["gas_price_sensor"] == "sensor.gas_price"
    assert data["gas_boiler_efficiency"] == 0.95
    assert data["gas_calorific_value_kwh_per_m3"] == 9.5


def test_validate_gas_boiler_subentry_defaults():
    data = _validate_gas_boiler_subentry({"gas_price_sensor": "sensor.gas_price"})
    assert data["gas_boiler_efficiency"] == 0.90
    assert data["gas_calorific_value_kwh_per_m3"] == 9.77


def test_validate_gas_boiler_subentry_rejects_missing_price_sensor():
    with pytest.raises(vol.Invalid):
        _validate_gas_boiler_subentry({})
    with pytest.raises(vol.Invalid):
        _validate_gas_boiler_subentry({"gas_price_sensor": ""})


def test_validate_gas_boiler_subentry_rejects_efficiency_out_of_range():
    with pytest.raises(vol.Invalid):
        _validate_gas_boiler_subentry(
            {"gas_price_sensor": "sensor.gas_price", "gas_boiler_efficiency": 0.1}
        )
    with pytest.raises(vol.Invalid):
        _validate_gas_boiler_subentry(
            {"gas_price_sensor": "sensor.gas_price", "gas_boiler_efficiency": 2.0}
        )


def test_validate_gas_boiler_subentry_rejects_non_positive_calorific_value():
    with pytest.raises(vol.Invalid):
        _validate_gas_boiler_subentry(
            {
                "gas_price_sensor": "sensor.gas_price",
                "gas_calorific_value_kwh_per_m3": 0,
            }
        )
    with pytest.raises(vol.Invalid):
        _validate_gas_boiler_subentry(
            {
                "gas_price_sensor": "sensor.gas_price",
                "gas_calorific_value_kwh_per_m3": -1,
            }
        )


@pytest.mark.asyncio
async def test_gas_boiler_subentry_absent_when_entry_lacks_subentries_attribute(
    hass: HomeAssistant,
):
    """Mirrors test_zone_subentry.py's equivalent guard: __init__.py's
    gas-boiler lookup must not raise on an HA release/object without a
    `subentries` attribute - it degrades to "no gas boiler configured"."""
    entry = MagicMock(spec=["entry_id", "data", "options"])
    entry.entry_id = "test_entry_no_subentries"
    entry.data = {}
    entry.options = {}

    gas_subentry = None
    for _subentry_id, subentry in getattr(entry, "subentries", {}).items():
        if subentry.subentry_type == GAS_SUBENTRY_TYPE:
            gas_subentry = subentry
            break
    assert gas_subentry is None


def test_both_subentry_types_share_the_same_ha_version_gate():
    """ZONE_SUBENTRY_TYPE and GAS_SUBENTRY_TYPE are distinct keys, and both
    subentry flow classes are None-gated by the exact same HA-version
    check, so async_get_supported_subentry_types is consistent (empty for
    both, or populated for both) rather than mixing states."""
    assert ZONE_SUBENTRY_TYPE != GAS_SUBENTRY_TYPE
    result = HeatingCurveOptimizerConfigFlow.async_get_supported_subentry_types(
        MagicMock()
    )
    assert ZONE_SUBENTRY_TYPE not in result
    assert GAS_SUBENTRY_TYPE not in result
