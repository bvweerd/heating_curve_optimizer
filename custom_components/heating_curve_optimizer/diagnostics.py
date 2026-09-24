"""Diagnostics support for the Heating Curve Optimizer integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_GAS_PRICE_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_POWER_CONSUMPTION,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
)

# Sensor entity IDs may be considered private; redact them. Every config key
# that holds an entity ID (or a list of them) belongs here - diagnostics get
# pasted into public issue trackers. Mirrors battery_controller's own
# TO_REDACT/its comment: that file recorded a real past bug where an entry
# was misspelled and silently redacted nothing, which is why this list is
# built from the constants rather than from literal strings.
TO_REDACT: set[str] = {
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_GAS_PRICE_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_POWER_CONSUMPTION,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
}


def _serialize_state(state: State | None) -> dict[str, Any]:
    """Serialize a Home Assistant state for diagnostics output."""

    if state is None:
        return {}

    return {
        "state": state.state,
        "attributes": dict(state.attributes),
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
        "context": {
            "id": state.context.id,
            "parent_id": state.context.parent_id,
            "user_id": state.context.user_id,
        },
    }


def _serialize_mapping(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a serialisable copy of a mapping."""

    if not data:
        return {}

    return {key: value for key, value in data.items()}


def _serialize_calibration(optimization_coordinator: Any) -> dict[str, Any] | None:
    """A coordinator's thermal-calibration state, or None if not set up."""

    calibration = getattr(optimization_coordinator, "thermal_calibration", None)
    if calibration is None:
        return None
    return {
        "sample_count": calibration.sample_count,
        "applied": calibration.applied,
        "last_result": calibration.last_result,
        "learned_ua_w_per_k": calibration.learned_ua_w_per_k,
        "learned_thermal_mass_kwh_per_k": calibration.learned_thermal_mass_kwh_per_k,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics information for a config entry."""

    runtime_data = getattr(entry, "runtime_data", None)
    stored_data: dict[str, Any] = {}
    calibration_data: dict[str, Any] = {}
    if runtime_data is not None:
        stored_data = {
            "config": async_redact_data(dict(runtime_data.config), TO_REDACT),
            "weather": (
                _serialize_mapping(runtime_data.weather_coordinator.data)
                if runtime_data.weather_coordinator.data
                else {}
            ),
            "heat": (
                _serialize_mapping(runtime_data.heat_coordinator.data)
                if runtime_data.heat_coordinator is not None
                and runtime_data.heat_coordinator.data
                else {}
            ),
            "optimization": (
                _serialize_mapping(runtime_data.optimization_coordinator.data)
                if runtime_data.optimization_coordinator is not None
                and runtime_data.optimization_coordinator.data
                else {}
            ),
            "zones": sorted(runtime_data.zones.keys()),
        }

        primary_calibration = _serialize_calibration(
            runtime_data.optimization_coordinator
        )
        if primary_calibration is not None:
            calibration_data["primary_zone"] = primary_calibration
        for zone_id, zone_data in runtime_data.zones.items():
            zone_optimization_coordinator = zone_data.get("optimization_coordinator")
            zone_calibration = _serialize_calibration(zone_optimization_coordinator)
            if zone_calibration is not None:
                calibration_data[zone_id] = zone_calibration

    ent_reg = er.async_get(hass)

    # Entity state/attributes come from hass.states rather than the entity
    # object itself: diagnostics only needs the same data a Lovelace card or
    # automation would see (the state machine), not a live entity reference,
    # which would tie this module to platform-loading order.
    sensors: list[dict[str, Any]] = []
    for ent_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent_entry.domain != "sensor":
            continue

        state = hass.states.get(ent_entry.entity_id)

        sensors.append(
            {
                "entity_id": ent_entry.entity_id,
                "unique_id": ent_entry.unique_id,
                "original_name": ent_entry.original_name,
                "available": state is not None
                and state.state not in ("unknown", "unavailable"),
                "device_class": ent_entry.device_class,
                "state_class": (
                    ent_entry.capabilities.get("state_class")
                    if ent_entry.capabilities
                    else None
                ),
                "state": _serialize_state(state),
                "extra_state_attributes": _serialize_mapping(
                    state.attributes if state else None
                ),
            }
        )

    diagnostics: dict[str, Any] = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "subentries": {
                sub.title: {
                    "type": sub.subentry_type,
                    "data": async_redact_data(dict(sub.data), TO_REDACT),
                }
                for sub in getattr(entry, "subentries", {}).values()
            },
        },
        "stored_data": stored_data,
        "calibration": calibration_data,
        "sensors": sensors,
    }

    return diagnostics
