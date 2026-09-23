"""Tests for the pure data-flattening helpers behind config_flow.py's new
single-page sectioned form (Part B of the config-flow modernization).

`homeassistant.data_entry_flow.section()` itself does not exist in this
repo's pinned test HA (2024.3.3 - confirmed by direct import attempt), so
the schema-building function that actually uses `section()`
(`_build_sectioned_schema`) and the `_section is not None` branch of
`async_step_user` are **not exercisable by this suite**, the same
documented, accepted limitation `test_zone_subentry.py`/
`test_gas_boiler_subentry.py` already carry for `ConfigSubentryFlow`-gated
code. What *is* tested here are the pure data-flattening functions, which
have no dependency on `section`/HA version at all.

Zone-specific fields (area, envelope, ventilation, thermal mass, emitter
type, target temperature, hysteresis, indoor sensor) are collected via
HeatingZoneSubentryFlow instead - see test_zone_subentry.py - and are no
longer part of this main flow at all, not even the first zone's.
"""

from custom_components.heating_curve_optimizer.companion_integrations import (
    FIELD_SOURCES_CONSUMPTION,
    FIELD_SOURCES_PRODUCTION,
)
from custom_components.heating_curve_optimizer.config_flow import (
    HeatingCurveOptimizerConfigFlow,
    _build_configs_from_sources,
    _extract_sectioned_data,
)
from custom_components.heating_curve_optimizer.const import (
    CONF_BASE_COP,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_HEATING_CURVE_OFFSET,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_K_FACTOR,
    CONF_OFFSET_DELTA_T,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_PLANNING_WINDOW,
    CONF_POWER_CONSUMPTION,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_TIME_BASE,
    DEFAULT_COP_AT_35,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_HEATING_CURVE_OFFSET,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_K_FACTOR,
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_TIME_BASE,
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
)

MINIMAL_SECTIONED_INPUT = {
    "building": {
        CONF_CONSUMPTION_PRICE_SENSOR: "sensor.consumption_price",
        CONF_PRODUCTION_PRICE_SENSOR: "sensor.production_price",
    },
    "sensors": {},
    "heat_pump_and_curve": {},
    "advanced": {},
}


def test_extract_sectioned_data_minimal_input_uses_defaults():
    flat = _extract_sectioned_data(MINIMAL_SECTIONED_INPUT)

    assert flat[CONF_CONSUMPTION_PRICE_SENSOR] == "sensor.consumption_price"
    assert flat[CONF_PRODUCTION_PRICE_SENSOR] == "sensor.production_price"
    assert flat[CONF_POWER_CONSUMPTION] is None
    assert flat[CONF_SUPPLY_TEMPERATURE_SENSOR] is None
    assert flat[CONF_GRID_IMPORT_SENSOR] is None
    assert flat[CONF_GRID_EXPORT_SENSOR] is None
    assert flat[CONF_K_FACTOR] == DEFAULT_K_FACTOR
    assert flat[CONF_BASE_COP] == DEFAULT_COP_AT_35
    assert flat[CONF_OUTDOOR_TEMP_COEFFICIENT] == DEFAULT_OUTDOOR_TEMP_COEFFICIENT
    assert flat[CONF_COP_COMPENSATION_FACTOR] == DEFAULT_COP_COMPENSATION_FACTOR
    assert flat[CONF_HEAT_CURVE_MIN_OUTDOOR] == -20.0
    assert flat[CONF_HEAT_CURVE_MAX_OUTDOOR] == 15.0
    assert flat[CONF_HEATING_CURVE_OFFSET] == DEFAULT_HEATING_CURVE_OFFSET
    assert flat[CONF_HEAT_CURVE_MIN] == DEFAULT_HEAT_CURVE_MIN
    assert flat[CONF_HEAT_CURVE_MAX] == DEFAULT_HEAT_CURVE_MAX
    assert flat[CONF_OFFSET_DELTA_T] == DEFAULT_OFFSET_DELTA_T
    assert flat[CONF_PLANNING_WINDOW] == DEFAULT_PLANNING_WINDOW
    assert flat[CONF_TIME_BASE] == DEFAULT_TIME_BASE
    assert "area_m2" not in flat
    assert "energy_label" not in flat
    assert "target_indoor_temp" not in flat
    assert "indoor_temperature_sensor" not in flat


def test_extract_sectioned_data_full_input_round_trips_exactly():
    full_input = {
        "building": {
            CONF_CONSUMPTION_PRICE_SENSOR: "sensor.cp",
            CONF_PRODUCTION_PRICE_SENSOR: "sensor.pp",
        },
        "sensors": {
            CONF_POWER_CONSUMPTION: "sensor.power",
            CONF_SUPPLY_TEMPERATURE_SENSOR: "sensor.supply",
            CONF_GRID_IMPORT_SENSOR: "sensor.grid_in",
            CONF_GRID_EXPORT_SENSOR: "sensor.grid_out",
        },
        "heat_pump_and_curve": {
            CONF_K_FACTOR: 0.03,
            CONF_BASE_COP: 4.5,
            CONF_OUTDOOR_TEMP_COEFFICIENT: 0.02,
            CONF_COP_COMPENSATION_FACTOR: 0.95,
            CONF_HEAT_CURVE_MIN_OUTDOOR: -15.0,
            CONF_HEAT_CURVE_MAX_OUTDOOR: 18.0,
            CONF_HEATING_CURVE_OFFSET: 1.0,
            CONF_HEAT_CURVE_MIN: 25.0,
            CONF_HEAT_CURVE_MAX: 55.0,
            CONF_OFFSET_DELTA_T: 20,
        },
        "advanced": {
            CONF_PLANNING_WINDOW: 12,
            CONF_TIME_BASE: 30,
        },
    }

    flat = _extract_sectioned_data(full_input)

    assert flat[CONF_CONSUMPTION_PRICE_SENSOR] == "sensor.cp"
    assert flat[CONF_POWER_CONSUMPTION] == "sensor.power"
    assert flat[CONF_K_FACTOR] == 0.03
    assert flat[CONF_BASE_COP] == 4.5
    assert flat[CONF_HEAT_CURVE_MIN] == 25.0
    assert flat[CONF_OFFSET_DELTA_T] == 20
    assert isinstance(flat[CONF_OFFSET_DELTA_T], int)
    assert flat[CONF_PLANNING_WINDOW] == 12
    assert isinstance(flat[CONF_PLANNING_WINDOW], int)


def test_build_configs_from_sources_both_populated():
    configs = _build_configs_from_sources(
        ["sensor.consumption_a", "sensor.consumption_b"], ["sensor.production_a"]
    )

    assert configs == [
        {
            CONF_SOURCE_TYPE: SOURCE_TYPE_CONSUMPTION,
            CONF_SOURCES: ["sensor.consumption_a", "sensor.consumption_b"],
        },
        {
            CONF_SOURCE_TYPE: SOURCE_TYPE_PRODUCTION,
            CONF_SOURCES: ["sensor.production_a"],
        },
    ]


def test_build_configs_from_sources_one_empty_omits_its_block():
    configs = _build_configs_from_sources(["sensor.consumption_a"], [])

    assert configs == [
        {
            CONF_SOURCE_TYPE: SOURCE_TYPE_CONSUMPTION,
            CONF_SOURCES: ["sensor.consumption_a"],
        }
    ]


def test_build_configs_from_sources_both_empty_returns_empty_list():
    assert _build_configs_from_sources([], []) == []


def test_sectioned_defaults_includes_source_lists_from_configs():
    """`_sectioned_defaults` (fed into `_build_sectioned_schema`) reuses
    `_build_entry_data`'s own dict, plus derives the two flattened source
    fields from `self.configs` - this is pure attribute-reading logic with
    no `section` dependency, unlike `_build_sectioned_schema` itself."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.consumption_price_sensor = "sensor.cp"
    flow.production_price_sensor = "sensor.pp"
    flow.configs = [
        {CONF_SOURCE_TYPE: SOURCE_TYPE_CONSUMPTION, CONF_SOURCES: ["sensor.c1"]},
        {CONF_SOURCE_TYPE: SOURCE_TYPE_PRODUCTION, CONF_SOURCES: ["sensor.p1"]},
    ]

    defaults = flow._sectioned_defaults()

    assert defaults[CONF_CONSUMPTION_PRICE_SENSOR] == "sensor.cp"
    assert defaults[FIELD_SOURCES_CONSUMPTION] == ["sensor.c1"]
    assert defaults[FIELD_SOURCES_PRODUCTION] == ["sensor.p1"]
    assert "area_m2" not in defaults
    assert "energy_label" not in defaults


def test_sectioned_defaults_empty_configs_gives_empty_source_lists():
    flow = HeatingCurveOptimizerConfigFlow()

    defaults = flow._sectioned_defaults()

    assert defaults[FIELD_SOURCES_CONSUMPTION] == []
    assert defaults[FIELD_SOURCES_PRODUCTION] == []
