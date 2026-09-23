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
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
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
_BC_CONF_GROSS_LOAD_SENSORS = "gross_load_sensors"
_BC_CONF_PV_PRODUCTION_SENSORS = "pv_production_sensors"
_BC_CONF_PV_MEASURED_PRODUCTION_SENSOR = "pv_measured_production_sensor"
_BC_CONF_PRICE_SENSOR = "price_sensor"
_BC_CONF_FEED_IN_PRICE_SENSOR = "feed_in_price_sensor"

# battery_controller stores each PV array as its own subentry (added via
# the integration page after initial setup, like this integration's own
# zone/gas-boiler subentries) rather than as a field on the main entry -
# its own measured-production sensor lives only there, never in
# entry.data/entry.options, so it needs its own subentry read (see
# _bc_pv_subentry_production_sensors below), the same way DECC's source
# subentries already do below.
_BC_PV_SUBENTRY_TYPE = "pv_array"

_BATTERY_CONTROLLER_SOURCE = "Battery Controller"
_DECC_SOURCE = "Dynamic Energy Contract Calculator"

# Field names on this integration's own ConfigFlow instance.
FIELD_POWER_CONSUMPTION = "power_consumption"
FIELD_GRID_IMPORT_SENSOR = "grid_import_sensor"
FIELD_GRID_EXPORT_SENSOR = "grid_export_sensor"
FIELD_CONSUMPTION_PRICE_SENSOR = "consumption_price_sensor"
FIELD_PRODUCTION_PRICE_SENSOR = "production_price_sensor"
FIELD_SOURCES_CONSUMPTION = "sources_consumption"
FIELD_SOURCES_PRODUCTION = "sources_production"


@dataclass
class DetectedSensor:
    """A sensor entity_id found in a companion integration's own config."""

    entity_id: str
    source: str


@dataclass
class DetectedSensorList:
    """A list of sensor entity_ids found in a companion integration's own
    config - for the multi-select consumption/production energy-sensor
    fields (CONF_SOURCES), as opposed to DetectedSensor's single entity_id
    for the power/price fields."""

    entity_ids: list[str]
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


def _decc_source_entities(hass: HomeAssistant, source_type: str) -> list[str] | None:
    """Entity_ids for a given source_type from DECC's own source
    subentries - each stores `{CONF_SOURCE_TYPE: ..., CONF_SOURCES:
    [entity_ids]}`, the identical key/value shape this integration's own
    `self.configs` list already uses (confirmed against
    dynamic_energy_contract_calculator's const.py: same key strings
    ("source_type"/"sources") and the same
    "Electricity consumption"/"Electricity production" values).

    Matched by the presence of both keys in a subentry's data, not a
    hardcoded subentry_type string - a rename on DECC's side can't
    silently break this. Returns None (not an empty list) when DECC isn't
    configured or has no matching, non-empty source - distinguishes "not
    configured" from a hypothetical "configured with zero sensors".
    """
    entries = hass.config_entries.async_entries(DECC_DOMAIN)
    if not entries:
        return None
    for subentry in getattr(entries[0], "subentries", {}).values():
        data = subentry.data
        if data.get(CONF_SOURCE_TYPE) != source_type:
            continue
        sources = data.get(CONF_SOURCES)
        if isinstance(sources, list) and sources:
            return [str(s) for s in sources]
    return None


def _dedupe(entity_ids: list[str]) -> list[str]:
    """Order-preserving de-duplication - the same sensor can legitimately
    turn up in two of battery_controller's own fields at once (e.g. a PV
    array's measured-production sensor also summed into its main entry's
    flat `pv_production_sensors` list)."""
    seen: set[str] = set()
    result: list[str] = []
    for entity_id in entity_ids:
        if entity_id not in seen:
            seen.add(entity_id)
            result.append(entity_id)
    return result


def _bc_pv_subentry_production_sensors(hass: HomeAssistant) -> list[str]:
    """Each battery_controller PV-array subentry's own
    `pv_measured_production_sensor` - not reachable via
    `_first_configured_entry` (which only merges `entry.data`/
    `entry.options`), since subentries are a separate structure. Matched
    by `subentry_type == "pv_array"`, the same discriminator
    `BatteryControllerPVSubentryFlow` itself registers under."""
    entries = hass.config_entries.async_entries(BATTERY_CONTROLLER_DOMAIN)
    if not entries:
        return []
    sensors: list[str] = []
    for subentry in getattr(entries[0], "subentries", {}).values():
        if getattr(subentry, "subentry_type", None) != _BC_PV_SUBENTRY_TYPE:
            continue
        sensor = subentry.data.get(_BC_CONF_PV_MEASURED_PRODUCTION_SENSOR)
        if sensor:
            sensors.append(str(sensor))
    return sensors


def detect_source_sensors(hass: HomeAssistant) -> dict[str, DetectedSensorList]:
    """Candidate consumption/production energy-sensor lists for the main
    setup wizard's CONF_SOURCES fields.

    DECC's own source subentries are preferred when present: they're
    curated for the exact same "track consumption/production for cost"
    purpose this integration's own sources are for. battery_controller is
    the fallback, checked independently per field - DECC might only have
    one of the two source types configured. Consumption candidates are the
    union of its `grid_import_sensors` and `gross_load_sensors` lists (both
    represent metered consumption, just from a different vantage point -
    see its own migration comment for why there are two). Production
    candidates are the union of its `grid_export_sensors` and
    `pv_production_sensors` main-entry lists, plus every PV-array
    subentry's own measured-production sensor - the same full "same flow
    as battery_controller" picture its own config flow shows, not just its
    main entry's flat fields.
    """
    detected: dict[str, DetectedSensorList] = {}

    decc_consumption = _decc_source_entities(hass, SOURCE_TYPE_CONSUMPTION)
    if decc_consumption:
        detected[FIELD_SOURCES_CONSUMPTION] = DetectedSensorList(
            decc_consumption, _DECC_SOURCE
        )

    decc_production = _decc_source_entities(hass, SOURCE_TYPE_PRODUCTION)
    if decc_production:
        detected[FIELD_SOURCES_PRODUCTION] = DetectedSensorList(
            decc_production, _DECC_SOURCE
        )

    if FIELD_SOURCES_CONSUMPTION in detected and FIELD_SOURCES_PRODUCTION in detected:
        return detected

    bc_config = _first_configured_entry(hass, BATTERY_CONTROLLER_DOMAIN)
    if bc_config is not None:
        if FIELD_SOURCES_CONSUMPTION not in detected:
            consumption: list[str] = []
            for key in (_BC_CONF_GRID_IMPORT_SENSORS, _BC_CONF_GROSS_LOAD_SENSORS):
                value = bc_config.get(key)
                if isinstance(value, list):
                    consumption.extend(str(s) for s in value)
            consumption = _dedupe(consumption)
            if consumption:
                detected[FIELD_SOURCES_CONSUMPTION] = DetectedSensorList(
                    consumption, _BATTERY_CONTROLLER_SOURCE
                )

        if FIELD_SOURCES_PRODUCTION not in detected:
            production: list[str] = []
            for key in (_BC_CONF_GRID_EXPORT_SENSORS, _BC_CONF_PV_PRODUCTION_SENSORS):
                value = bc_config.get(key)
                if isinstance(value, list):
                    production.extend(str(s) for s in value)
            production.extend(_bc_pv_subentry_production_sensors(hass))
            production = _dedupe(production)
            if production:
                detected[FIELD_SOURCES_PRODUCTION] = DetectedSensorList(
                    production, _BATTERY_CONTROLLER_SOURCE
                )

    return detected
