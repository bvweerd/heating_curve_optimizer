"""Diagnostics support for the Heating Curve Optimizer integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .calibration import ThermalCalibrationState
from .const import (
    CONF_ACCURACY_HORIZON_HOURS,
    CONF_CALIBRATION_MAX_HOURS,
    CONF_CALIBRATION_MAX_JUMP_C,
    CONF_CALIBRATION_MIN_HOURS,
    CONF_CALIBRATION_WINDOW,
    CONF_COMFORT_LOOKAHEAD_HOURS,
    CONF_COMFORT_PENALTY_WEIGHT,
    CONF_COMFORT_TOLERANCE_C,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_COP_SCALE_BOUNDS_LOWER,
    CONF_COP_SCALE_BOUNDS_UPPER,
    CONF_CYCLING_PENALTY_WEIGHT,
    CONF_DEFROST_BASE_PENALTY,
    CONF_DEFROST_COLD_THRESHOLD,
    CONF_DEFROST_FREE_THRESHOLD,
    CONF_DEFROST_MIN_COP_MULTIPLIER,
    CONF_EMITTER_EXPONENT_BOUNDS_LOWER,
    CONF_EMITTER_EXPONENT_BOUNDS_UPPER,
    CONF_FEED_IN_PRICE_FALLBACK,
    CONF_GAS_MIN_WINDOW_HOURS,
    CONF_GAS_PRICE_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_GROUND_ALBEDO,
    CONF_HARD_FLOOR_PENALTY,
    CONF_HEATPUMP_HEADROOM,
    CONF_IDLE_POWER_THRESHOLD_KW,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_INTERNAL_GAIN_MAX_W_PER_M2,
    CONF_MIN_COP,
    CONF_MIN_COP_SAMPLES,
    CONF_MIN_EMITTER_SAMPLES,
    CONF_MIN_INDOOR_TEMP_DELTA,
    CONF_MIN_R_SQUARED,
    CONF_MIN_RESIDUAL_SAMPLES,
    CONF_MIN_RUNNING_POWER_KW,
    CONF_MIN_SAMPLES_TO_APPLY,
    CONF_MIN_SHARE_EACH_DIRECTION,
    CONF_MWH_MAGNITUDE_THRESHOLD,
    CONF_OFFSET_MAX,
    CONF_OFFSET_MIN,
    CONF_POWER_CONSUMPTION,
    CONF_PRICE_CHANGE_MIN_ABS,
    CONF_PRICE_CHANGE_REL,
    CONF_PRIOR_STRENGTH,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_RATIO_BOUNDS_LOWER,
    CONF_RATIO_BOUNDS_UPPER,
    CONF_SOLAR_FACTOR_BOUNDS_UPPER,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_TWO_MASS_AUTOCORRELATION,
    CONF_WINDOW_SHGC,
    DEFAULT_ACCURACY_HORIZON_HOURS,
    DEFAULT_CALIBRATION_MAX_HOURS,
    DEFAULT_CALIBRATION_MAX_JUMP_C,
    DEFAULT_CALIBRATION_MIN_HOURS,
    DEFAULT_CALIBRATION_WINDOW,
    DEFAULT_COMFORT_LOOKAHEAD_HOURS,
    DEFAULT_COMFORT_PENALTY_WEIGHT,
    DEFAULT_COMFORT_TOLERANCE_C,
    DEFAULT_COP_SCALE_BOUNDS_LOWER,
    DEFAULT_COP_SCALE_BOUNDS_UPPER,
    DEFAULT_CYCLING_PENALTY_WEIGHT,
    DEFAULT_DEFROST_BASE_PENALTY,
    DEFAULT_DEFROST_COLD_THRESHOLD,
    DEFAULT_DEFROST_FREE_THRESHOLD,
    DEFAULT_DEFROST_MIN_COP_MULTIPLIER,
    DEFAULT_EMITTER_EXPONENT_BOUNDS_LOWER,
    DEFAULT_EMITTER_EXPONENT_BOUNDS_UPPER,
    DEFAULT_FEED_IN_PRICE_FALLBACK,
    DEFAULT_GAS_MIN_WINDOW_HOURS,
    DEFAULT_GROUND_ALBEDO,
    DEFAULT_HARD_FLOOR_PENALTY,
    DEFAULT_HEATPUMP_HEADROOM,
    DEFAULT_IDLE_POWER_THRESHOLD_KW,
    DEFAULT_INTERNAL_GAIN_MAX_W_PER_M2,
    DEFAULT_MIN_COP,
    DEFAULT_MIN_COP_SAMPLES,
    DEFAULT_MIN_EMITTER_SAMPLES,
    DEFAULT_MIN_INDOOR_TEMP_DELTA,
    DEFAULT_MIN_R_SQUARED,
    DEFAULT_MIN_RESIDUAL_SAMPLES,
    DEFAULT_MIN_RUNNING_POWER_KW,
    DEFAULT_MIN_SAMPLES_TO_APPLY,
    DEFAULT_MIN_SHARE_EACH_DIRECTION,
    DEFAULT_MWH_MAGNITUDE_THRESHOLD,
    DEFAULT_OFFSET_MAX,
    DEFAULT_OFFSET_MIN,
    DEFAULT_PRICE_CHANGE_MIN_ABS,
    DEFAULT_PRICE_CHANGE_REL,
    DEFAULT_PRIOR_STRENGTH,
    DEFAULT_RATIO_BOUNDS_LOWER,
    DEFAULT_RATIO_BOUNDS_UPPER,
    DEFAULT_SOLAR_FACTOR_BOUNDS_UPPER,
    DEFAULT_TWO_MASS_AUTOCORRELATION,
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
    if not isinstance(calibration, ThermalCalibrationState):
        return None
    return {
        "sample_count": calibration.sample_count,
        "ready": calibration.ready,
        "last_result": calibration.last_result,
        "exclusions": dict(calibration.exclusions),
        "fit": asdict(calibration.fit) if calibration.fit else None,
        "cop_fit": asdict(calibration.cop_fit) if calibration.cop_fit else None,
        "emitter_fit": (
            asdict(calibration.emitter_fit) if calibration.emitter_fit else None
        ),
        "samples": [s.as_list() for s in calibration.samples],
        "cop_samples": [s.as_list() for s in calibration.cop_samples],
        "emitter_samples": [s.as_list() for s in calibration.emitter_samples],
    }


# Expert settings: (CONF_key, DEFAULT_value, description).
_EXPERT_DEFAULTS: list[tuple[str, Any, str]] = [
    (CONF_COMFORT_PENALTY_WEIGHT, DEFAULT_COMFORT_PENALTY_WEIGHT, "comfort penalty"),
    (CONF_CYCLING_PENALTY_WEIGHT, DEFAULT_CYCLING_PENALTY_WEIGHT, "cycling penalty"),
    (CONF_HARD_FLOOR_PENALTY, DEFAULT_HARD_FLOOR_PENALTY, "hard floor penalty"),
    (CONF_FEED_IN_PRICE_FALLBACK, DEFAULT_FEED_IN_PRICE_FALLBACK, "feed-in fallback"),
    (CONF_OFFSET_MIN, DEFAULT_OFFSET_MIN, "offset min"),
    (CONF_OFFSET_MAX, DEFAULT_OFFSET_MAX, "offset max"),
    (CONF_HEATPUMP_HEADROOM, DEFAULT_HEATPUMP_HEADROOM, "HP headroom"),
    (CONF_IDLE_POWER_THRESHOLD_KW, DEFAULT_IDLE_POWER_THRESHOLD_KW, "idle threshold"),
    (CONF_PRICE_CHANGE_REL, DEFAULT_PRICE_CHANGE_REL, "price change rel"),
    (CONF_PRICE_CHANGE_MIN_ABS, DEFAULT_PRICE_CHANGE_MIN_ABS, "price change abs"),
    (CONF_MIN_RUNNING_POWER_KW, DEFAULT_MIN_RUNNING_POWER_KW, "min running power"),
    (CONF_ACCURACY_HORIZON_HOURS, DEFAULT_ACCURACY_HORIZON_HOURS, "accuracy horizon"),
    (CONF_MWH_MAGNITUDE_THRESHOLD, DEFAULT_MWH_MAGNITUDE_THRESHOLD, "MWh threshold"),
    (CONF_CALIBRATION_WINDOW, DEFAULT_CALIBRATION_WINDOW, "calibration window"),
    (CONF_MIN_SAMPLES_TO_APPLY, DEFAULT_MIN_SAMPLES_TO_APPLY, "min samples"),
    (CONF_MIN_R_SQUARED, DEFAULT_MIN_R_SQUARED, "min R²"),
    (CONF_MIN_SHARE_EACH_DIRECTION, DEFAULT_MIN_SHARE_EACH_DIRECTION, "min share"),
    (CONF_MIN_INDOOR_TEMP_DELTA, DEFAULT_MIN_INDOOR_TEMP_DELTA, "min indoor delta"),
    (CONF_PRIOR_STRENGTH, DEFAULT_PRIOR_STRENGTH, "prior strength"),
    (CONF_RATIO_BOUNDS_LOWER, DEFAULT_RATIO_BOUNDS_LOWER, "ratio lower"),
    (CONF_RATIO_BOUNDS_UPPER, DEFAULT_RATIO_BOUNDS_UPPER, "ratio upper"),
    (CONF_SOLAR_FACTOR_BOUNDS_UPPER, DEFAULT_SOLAR_FACTOR_BOUNDS_UPPER, "solar upper"),
    (
        CONF_INTERNAL_GAIN_MAX_W_PER_M2,
        DEFAULT_INTERNAL_GAIN_MAX_W_PER_M2,
        "internal gain max",
    ),
    (CONF_MIN_COP_SAMPLES, DEFAULT_MIN_COP_SAMPLES, "min COP samples"),
    (CONF_COP_SCALE_BOUNDS_LOWER, DEFAULT_COP_SCALE_BOUNDS_LOWER, "COP scale lower"),
    (CONF_COP_SCALE_BOUNDS_UPPER, DEFAULT_COP_SCALE_BOUNDS_UPPER, "COP scale upper"),
    (CONF_MIN_EMITTER_SAMPLES, DEFAULT_MIN_EMITTER_SAMPLES, "min emitter samples"),
    (
        CONF_EMITTER_EXPONENT_BOUNDS_LOWER,
        DEFAULT_EMITTER_EXPONENT_BOUNDS_LOWER,
        "emitter exp lower",
    ),
    (
        CONF_EMITTER_EXPONENT_BOUNDS_UPPER,
        DEFAULT_EMITTER_EXPONENT_BOUNDS_UPPER,
        "emitter exp upper",
    ),
    (CONF_CALIBRATION_MIN_HOURS, DEFAULT_CALIBRATION_MIN_HOURS, "cal min hours"),
    (CONF_CALIBRATION_MAX_HOURS, DEFAULT_CALIBRATION_MAX_HOURS, "cal max hours"),
    (CONF_CALIBRATION_MAX_JUMP_C, DEFAULT_CALIBRATION_MAX_JUMP_C, "cal max jump"),
    (CONF_GAS_MIN_WINDOW_HOURS, DEFAULT_GAS_MIN_WINDOW_HOURS, "gas min window"),
    (CONF_MIN_RESIDUAL_SAMPLES, DEFAULT_MIN_RESIDUAL_SAMPLES, "min residual samples"),
    (
        CONF_TWO_MASS_AUTOCORRELATION,
        DEFAULT_TWO_MASS_AUTOCORRELATION,
        "two-mass autocorr",
    ),
    (CONF_GROUND_ALBEDO, DEFAULT_GROUND_ALBEDO, "ground albedo"),
    (
        CONF_DEFROST_FREE_THRESHOLD,
        DEFAULT_DEFROST_FREE_THRESHOLD,
        "defrost free threshold",
    ),
    (
        CONF_DEFROST_COLD_THRESHOLD,
        DEFAULT_DEFROST_COLD_THRESHOLD,
        "defrost cold threshold",
    ),
    (CONF_DEFROST_BASE_PENALTY, DEFAULT_DEFROST_BASE_PENALTY, "defrost penalty"),
    (
        CONF_DEFROST_MIN_COP_MULTIPLIER,
        DEFAULT_DEFROST_MIN_COP_MULTIPLIER,
        "defrost min COP mult",
    ),
    (CONF_MIN_COP, DEFAULT_MIN_COP, "min COP"),
    (
        CONF_COMFORT_LOOKAHEAD_HOURS,
        DEFAULT_COMFORT_LOOKAHEAD_HOURS,
        "gas comfort lookahead",
    ),
    (CONF_COMFORT_TOLERANCE_C, DEFAULT_COMFORT_TOLERANCE_C, "gas comfort tolerance"),
]


def _expert_settings_summary(entry: ConfigEntry) -> dict[str, Any]:
    """Summarise expert settings: which deviate from defaults, plus tips."""
    config = {**entry.data, **entry.options}
    for sub in getattr(entry, "subentries", {}).values():
        config.update(sub.data)

    non_default: dict[str, Any] = {}
    for conf_key, default, label in _EXPERT_DEFAULTS:
        value = config.get(conf_key)
        if value is not None and value != default:
            non_default[conf_key] = {"value": value, "default": default, "label": label}

    shgc = config.get(CONF_WINDOW_SHGC)

    tips: list[str] = []
    comfort_weight = float(
        config.get(CONF_COMFORT_PENALTY_WEIGHT, DEFAULT_COMFORT_PENALTY_WEIGHT)
    )
    if comfort_weight < 20:
        tips.append(
            "comfort_penalty_weight is very low — the optimizer may allow "
            "large temperature swings to save energy."
        )
    if comfort_weight > 200:
        tips.append(
            "comfort_penalty_weight is very high — the optimizer will "
            "prioritize comfort over energy savings."
        )

    offset_min = int(config.get(CONF_OFFSET_MIN, DEFAULT_OFFSET_MIN))
    offset_max = int(config.get(CONF_OFFSET_MAX, DEFAULT_OFFSET_MAX))
    if offset_max - offset_min > 10:
        tips.append(
            f"Offset range is wide ({offset_min} to {offset_max}). "
            "Verify your heat pump can follow large curve shifts."
        )

    headroom = float(config.get(CONF_HEATPUMP_HEADROOM, DEFAULT_HEATPUMP_HEADROOM))
    if headroom < 1.0:
        tips.append(
            "heatpump_headroom < 1.0 means the modelled HP capacity is less "
            "than the emitter design output — the optimizer will be very "
            "conservative."
        )

    prior = float(config.get(CONF_PRIOR_STRENGTH, DEFAULT_PRIOR_STRENGTH))
    if prior < 0.5:
        tips.append(
            "prior_strength is very low — calibration will trust noisy data "
            "over the energy-label prior. Consider increasing if fits are "
            "implausible."
        )

    r_sq = float(config.get(CONF_MIN_R_SQUARED, DEFAULT_MIN_R_SQUARED))
    if r_sq < 0.2:
        tips.append("min_r_squared is very low — calibration may apply noisy fits.")

    albedo = float(config.get(CONF_GROUND_ALBEDO, DEFAULT_GROUND_ALBEDO))
    if albedo > 0.4:
        tips.append(
            f"Ground albedo is {albedo} — appropriate for snow cover but "
            "not for year-round use in temperate climates."
        )

    return {
        "non_default_count": len(non_default),
        "non_default": non_default,
        "window_shgc_override": shgc,
        "tips": tips,
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
        "expert_settings": _expert_settings_summary(entry),
        "stored_data": stored_data,
        "calibration": calibration_data,
        "sensors": sensors,
    }

    return diagnostics
