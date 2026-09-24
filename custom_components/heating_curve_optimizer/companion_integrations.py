"""Detect sensor settings already configured in companion integrations.

Battery Controller (`battery_controller`) and Dynamic Energy Contract
Calculator (`dynamic_energy_contract_calculator`, "DECC") are separate,
optional Home Assistant integrations by the same author. When either is
already configured on this Home Assistant instance, several of their
settings are the exact same physical sensors this integration's own setup
wizard asks for - detecting them lets the wizard offer to prefill instead
of making the user look the entity_id up a second time.

Entirely read-only and additive: neither companion integration needs to be
installed for this integration to work, and this module returns an empty
result whenever they aren't configured - see config_flow.py's
`async_step_detected_integrations` for the zero-impact wiring.

Pure Python aside from the `HomeAssistant`/entity-registry lookups - no
state is written here, matching helpers.py's module-boundary convention.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    DEFAULT_PV_EFFICIENCY_FACTOR,
    DEFAULT_PV_ORIENTATION_DEG,
    DEFAULT_PV_TILT,
)

BATTERY_CONTROLLER_DOMAIN = "battery_controller"
DECC_DOMAIN = "dynamic_energy_contract_calculator"

# battery_controller's own const.py keys (duplicated here rather than
# imported - it's a separate, optional integration, not a dependency of
# this one). Consumption/grid keys are lists there; the first entry is
# used as the single-sensor candidate this integration's own fields expect.
#
# electricity_consumption_sensors/electricity_production_sensors are
# deliberately NOT read here: battery_controller's own config_flow.py
# migrates entries off those two fields as of its schema version 6
# (splitting them into grid_import_sensors/grid_export_sensors/
# gross_load_sensors - see its async_migrate_entry), so on any
# battery_controller install that has been through a single HA restart
# since updating, those keys no longer exist in entry.data/entry.options
# at all. Reading them here would silently detect nothing on every
# real-world install.
_BC_CONF_POWER_CONSUMPTION_SENSORS = "power_consumption_sensors"
_BC_CONF_GRID_IMPORT_SENSORS = "grid_import_sensors"
_BC_CONF_GRID_EXPORT_SENSORS = "grid_export_sensors"
_BC_CONF_PRICE_SENSOR = "price_sensor"
_BC_CONF_FEED_IN_PRICE_SENSOR = "feed_in_price_sensor"

# battery_controller stores each PV array as its own subentry.
_BC_PV_SUBENTRY_TYPE = "pv_array"

_BATTERY_CONTROLLER_SOURCE = "Battery Controller"
_DECC_SOURCE = "Dynamic Energy Contract Calculator"

# Field names on this integration's own ConfigFlow instance.
FIELD_POWER_CONSUMPTION = "power_consumption"
FIELD_GRID_IMPORT_SENSOR = "grid_import_sensor"
FIELD_GRID_EXPORT_SENSOR = "grid_export_sensor"
FIELD_CONSUMPTION_PRICE_SENSOR = "consumption_price_sensor"
FIELD_PRODUCTION_PRICE_SENSOR = "production_price_sensor"


@dataclass
class DetectedSensor:
    """A sensor entity_id found in a companion integration's own config."""

    entity_id: str
    source: str


@dataclass
class DetectedPvArray:
    """One battery_controller PV-array subentry's physical parameters -
    for HeatingPvArraySubentryFlow's import step, as opposed to
    DetectedSensor's single entity_id fields. Unlike
    those, this is not an entity_id at all: it carries the array's own
    structural config (peak_power_kwp/orientation/tilt/efficiency_factor/
    dc_coupled), field-for-field identical to this integration's own
    PV-array subentry schema (see const.py's PV_SUBENTRY_TYPE comment and
    _validate_pv_array_subentry in config_flow.py), so it can prefill that
    schema directly without any remapping."""

    name: str
    peak_power_kwp: float
    orientation: float
    tilt: float
    efficiency_factor: float
    dc_coupled: bool
    source: str


def _first_configured_entry(
    hass: HomeAssistant, domain: str
) -> dict[str, object] | None:
    """Merged options-over-data config of the first entry of `domain`, or
    None if no entry exists. Multiple entries of the same companion domain
    are a rare setup this helper doesn't try to disambiguate - the first
    one (registry order) wins, matching this integration's own "options ->
    data -> default" precedence for the merge itself."""
    entries = hass.config_entries.async_entries(domain)
    if not entries:
        return None
    entry = entries[0]
    merged: dict[str, object] = dict(entry.data)
    merged.update(entry.options)
    return merged


def _first_list_item(value: object) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str) and value:
        return value
    return None


def _decc_price_sensor(
    hass: HomeAssistant, unique_id_suffix: str
) -> DetectedSensor | None:
    """Look up one of DECC's fixed-unique_id summary price sensors via the
    entity registry, not by guessing its entity_id (the user may have
    renamed it). Returns None if DECC isn't configured, or is configured
    without the source that sensor depends on (e.g. no gas source set up
    -> no current_gas_consumption_price sensor was ever created)."""
    if not hass.config_entries.async_entries(DECC_DOMAIN):
        return None
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DECC_DOMAIN, f"{DECC_DOMAIN}_{unique_id_suffix}"
    )
    if entity_id is None:
        return None
    return DetectedSensor(entity_id=entity_id, source=_DECC_SOURCE)


def detect_main_flow_sensors(hass: HomeAssistant) -> dict[str, DetectedSensor]:
    """Candidate sensors for the main setup wizard's power/grid/price
    fields. Only keys with an actual candidate are present - an empty dict
    when neither companion integration is configured, which is the common
    case and the reason this feature costs nothing for other installs.

    DECC's price sensors are preferred over battery_controller's raw
    `price_sensor`/`feed_in_price_sensor` when both are available: DECC's
    value already has markup/tax/VAT applied (the actually-delivered
    price), while battery_controller's is the pre-markup wholesale input
    it happens to read - the wrong one to optimize cost against.
    """
    detected: dict[str, DetectedSensor] = {}

    bc_config = _first_configured_entry(hass, BATTERY_CONTROLLER_DOMAIN)
    if bc_config is not None:
        power = _first_list_item(bc_config.get(_BC_CONF_POWER_CONSUMPTION_SENSORS))
        if power:
            detected[FIELD_POWER_CONSUMPTION] = DetectedSensor(
                power, _BATTERY_CONTROLLER_SOURCE
            )

        grid_import = _first_list_item(bc_config.get(_BC_CONF_GRID_IMPORT_SENSORS))
        if grid_import:
            detected[FIELD_GRID_IMPORT_SENSOR] = DetectedSensor(
                grid_import, _BATTERY_CONTROLLER_SOURCE
            )

        grid_export = _first_list_item(bc_config.get(_BC_CONF_GRID_EXPORT_SENSORS))
        if grid_export:
            detected[FIELD_GRID_EXPORT_SENSOR] = DetectedSensor(
                grid_export, _BATTERY_CONTROLLER_SOURCE
            )

        consumption_price = _first_list_item(bc_config.get(_BC_CONF_PRICE_SENSOR))
        if consumption_price:
            detected[FIELD_CONSUMPTION_PRICE_SENSOR] = DetectedSensor(
                consumption_price, _BATTERY_CONTROLLER_SOURCE
            )

        production_price = _first_list_item(
            bc_config.get(_BC_CONF_FEED_IN_PRICE_SENSOR)
        )
        if production_price:
            detected[FIELD_PRODUCTION_PRICE_SENSOR] = DetectedSensor(
                production_price, _BATTERY_CONTROLLER_SOURCE
            )

    decc_consumption = _decc_price_sensor(hass, "current_consumption_price")
    if decc_consumption is not None:
        detected[FIELD_CONSUMPTION_PRICE_SENSOR] = decc_consumption

    decc_production = _decc_price_sensor(hass, "current_production_price")
    if decc_production is not None:
        detected[FIELD_PRODUCTION_PRICE_SENSOR] = decc_production

    return detected


def detect_gas_price_sensor(hass: HomeAssistant) -> DetectedSensor | None:
    """DECC's `current_gas_consumption_price` summary sensor, for the
    hybrid gas-boiler subentry's `gas_price_sensor` field."""
    return _decc_price_sensor(hass, "current_gas_consumption_price")


def detect_pv_arrays(hass: HomeAssistant) -> list[DetectedPvArray]:
    """Every battery_controller PV-array subentry, as import candidates for
    this integration's own HeatingPvArraySubentryFlow.

    Reads the array's own physical parameters - peak_power_kwp/orientation/
    tilt/efficiency_factor/dc_coupled - so a user who already configured
    an array in battery_controller doesn't have to re-enter its numbers a
    second time for this integration's own PV production forecast. Field
    names are a direct match (see DetectedPvArray's docstring), so this is
    a straight read-through, not a remapping. `peak_power_kwp` is required
    by battery_controller's own subentry schema; a subentry missing or
    malformed on that one field is skipped rather than guessed at, since a
    fabricated peak power would silently poison the forecast.
    """
    entries = hass.config_entries.async_entries(BATTERY_CONTROLLER_DOMAIN)
    if not entries:
        return []
    detected: list[DetectedPvArray] = []
    for subentry in getattr(entries[0], "subentries", {}).values():
        if getattr(subentry, "subentry_type", None) != _BC_PV_SUBENTRY_TYPE:
            continue
        data = subentry.data
        try:
            peak_power_kwp = float(data["peak_power_kwp"])
        except (KeyError, TypeError, ValueError):
            continue
        detected.append(
            DetectedPvArray(
                name=str(getattr(subentry, "title", "") or ""),
                peak_power_kwp=peak_power_kwp,
                orientation=float(data.get("orientation", DEFAULT_PV_ORIENTATION_DEG)),
                tilt=float(data.get("tilt", DEFAULT_PV_TILT)),
                efficiency_factor=float(
                    data.get("efficiency_factor", DEFAULT_PV_EFFICIENCY_FACTOR)
                ),
                dc_coupled=bool(data.get("dc_coupled", False)),
                source=_BATTERY_CONTROLLER_SOURCE,
            )
        )
    return detected
