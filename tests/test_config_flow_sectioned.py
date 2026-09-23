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
"""

from custom_components.heating_curve_optimizer.config_flow import (
    _build_configs_from_sources,
    _extract_sectioned_data,
)
from custom_components.heating_curve_optimizer.const import (
    CONF_AREA_M2,
    CONF_BASE_COP,
    CONF_CEILING_HEIGHT,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_EMITTER_TYPE,
    CONF_ENERGY_LABEL,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_HEATING_CURVE_OFFSET,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_INDOOR_TEMP_HYSTERESIS,
    CONF_K_FACTOR,
    CONF_MAX_BUFFER_DEBT,
    CONF_OFFSET_DELTA_T,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_PLANNING_WINDOW,
    CONF_POWER_CONSUMPTION,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_PV_EAST_WP,
    CONF_PV_SOUTH_WP,
    CONF_PV_TILT,
    CONF_PV_WEST_WP,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_TARGET_INDOOR_TEMP,
    CONF_THERMAL_MASS_CLASS,
    CONF_TIME_BASE,
    CONF_VENTILATION_TYPE,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_COP_AT_35,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_EMITTER_TYPE,
    DEFAULT_HEATING_CURVE_OFFSET,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_INDOOR_TEMP_HYSTERESIS,
    DEFAULT_K_FACTOR,
    DEFAULT_MAX_BUFFER_DEBT,
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_PV_TILT,
    DEFAULT_TARGET_INDOOR_TEMP,
    DEFAULT_THERMAL_MASS_CLASS,
    DEFAULT_TIME_BASE,
    DEFAULT_VENTILATION_TYPE,
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
)

MINIMAL_SECTIONED_INPUT = {
    "building": {
        CONF_AREA_M2: 150,
        CONF_ENERGY_LABEL: "C",
        CONF_CONSUMPTION_PRICE_SENSOR: "sensor.consumption_price",
        CONF_PRODUCTION_PRICE_SENSOR: "sensor.production_price",
    },
    "envelope": {},
    "sensors": {},
    "heat_pump_and_curve": {},
    "advanced": {},
}


def test_extract_sectioned_data_minimal_input_uses_defaults():
    flat = _extract_sectioned_data(MINIMAL_SECTIONED_INPUT)

    assert flat[CONF_AREA_M2] == 150.0
    assert flat[CONF_ENERGY_LABEL] == "C"
    assert flat[CONF_CONSUMPTION_PRICE_SENSOR] == "sensor.consumption_price"
    assert flat[CONF_PRODUCTION_PRICE_SENSOR] == "sensor.production_price"
    assert flat[CONF_GLASS_EAST_M2] == 0.0
    assert flat[CONF_GLASS_WEST_M2] == 0.0
    assert flat[CONF_GLASS_SOUTH_M2] == 0.0
    assert flat[CONF_GLASS_U_VALUE] == 1.2
    assert flat[CONF_VENTILATION_TYPE] == DEFAULT_VENTILATION_TYPE
    assert flat[CONF_CEILING_HEIGHT] == DEFAULT_CEILING_HEIGHT
    assert flat[CONF_THERMAL_MASS_CLASS] == DEFAULT_THERMAL_MASS_CLASS
    assert flat[CONF_EMITTER_TYPE] == DEFAULT_EMITTER_TYPE
    assert flat[CONF_PV_EAST_WP] == 0.0
    assert flat[CONF_PV_SOUTH_WP] == 0.0
    assert flat[CONF_PV_WEST_WP] == 0.0
    assert flat[CONF_PV_TILT] == DEFAULT_PV_TILT
    assert flat[CONF_INDOOR_TEMPERATURE_SENSOR] is None
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
    assert flat[CONF_MAX_BUFFER_DEBT] == DEFAULT_MAX_BUFFER_DEBT
    assert flat[CONF_TARGET_INDOOR_TEMP] == DEFAULT_TARGET_INDOOR_TEMP
    assert flat[CONF_INDOOR_TEMP_HYSTERESIS] == DEFAULT_INDOOR_TEMP_HYSTERESIS


def test_extract_sectioned_data_full_input_round_trips_exactly():
    full_input = {
        "building": {
            CONF_AREA_M2: 200,
            CONF_ENERGY_LABEL: "A+",
            CONF_CONSUMPTION_PRICE_SENSOR: "sensor.cp",
            CONF_PRODUCTION_PRICE_SENSOR: "sensor.pp",
        },
        "envelope": {
            CONF_GLASS_EAST_M2: 5,
            CONF_GLASS_WEST_M2: 6,
            CONF_GLASS_SOUTH_M2: 12,
            CONF_GLASS_U_VALUE: 0.8,
            CONF_VENTILATION_TYPE: "balanced_heat_recovery",
            CONF_CEILING_HEIGHT: 3.0,
            CONF_THERMAL_MASS_CLASS: "heavy",
            CONF_EMITTER_TYPE: "underfloor",
            CONF_PV_EAST_WP: 1000,
            CONF_PV_SOUTH_WP: 3000,
            CONF_PV_WEST_WP: 1000,
            CONF_PV_TILT: 40,
        },
        "sensors": {
            CONF_INDOOR_TEMPERATURE_SENSOR: "sensor.indoor",
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
            CONF_MAX_BUFFER_DEBT: 8.0,
            CONF_TARGET_INDOOR_TEMP: 21.0,
            CONF_INDOOR_TEMP_HYSTERESIS: 0.4,
        },
    }

    flat = _extract_sectioned_data(full_input)

    assert flat[CONF_AREA_M2] == 200.0
    assert flat[CONF_ENERGY_LABEL] == "A+"
    assert flat[CONF_GLASS_SOUTH_M2] == 12.0
    assert flat[CONF_VENTILATION_TYPE] == "balanced_heat_recovery"
    assert flat[CONF_THERMAL_MASS_CLASS] == "heavy"
    assert flat[CONF_EMITTER_TYPE] == "underfloor"
    assert flat[CONF_INDOOR_TEMPERATURE_SENSOR] == "sensor.indoor"
    assert flat[CONF_POWER_CONSUMPTION] == "sensor.power"
    assert flat[CONF_K_FACTOR] == 0.03
    assert flat[CONF_BASE_COP] == 4.5
    assert flat[CONF_HEAT_CURVE_MIN] == 25.0
    assert flat[CONF_OFFSET_DELTA_T] == 20
    assert isinstance(flat[CONF_OFFSET_DELTA_T], int)
    assert flat[CONF_PLANNING_WINDOW] == 12
    assert isinstance(flat[CONF_PLANNING_WINDOW], int)
    assert flat[CONF_TARGET_INDOOR_TEMP] == 21.0


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
