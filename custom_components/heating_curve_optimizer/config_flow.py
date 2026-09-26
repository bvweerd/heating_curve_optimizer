"""Config flow for the Heating Curve Optimizer integration.

The main entry holds what is shared by the whole installation: price
sensors, heat pump parameters, the heating curve and optional real-time
sensors. Heating zones, PV arrays and the optional gas boiler are config
subentries, added from the integration page after setup.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.translation import async_get_translations

from .calibration import CALIBRATION_MODES
from .companion_integrations import (
    DetectedPvArray,
    DetectedSensor,
    detect_gas_price_sensor,
    detect_main_flow_sensors,
    detect_pv_arrays,
)
from .const import (
    CONF_ACCURACY_HORIZON_HOURS,
    CONF_AREA_M2,
    CONF_BASE_COP,
    CONF_CALIBRATION_MAX_HOURS,
    CONF_CALIBRATION_MAX_JUMP_C,
    CONF_CALIBRATION_MIN_HOURS,
    CONF_CALIBRATION_MODE,
    CONF_CALIBRATION_WINDOW,
    CONF_CEILING_HEIGHT,
    CONF_COMFORT_LOOKAHEAD_HOURS,
    CONF_COMFORT_PENALTY_WEIGHT,
    CONF_COMFORT_TOLERANCE_C,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_COP_SCALE_BOUNDS_LOWER,
    CONF_COP_SCALE_BOUNDS_UPPER,
    CONF_CYCLING_PENALTY_WEIGHT,
    CONF_DEFROST_BASE_PENALTY,
    CONF_DEFROST_COLD_THRESHOLD,
    CONF_DEFROST_FREE_THRESHOLD,
    CONF_DEFROST_MIN_COP_MULTIPLIER,
    CONF_DHW_ACTIVE_SENSOR,
    CONF_EMITTER_EXPONENT_BOUNDS_LOWER,
    CONF_EMITTER_EXPONENT_BOUNDS_UPPER,
    CONF_EMITTER_TYPE,
    CONF_ENERGY_LABEL,
    CONF_FEED_IN_PRICE_FALLBACK,
    CONF_GAS_BOILER_EFFICIENCY,
    CONF_GAS_CALORIFIC_VALUE,
    CONF_GAS_COMFORT_BACKUP,
    CONF_GAS_METER_SENSOR,
    CONF_GAS_MIN_WINDOW_HOURS,
    CONF_GAS_PRICE_SENSOR,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_GROUND_ALBEDO,
    CONF_HARD_FLOOR_PENALTY,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_HEAT_PUMP_MAX_THERMAL_POWER,
    CONF_HEAT_PUMP_THERMAL_POWER_SENSOR,
    CONF_HEATPUMP_HEADROOM,
    CONF_IDLE_POWER_THRESHOLD_KW,
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_INTERNAL_GAIN_MAX_W_PER_M2,
    CONF_INTERNAL_GAINS_W_PER_M2,
    CONF_K_FACTOR,
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
    CONF_OFFSET_DELTA_T,
    CONF_OFFSET_MAX,
    CONF_OFFSET_MIN,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_PLANNING_WINDOW,
    CONF_POWER_CONSUMPTION,
    CONF_PRICE_CHANGE_MIN_ABS,
    CONF_PRICE_CHANGE_REL,
    CONF_PRIOR_STRENGTH,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_PV_DC_COUPLED,
    CONF_PV_EFFICIENCY_FACTOR,
    CONF_PV_ORIENTATION,
    CONF_PV_PEAK_POWER_KWP,
    CONF_PV_TILT,
    CONF_RATIO_BOUNDS_LOWER,
    CONF_RATIO_BOUNDS_UPPER,
    CONF_SOLAR_FACTOR_BOUNDS_UPPER,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_TARGET_INDOOR_TEMP,
    CONF_THERMAL_MASS_CLASS,
    CONF_TWO_MASS_AUTOCORRELATION,
    CONF_VENTILATION_TYPE,
    CONF_WINDOW_SENSORS,
    CONF_WINDOW_SHGC,
    DEFAULT_ACCURACY_HORIZON_HOURS,
    DEFAULT_CALIBRATION_MAX_HOURS,
    DEFAULT_CALIBRATION_MAX_JUMP_C,
    DEFAULT_CALIBRATION_MIN_HOURS,
    DEFAULT_CALIBRATION_MODE,
    DEFAULT_CALIBRATION_WINDOW,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_COMFORT_LOOKAHEAD_HOURS,
    DEFAULT_COMFORT_PENALTY_WEIGHT,
    DEFAULT_COMFORT_TOLERANCE_C,
    DEFAULT_COP_AT_35,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_COP_SCALE_BOUNDS_LOWER,
    DEFAULT_COP_SCALE_BOUNDS_UPPER,
    DEFAULT_CYCLING_PENALTY_WEIGHT,
    DEFAULT_DEFROST_BASE_PENALTY,
    DEFAULT_DEFROST_COLD_THRESHOLD,
    DEFAULT_DEFROST_FREE_THRESHOLD,
    DEFAULT_DEFROST_MIN_COP_MULTIPLIER,
    DEFAULT_EMITTER_EXPONENT_BOUNDS_LOWER,
    DEFAULT_EMITTER_EXPONENT_BOUNDS_UPPER,
    DEFAULT_EMITTER_TYPE,
    DEFAULT_FEED_IN_PRICE_FALLBACK,
    DEFAULT_GAS_BOILER_EFFICIENCY,
    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
    DEFAULT_GAS_COMFORT_BACKUP,
    DEFAULT_GAS_MIN_WINDOW_HOURS,
    DEFAULT_GLASS_U_VALUE,
    DEFAULT_GROUND_ALBEDO,
    DEFAULT_HARD_FLOOR_PENALTY,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_HEAT_CURVE_MAX_OUTDOOR,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_HEAT_CURVE_MIN_OUTDOOR,
    DEFAULT_HEATPUMP_HEADROOM,
    DEFAULT_IDLE_POWER_THRESHOLD_KW,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
    DEFAULT_INTERNAL_GAIN_MAX_W_PER_M2,
    DEFAULT_INTERNAL_GAINS_W_PER_M2,
    DEFAULT_K_FACTOR,
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
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_OFFSET_MAX,
    DEFAULT_OFFSET_MIN,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_PRICE_CHANGE_MIN_ABS,
    DEFAULT_PRICE_CHANGE_REL,
    DEFAULT_PRIOR_STRENGTH,
    DEFAULT_PV_EFFICIENCY_FACTOR,
    DEFAULT_PV_ORIENTATION_DEG,
    DEFAULT_PV_TILT,
    DEFAULT_RATIO_BOUNDS_LOWER,
    DEFAULT_RATIO_BOUNDS_UPPER,
    DEFAULT_SOLAR_FACTOR_BOUNDS_UPPER,
    DEFAULT_TARGET_INDOOR_TEMP,
    DEFAULT_THERMAL_MASS_CLASS,
    DEFAULT_TWO_MASS_AUTOCORRELATION,
    DEFAULT_VENTILATION_TYPE,
    DOMAIN,
    EMITTER_EXPONENT_MAP,
    ENERGY_LABELS,
    ENTITY_MANAGED_OPTIONS,
    GAS_SUBENTRY_TYPE,
    PV_SUBENTRY_TYPE,
    THERMAL_MASS_WH_PER_M2_K,
    VENTILATION_TYPES,
    ZONE_SUBENTRY_TYPE,
)

# --- selector helpers -------------------------------------------------------


def _number(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> NumberSelector:
    config = NumberSelectorConfig(
        min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _sensor(device_class: str | list[str] | None = None) -> EntitySelector:
    if device_class is None:
        return EntitySelector(EntitySelectorConfig(domain="sensor"))
    return EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class=device_class)
    )


def _select(options: list[str], translation_key: str | None = None) -> SelectSelector:
    config = SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
    if translation_key is not None:
        config["translation_key"] = translation_key
    return SelectSelector(config)


def _suggested(
    defaults: dict[str, Any], key: str, fallback: Any = None
) -> dict[str, Any]:
    value = defaults.get(key, fallback)
    return {"suggested_value": value} if value is not None else {}


async def _test_api_connection(hass: HomeAssistant) -> str | None:
    """Check that open-meteo.com is reachable; return an error key or None."""
    session = async_get_clientsession(hass)
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(
        {
            "latitude": hass.config.latitude,
            "longitude": hass.config.longitude,
            "hourly": "temperature_2m",
            "forecast_days": "1",
        }
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return "cannot_connect"
    except (aiohttp.ClientError, TimeoutError):
        return "cannot_connect"
    return None


# --- main entry ---------------------------------------------------------------

FIELD_USE_DETECTED = "use_detected"

SECTION_PRICES = "prices"
SECTION_SENSORS = "sensors"
SECTION_HEAT_PUMP = "heat_pump"
SECTION_HEATING_CURVE = "heating_curve"
SECTION_ADVANCED = "advanced"
SECTION_EXPERT_OPTIMIZER = "expert_optimizer"
SECTION_EXPERT_CALIBRATION = "expert_calibration"
SECTION_EXPERT_CLIMATE = "expert_climate"

_MAIN_SECTIONS: dict[str, tuple[str, ...]] = {
    SECTION_PRICES: (CONF_CONSUMPTION_PRICE_SENSOR, CONF_PRODUCTION_PRICE_SENSOR),
    SECTION_SENSORS: (
        CONF_POWER_CONSUMPTION,
        CONF_HEAT_PUMP_THERMAL_POWER_SENSOR,
        CONF_SUPPLY_TEMPERATURE_SENSOR,
        CONF_DHW_ACTIVE_SENSOR,
        CONF_GRID_IMPORT_SENSOR,
        CONF_GRID_EXPORT_SENSOR,
    ),
    SECTION_HEAT_PUMP: (
        CONF_BASE_COP,
        CONF_K_FACTOR,
        CONF_OUTDOOR_TEMP_COEFFICIENT,
        CONF_COP_COMPENSATION_FACTOR,
        CONF_HEAT_PUMP_MAX_THERMAL_POWER,
    ),
    SECTION_HEATING_CURVE: (
        CONF_HEAT_CURVE_MIN,
        CONF_HEAT_CURVE_MAX,
        CONF_HEAT_CURVE_MIN_OUTDOOR,
        CONF_HEAT_CURVE_MAX_OUTDOOR,
        CONF_OFFSET_DELTA_T,
    ),
    SECTION_ADVANCED: (CONF_PLANNING_WINDOW,),
    SECTION_EXPERT_OPTIMIZER: (
        CONF_COMFORT_PENALTY_WEIGHT,
        CONF_CYCLING_PENALTY_WEIGHT,
        CONF_HARD_FLOOR_PENALTY,
        CONF_FEED_IN_PRICE_FALLBACK,
        CONF_OFFSET_MIN,
        CONF_OFFSET_MAX,
        CONF_HEATPUMP_HEADROOM,
    ),
    SECTION_EXPERT_CALIBRATION: (
        CONF_CALIBRATION_WINDOW,
        CONF_MIN_SAMPLES_TO_APPLY,
        CONF_MIN_R_SQUARED,
        CONF_MIN_SHARE_EACH_DIRECTION,
        CONF_MIN_INDOOR_TEMP_DELTA,
        CONF_PRIOR_STRENGTH,
        CONF_RATIO_BOUNDS_LOWER,
        CONF_RATIO_BOUNDS_UPPER,
        CONF_SOLAR_FACTOR_BOUNDS_UPPER,
        CONF_INTERNAL_GAIN_MAX_W_PER_M2,
        CONF_MIN_COP_SAMPLES,
        CONF_COP_SCALE_BOUNDS_LOWER,
        CONF_COP_SCALE_BOUNDS_UPPER,
        CONF_MIN_EMITTER_SAMPLES,
        CONF_EMITTER_EXPONENT_BOUNDS_LOWER,
        CONF_EMITTER_EXPONENT_BOUNDS_UPPER,
        CONF_CALIBRATION_MIN_HOURS,
        CONF_CALIBRATION_MAX_HOURS,
        CONF_CALIBRATION_MAX_JUMP_C,
        CONF_GAS_MIN_WINDOW_HOURS,
        CONF_MIN_RESIDUAL_SAMPLES,
        CONF_TWO_MASS_AUTOCORRELATION,
    ),
    SECTION_EXPERT_CLIMATE: (
        CONF_GROUND_ALBEDO,
        CONF_DEFROST_FREE_THRESHOLD,
        CONF_DEFROST_COLD_THRESHOLD,
        CONF_DEFROST_BASE_PENALTY,
        CONF_DEFROST_MIN_COP_MULTIPLIER,
        CONF_MIN_COP,
        CONF_MWH_MAGNITUDE_THRESHOLD,
        CONF_IDLE_POWER_THRESHOLD_KW,
        CONF_PRICE_CHANGE_REL,
        CONF_PRICE_CHANGE_MIN_ABS,
        CONF_MIN_RUNNING_POWER_KW,
        CONF_ACCURACY_HORIZON_HOURS,
    ),
}

MAIN_DEFAULTS: dict[str, Any] = {
    CONF_BASE_COP: DEFAULT_COP_AT_35,
    CONF_K_FACTOR: DEFAULT_K_FACTOR,
    CONF_OUTDOOR_TEMP_COEFFICIENT: DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    CONF_COP_COMPENSATION_FACTOR: DEFAULT_COP_COMPENSATION_FACTOR,
    CONF_HEAT_CURVE_MIN: DEFAULT_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MAX: DEFAULT_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MIN_OUTDOOR: DEFAULT_HEAT_CURVE_MIN_OUTDOOR,
    CONF_HEAT_CURVE_MAX_OUTDOOR: DEFAULT_HEAT_CURVE_MAX_OUTDOOR,
    CONF_OFFSET_DELTA_T: DEFAULT_OFFSET_DELTA_T,
    CONF_PLANNING_WINDOW: DEFAULT_PLANNING_WINDOW,
    # Expert: Optimizer
    CONF_COMFORT_PENALTY_WEIGHT: DEFAULT_COMFORT_PENALTY_WEIGHT,
    CONF_CYCLING_PENALTY_WEIGHT: DEFAULT_CYCLING_PENALTY_WEIGHT,
    CONF_HARD_FLOOR_PENALTY: DEFAULT_HARD_FLOOR_PENALTY,
    CONF_FEED_IN_PRICE_FALLBACK: DEFAULT_FEED_IN_PRICE_FALLBACK,
    CONF_OFFSET_MIN: DEFAULT_OFFSET_MIN,
    CONF_OFFSET_MAX: DEFAULT_OFFSET_MAX,
    CONF_HEATPUMP_HEADROOM: DEFAULT_HEATPUMP_HEADROOM,
    # Expert: Calibration
    CONF_CALIBRATION_WINDOW: DEFAULT_CALIBRATION_WINDOW,
    CONF_MIN_SAMPLES_TO_APPLY: DEFAULT_MIN_SAMPLES_TO_APPLY,
    CONF_MIN_R_SQUARED: DEFAULT_MIN_R_SQUARED,
    CONF_MIN_SHARE_EACH_DIRECTION: DEFAULT_MIN_SHARE_EACH_DIRECTION,
    CONF_MIN_INDOOR_TEMP_DELTA: DEFAULT_MIN_INDOOR_TEMP_DELTA,
    CONF_PRIOR_STRENGTH: DEFAULT_PRIOR_STRENGTH,
    CONF_RATIO_BOUNDS_LOWER: DEFAULT_RATIO_BOUNDS_LOWER,
    CONF_RATIO_BOUNDS_UPPER: DEFAULT_RATIO_BOUNDS_UPPER,
    CONF_SOLAR_FACTOR_BOUNDS_UPPER: DEFAULT_SOLAR_FACTOR_BOUNDS_UPPER,
    CONF_INTERNAL_GAIN_MAX_W_PER_M2: DEFAULT_INTERNAL_GAIN_MAX_W_PER_M2,
    CONF_MIN_COP_SAMPLES: DEFAULT_MIN_COP_SAMPLES,
    CONF_COP_SCALE_BOUNDS_LOWER: DEFAULT_COP_SCALE_BOUNDS_LOWER,
    CONF_COP_SCALE_BOUNDS_UPPER: DEFAULT_COP_SCALE_BOUNDS_UPPER,
    CONF_MIN_EMITTER_SAMPLES: DEFAULT_MIN_EMITTER_SAMPLES,
    CONF_EMITTER_EXPONENT_BOUNDS_LOWER: DEFAULT_EMITTER_EXPONENT_BOUNDS_LOWER,
    CONF_EMITTER_EXPONENT_BOUNDS_UPPER: DEFAULT_EMITTER_EXPONENT_BOUNDS_UPPER,
    CONF_CALIBRATION_MIN_HOURS: DEFAULT_CALIBRATION_MIN_HOURS,
    CONF_CALIBRATION_MAX_HOURS: DEFAULT_CALIBRATION_MAX_HOURS,
    CONF_CALIBRATION_MAX_JUMP_C: DEFAULT_CALIBRATION_MAX_JUMP_C,
    CONF_GAS_MIN_WINDOW_HOURS: DEFAULT_GAS_MIN_WINDOW_HOURS,
    CONF_MIN_RESIDUAL_SAMPLES: DEFAULT_MIN_RESIDUAL_SAMPLES,
    CONF_TWO_MASS_AUTOCORRELATION: DEFAULT_TWO_MASS_AUTOCORRELATION,
    # Expert: Climate
    CONF_GROUND_ALBEDO: DEFAULT_GROUND_ALBEDO,
    CONF_DEFROST_FREE_THRESHOLD: DEFAULT_DEFROST_FREE_THRESHOLD,
    CONF_DEFROST_COLD_THRESHOLD: DEFAULT_DEFROST_COLD_THRESHOLD,
    CONF_DEFROST_BASE_PENALTY: DEFAULT_DEFROST_BASE_PENALTY,
    CONF_DEFROST_MIN_COP_MULTIPLIER: DEFAULT_DEFROST_MIN_COP_MULTIPLIER,
    CONF_MIN_COP: DEFAULT_MIN_COP,
    CONF_MWH_MAGNITUDE_THRESHOLD: DEFAULT_MWH_MAGNITUDE_THRESHOLD,
    CONF_IDLE_POWER_THRESHOLD_KW: DEFAULT_IDLE_POWER_THRESHOLD_KW,
    CONF_PRICE_CHANGE_REL: DEFAULT_PRICE_CHANGE_REL,
    CONF_PRICE_CHANGE_MIN_ABS: DEFAULT_PRICE_CHANGE_MIN_ABS,
    CONF_MIN_RUNNING_POWER_KW: DEFAULT_MIN_RUNNING_POWER_KW,
    CONF_ACCURACY_HORIZON_HOURS: DEFAULT_ACCURACY_HORIZON_HOURS,
}


def build_main_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Single-page sectioned form for the main entry (setup and options)."""

    def opt(key: str) -> vol.Optional:
        return vol.Optional(key, description=_suggested(defaults, key))

    def req(key: str) -> vol.Required:
        return vol.Required(key, description=_suggested(defaults, key))

    prices = vol.Schema(
        {
            req(CONF_CONSUMPTION_PRICE_SENSOR): _sensor(),
            opt(CONF_PRODUCTION_PRICE_SENSOR): _sensor(),
        }
    )
    sensors = vol.Schema(
        {
            opt(CONF_POWER_CONSUMPTION): _sensor("power"),
            opt(CONF_HEAT_PUMP_THERMAL_POWER_SENSOR): _sensor("power"),
            opt(CONF_SUPPLY_TEMPERATURE_SENSOR): _sensor("temperature"),
            opt(CONF_DHW_ACTIVE_SENSOR): EntitySelector(
                EntitySelectorConfig(domain="binary_sensor")
            ),
            opt(CONF_GRID_IMPORT_SENSOR): _sensor("power"),
            opt(CONF_GRID_EXPORT_SENSOR): _sensor("power"),
        }
    )
    heat_pump = vol.Schema(
        {
            req(CONF_BASE_COP): _number(1.0, 8.0, 0.05),
            req(CONF_K_FACTOR): _number(0.0, 0.3, 0.005),
            req(CONF_OUTDOOR_TEMP_COEFFICIENT): _number(0.0, 0.2, 0.005),
            req(CONF_COP_COMPENSATION_FACTOR): _number(0.5, 1.2, 0.01),
            opt(CONF_HEAT_PUMP_MAX_THERMAL_POWER): _number(1.0, 50.0, 0.5, "kW"),
        }
    )
    heating_curve = vol.Schema(
        {
            req(CONF_HEAT_CURVE_MIN): _number(15.0, 50.0, 0.5, "°C"),
            req(CONF_HEAT_CURVE_MAX): _number(20.0, 75.0, 0.5, "°C"),
            req(CONF_HEAT_CURVE_MIN_OUTDOOR): _number(-30.0, 5.0, 0.5, "°C"),
            req(CONF_HEAT_CURVE_MAX_OUTDOOR): _number(5.0, 25.0, 0.5, "°C"),
            req(CONF_OFFSET_DELTA_T): _number(5, 120, 5, "min"),
        }
    )
    advanced = vol.Schema({req(CONF_PLANNING_WINDOW): _number(6, 48, 1, "h")})
    expert_optimizer = vol.Schema(
        {
            req(CONF_COMFORT_PENALTY_WEIGHT): _number(1.0, 500.0, 1.0, "EUR/K²/h"),
            req(CONF_CYCLING_PENALTY_WEIGHT): _number(0.0, 1.0, 0.001),
            req(CONF_HARD_FLOOR_PENALTY): _number(10.0, 10000.0, 10.0, "EUR"),
            req(CONF_FEED_IN_PRICE_FALLBACK): _number(0.0, 0.50, 0.005, "EUR/kWh"),
            req(CONF_OFFSET_MIN): _number(-10, 0, 1, "K"),
            req(CONF_OFFSET_MAX): _number(0, 10, 1, "K"),
            req(CONF_HEATPUMP_HEADROOM): _number(0.5, 3.0, 0.05),
        }
    )
    expert_calibration = vol.Schema(
        {
            req(CONF_CALIBRATION_WINDOW): _number(50, 1000, 10),
            req(CONF_MIN_SAMPLES_TO_APPLY): _number(10, 200, 5),
            req(CONF_MIN_R_SQUARED): _number(0.0, 0.99, 0.01),
            req(CONF_MIN_SHARE_EACH_DIRECTION): _number(0.0, 0.5, 0.01),
            req(CONF_MIN_INDOOR_TEMP_DELTA): _number(0.1, 1.0, 0.05, "K"),
            req(CONF_PRIOR_STRENGTH): _number(0.0, 20.0, 0.5),
            req(CONF_RATIO_BOUNDS_LOWER): _number(0.1, 1.0, 0.05),
            req(CONF_RATIO_BOUNDS_UPPER): _number(1.0, 10.0, 0.5),
            req(CONF_SOLAR_FACTOR_BOUNDS_UPPER): _number(0.5, 5.0, 0.1),
            req(CONF_INTERNAL_GAIN_MAX_W_PER_M2): _number(1.0, 30.0, 0.5, "W/m²"),
            req(CONF_MIN_COP_SAMPLES): _number(5, 100, 5),
            req(CONF_COP_SCALE_BOUNDS_LOWER): _number(0.1, 1.0, 0.05),
            req(CONF_COP_SCALE_BOUNDS_UPPER): _number(1.0, 3.0, 0.05),
            req(CONF_MIN_EMITTER_SAMPLES): _number(5, 100, 5),
            req(CONF_EMITTER_EXPONENT_BOUNDS_LOWER): _number(0.5, 1.5, 0.05),
            req(CONF_EMITTER_EXPONENT_BOUNDS_UPPER): _number(1.0, 2.0, 0.05),
            req(CONF_CALIBRATION_MIN_HOURS): _number(0.1, 2.0, 0.05, "h"),
            req(CONF_CALIBRATION_MAX_HOURS): _number(1.0, 24.0, 0.5, "h"),
            req(CONF_CALIBRATION_MAX_JUMP_C): _number(0.2, 3.0, 0.1, "K"),
            req(CONF_GAS_MIN_WINDOW_HOURS): _number(0.5, 8.0, 0.5, "h"),
            req(CONF_MIN_RESIDUAL_SAMPLES): _number(10, 200, 5),
            req(CONF_TWO_MASS_AUTOCORRELATION): _number(0.1, 0.95, 0.05),
        }
    )
    expert_climate = vol.Schema(
        {
            req(CONF_GROUND_ALBEDO): _number(0.0, 0.9, 0.05),
            req(CONF_DEFROST_FREE_THRESHOLD): _number(-5.0, 15.0, 0.5, "°C"),
            req(CONF_DEFROST_COLD_THRESHOLD): _number(-25.0, 0.0, 0.5, "°C"),
            req(CONF_DEFROST_BASE_PENALTY): _number(0.0, 0.5, 0.01),
            req(CONF_DEFROST_MIN_COP_MULTIPLIER): _number(0.3, 1.0, 0.01),
            req(CONF_MIN_COP): _number(0.1, 2.0, 0.1),
            req(CONF_MWH_MAGNITUDE_THRESHOLD): _number(1.0, 20.0, 0.5),
            req(CONF_IDLE_POWER_THRESHOLD_KW): _number(0.01, 1.0, 0.01, "kW"),
            req(CONF_PRICE_CHANGE_REL): _number(0.01, 0.5, 0.01),
            req(CONF_PRICE_CHANGE_MIN_ABS): _number(0.001, 0.1, 0.001, "EUR/kWh"),
            req(CONF_MIN_RUNNING_POWER_KW): _number(0.05, 2.0, 0.05, "kW"),
            req(CONF_ACCURACY_HORIZON_HOURS): _number(0.25, 6.0, 0.25, "h"),
        }
    )
    return vol.Schema(
        {
            vol.Required(SECTION_PRICES): section(prices, {"collapsed": False}),
            vol.Required(SECTION_SENSORS): section(sensors, {"collapsed": True}),
            vol.Required(SECTION_HEAT_PUMP): section(heat_pump, {"collapsed": False}),
            vol.Required(SECTION_HEATING_CURVE): section(
                heating_curve, {"collapsed": False}
            ),
            vol.Required(SECTION_ADVANCED): section(advanced, {"collapsed": True}),
            vol.Required(SECTION_EXPERT_OPTIMIZER): section(
                expert_optimizer, {"collapsed": True}
            ),
            vol.Required(SECTION_EXPERT_CALIBRATION): section(
                expert_calibration, {"collapsed": True}
            ),
            vol.Required(SECTION_EXPERT_CLIMATE): section(
                expert_climate, {"collapsed": True}
            ),
        }
    )


def flatten_main_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Flatten a sectioned submission into the stored config dict.

    Optional fields left empty are stored as None, so clearing a sensor in
    the options flow really clears it.
    """
    flat: dict[str, Any] = {}
    for section_key, keys in _MAIN_SECTIONS.items():
        values = user_input.get(section_key) or {}
        for key in keys:
            flat[key] = values.get(key)
    for key in (
        CONF_OFFSET_DELTA_T,
        CONF_PLANNING_WINDOW,
        CONF_OFFSET_MIN,
        CONF_OFFSET_MAX,
        CONF_CALIBRATION_WINDOW,
        CONF_MIN_SAMPLES_TO_APPLY,
        CONF_MIN_COP_SAMPLES,
        CONF_MIN_EMITTER_SAMPLES,
        CONF_MIN_RESIDUAL_SAMPLES,
    ):
        if flat.get(key) is not None:
            flat[key] = int(flat[key])
    return flat


def validate_main_config(flat: dict[str, Any]) -> dict[str, str]:
    """Cross-field validation; returns a form errors dict."""
    errors: dict[str, str] = {}
    if not flat.get(CONF_CONSUMPTION_PRICE_SENSOR):
        errors["base"] = "price_sensor_required"
    try:
        if float(flat[CONF_HEAT_CURVE_MIN]) >= float(flat[CONF_HEAT_CURVE_MAX]):
            errors["base"] = "curve_supply_range"
        elif float(flat[CONF_HEAT_CURVE_MIN_OUTDOOR]) >= float(
            flat[CONF_HEAT_CURVE_MAX_OUTDOOR]
        ):
            errors["base"] = "curve_outdoor_range"
    except (KeyError, TypeError, ValueError):
        errors["base"] = "curve_supply_range"
    return errors


class HeatingCurveOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._defaults: dict[str, Any] = dict(MAIN_DEFAULTS)
        self._detected: dict[str, DetectedSensor] = {}

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Heating zones, PV arrays and the gas boiler are subentries."""
        return {
            ZONE_SUBENTRY_TYPE: HeatingZoneSubentryFlow,
            PV_SUBENTRY_TYPE: HeatingPvArraySubentryFlow,
            GAS_SUBENTRY_TYPE: HeatingGasBoilerSubentryFlow,
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return HeatingCurveOptimizerOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main form (after offering companion-integration prefill)."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is None and not self._detected:
            self._detected = detect_main_flow_sensors(self.hass)
            if self._detected:
                return await self.async_step_detected_integrations()

        errors: dict[str, str] = {}
        if user_input is not None:
            flat = flatten_main_input(user_input)
            errors = validate_main_config(flat)
            if not errors and (
                connection_error := await _test_api_connection(self.hass)
            ):
                errors["base"] = connection_error
            if not errors:
                return self.async_create_entry(
                    title="Heating Curve Optimizer", data=flat
                )
            self._defaults.update({k: v for k, v in flat.items() if v is not None})

        return self.async_show_form(
            step_id="user", data_schema=build_main_schema(self._defaults), errors=errors
        )

    async def async_step_detected_integrations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to prefill sensors found in Battery Controller / DECC.

        One multi-select list (all selected by default) instead of a
        checkbox per sensor: each line names the setting, the entity and
        the integration it was found in.
        """
        if user_input is not None:
            for field in user_input.get(FIELD_USE_DETECTED, []):
                if field in self._detected:
                    self._defaults[field] = self._detected[field].entity_id
            return await self.async_step_user()

        translations = await async_get_translations(
            self.hass, self.hass.config.language, "selector", {DOMAIN}
        )
        prefix = f"component.{DOMAIN}.selector.{FIELD_USE_DETECTED}.options."
        options = [
            SelectOptionDict(
                value=field,
                label=f"{translations.get(prefix + field, field)}: "
                f"{detected.entity_id} ({detected.source})",
            )
            for field, detected in self._detected.items()
        ]
        schema = vol.Schema(
            {
                vol.Optional(
                    FIELD_USE_DETECTED, default=list(self._detected)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="detected_integrations", data_schema=schema)


class HeatingCurveOptimizerOptionsFlow(OptionsFlow):
    """Edit the main entry's settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the same form as the initial setup."""
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = flatten_main_input(user_input)
            errors = validate_main_config(flat)
            if not errors:
                # Keep the setpoints the number/climate entities manage.
                managed = {
                    k: v
                    for k, v in self.config_entry.options.items()
                    if k in ENTITY_MANAGED_OPTIONS
                }
                return self.async_create_entry(data={**flat, **managed})
            current.update(flat)

        return self.async_show_form(
            step_id="init",
            data_schema=build_main_schema({**MAIN_DEFAULTS, **current}),
            errors=errors,
        )


# --- heating zone subentry --------------------------------------------------------


def build_zone_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Form for a heating zone (one heating circuit)."""

    def d(key: str, fallback: Any = None) -> dict[str, Any]:
        return _suggested(defaults, key, fallback)

    return vol.Schema(
        {
            vol.Required("name", description=d("name")): TextSelector(),
            vol.Required(CONF_AREA_M2, description=d(CONF_AREA_M2)): _number(
                5, 2000, 1, "m²"
            ),
            vol.Required(
                CONF_ENERGY_LABEL, description=d(CONF_ENERGY_LABEL, "C")
            ): _select(ENERGY_LABELS),
            vol.Required(
                CONF_VENTILATION_TYPE,
                description=d(CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE),
            ): _select(list(VENTILATION_TYPES), "ventilation_type"),
            vol.Required(
                CONF_CEILING_HEIGHT,
                description=d(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT),
            ): _number(2.0, 6.0, 0.1, "m"),
            vol.Required(
                CONF_THERMAL_MASS_CLASS,
                description=d(CONF_THERMAL_MASS_CLASS, DEFAULT_THERMAL_MASS_CLASS),
            ): _select(list(THERMAL_MASS_WH_PER_M2_K), "thermal_mass_class"),
            vol.Required(
                CONF_EMITTER_TYPE,
                description=d(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE),
            ): _select(list(EMITTER_EXPONENT_MAP), "emitter_type"),
            vol.Required(
                CONF_INTERNAL_GAINS_W_PER_M2,
                description=d(
                    CONF_INTERNAL_GAINS_W_PER_M2, DEFAULT_INTERNAL_GAINS_W_PER_M2
                ),
            ): _number(0.0, 15.0, 0.5, "W/m²"),
            vol.Required(
                CONF_GLASS_SOUTH_M2, description=d(CONF_GLASS_SOUTH_M2, 0.0)
            ): _number(0, 200, 0.5, "m²"),
            vol.Required(
                CONF_GLASS_EAST_M2, description=d(CONF_GLASS_EAST_M2, 0.0)
            ): _number(0, 200, 0.5, "m²"),
            vol.Required(
                CONF_GLASS_WEST_M2, description=d(CONF_GLASS_WEST_M2, 0.0)
            ): _number(0, 200, 0.5, "m²"),
            vol.Required(
                CONF_GLASS_U_VALUE,
                description=d(CONF_GLASS_U_VALUE, DEFAULT_GLASS_U_VALUE),
            ): _number(0.4, 6.0, 0.1, "W/m²K"),
            vol.Required(
                CONF_TARGET_INDOOR_TEMP,
                description=d(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP),
            ): _number(15.0, 25.0, 0.5, "°C"),
            vol.Required(
                CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
                description=d(
                    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
                    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
                ),
            ): _number(0.1, 2.0, 0.1, "°C"),
            vol.Required(
                CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
                description=d(
                    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
                    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
                ),
            ): _number(0.1, 2.0, 0.1, "°C"),
            vol.Optional(
                CONF_INDOOR_TEMPERATURE_SENSOR,
                description=d(CONF_INDOOR_TEMPERATURE_SENSOR),
            ): _sensor("temperature"),
            vol.Optional(
                CONF_WINDOW_SENSORS, description=d(CONF_WINDOW_SENSORS)
            ): EntitySelector(
                EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(
                CONF_CALIBRATION_MODE,
                description=d(CONF_CALIBRATION_MODE, DEFAULT_CALIBRATION_MODE),
            ): _select(CALIBRATION_MODES, "calibration_mode"),
            vol.Optional(
                CONF_HEAT_CURVE_MIN, description=d(CONF_HEAT_CURVE_MIN)
            ): _number(15.0, 50.0, 0.5, "°C"),
            vol.Optional(
                CONF_HEAT_CURVE_MAX, description=d(CONF_HEAT_CURVE_MAX)
            ): _number(20.0, 75.0, 0.5, "°C"),
            vol.Optional(CONF_WINDOW_SHGC, description=d(CONF_WINDOW_SHGC)): _number(
                0.1, 0.9, 0.01
            ),
        }
    )


_ZONE_FLOAT_KEYS = (
    CONF_AREA_M2,
    CONF_CEILING_HEIGHT,
    CONF_INTERNAL_GAINS_W_PER_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_WEST_M2,
    CONF_GLASS_U_VALUE,
    CONF_TARGET_INDOOR_TEMP,
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
)


def validate_zone_input(
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Normalize a zone submission; returns (data, error key or None).

    The optional heating curve override is only stored when given, so the
    zone falls back to the main entry's curve otherwise.
    """
    name = str(user_input.get("name", "")).strip()
    if not name:
        return {}, "name_required"
    data: dict[str, Any] = {"name": name}
    for key in _ZONE_FLOAT_KEYS:
        data[key] = float(user_input[key])
    for key in (
        CONF_ENERGY_LABEL,
        CONF_VENTILATION_TYPE,
        CONF_THERMAL_MASS_CLASS,
        CONF_EMITTER_TYPE,
    ):
        data[key] = user_input[key]
    if sensor := user_input.get(CONF_INDOOR_TEMPERATURE_SENSOR):
        data[CONF_INDOOR_TEMPERATURE_SENSOR] = sensor
    if windows := user_input.get(CONF_WINDOW_SENSORS):
        data[CONF_WINDOW_SENSORS] = list(windows)
    data[CONF_CALIBRATION_MODE] = user_input.get(
        CONF_CALIBRATION_MODE, DEFAULT_CALIBRATION_MODE
    )
    curve_min = user_input.get(CONF_HEAT_CURVE_MIN)
    curve_max = user_input.get(CONF_HEAT_CURVE_MAX)
    if (curve_min is None) != (curve_max is None):
        return {}, "zone_curve_incomplete"
    if curve_min is not None and curve_max is not None:
        if float(curve_min) >= float(curve_max):
            return {}, "curve_supply_range"
        data[CONF_HEAT_CURVE_MIN] = float(curve_min)
        data[CONF_HEAT_CURVE_MAX] = float(curve_max)
    if (shgc := user_input.get(CONF_WINDOW_SHGC)) is not None:
        data[CONF_WINDOW_SHGC] = float(shgc)
    return data, None


class HeatingZoneSubentryFlow(ConfigSubentryFlow):
    """Add or edit a heating zone."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a heating zone."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, error = validate_zone_input(user_input)
            if error is None:
                return self.async_create_entry(title=data["name"], data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=build_zone_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a heating zone."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, error = validate_zone_input(user_input)
            if error is None:
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=data["name"], data=data
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_zone_schema(user_input or dict(subentry.data)),
            errors=errors,
        )


# --- PV array subentry -------------------------------------------------------------

_PV_IMPORT_CHOICE_MANUAL = "manual"


def build_pv_array_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Form for one PV array."""
    return vol.Schema(
        {
            vol.Optional(
                "name", description=_suggested(defaults, "name")
            ): TextSelector(),
            vol.Required(
                CONF_PV_PEAK_POWER_KWP,
                description=_suggested(defaults, CONF_PV_PEAK_POWER_KWP, 3.0),
            ): _number(0.1, 100.0, 0.05, "kWp"),
            vol.Required(
                CONF_PV_ORIENTATION,
                description=_suggested(
                    defaults, CONF_PV_ORIENTATION, DEFAULT_PV_ORIENTATION_DEG
                ),
            ): _number(0, 360, 1, "°"),
            vol.Required(
                CONF_PV_TILT,
                description=_suggested(defaults, CONF_PV_TILT, DEFAULT_PV_TILT),
            ): _number(0, 90, 1, "°"),
            vol.Required(
                CONF_PV_EFFICIENCY_FACTOR,
                description=_suggested(
                    defaults, CONF_PV_EFFICIENCY_FACTOR, DEFAULT_PV_EFFICIENCY_FACTOR
                ),
            ): _number(0.1, 1.0, 0.01),
            vol.Required(
                CONF_PV_DC_COUPLED,
                description=_suggested(defaults, CONF_PV_DC_COUPLED, False),
            ): BooleanSelector(),
        }
    )


def normalize_pv_array_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize a PV-array submission."""
    data: dict[str, Any] = {
        CONF_PV_PEAK_POWER_KWP: float(user_input[CONF_PV_PEAK_POWER_KWP]),
        CONF_PV_ORIENTATION: float(user_input[CONF_PV_ORIENTATION]),
        CONF_PV_TILT: float(user_input[CONF_PV_TILT]),
        CONF_PV_EFFICIENCY_FACTOR: float(user_input[CONF_PV_EFFICIENCY_FACTOR]),
        CONF_PV_DC_COUPLED: bool(user_input.get(CONF_PV_DC_COUPLED, False)),
    }
    if name := str(user_input.get("name") or "").strip():
        data["name"] = name
    return data


def pv_array_title(data: dict[str, Any]) -> str:
    """Display title for a PV-array subentry."""
    if name := str(data.get("name", "")).strip():
        return name
    return f"{data[CONF_PV_PEAK_POWER_KWP]:g} kWp"


def _detected_pv_defaults(array: DetectedPvArray) -> dict[str, Any]:
    """Prefill the PV form from a Battery Controller array, name included."""
    defaults: dict[str, Any] = {"name": array.name} if array.name else {}
    return defaults | {
        CONF_PV_PEAK_POWER_KWP: array.peak_power_kwp,
        CONF_PV_ORIENTATION: array.orientation,
        CONF_PV_TILT: array.tilt,
        CONF_PV_EFFICIENCY_FACTOR: array.efficiency_factor,
        CONF_PV_DC_COUPLED: array.dc_coupled,
    }


class HeatingPvArraySubentryFlow(ConfigSubentryFlow):
    """Add or edit a PV array (optionally imported from Battery Controller)."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._defaults: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Offer an import from Battery Controller when it has arrays."""
        detected = detect_pv_arrays(self.hass)
        if not detected:
            return await self.async_step_configure()
        if user_input is not None:
            choice = user_input.get("import_choice", _PV_IMPORT_CHOICE_MANUAL)
            if choice != _PV_IMPORT_CHOICE_MANUAL:
                self._defaults = _detected_pv_defaults(detected[int(choice)])
            return await self.async_step_configure()

        options: list[SelectOptionDict] = [
            SelectOptionDict(value=_PV_IMPORT_CHOICE_MANUAL, label="—")
        ] + [
            SelectOptionDict(
                value=str(i),
                label=f"{array.name or f'Array {i + 1}'} "
                f"({array.peak_power_kwp:g} kWp, {array.source})",
            )
            for i, array in enumerate(detected)
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "import_choice", default=_PV_IMPORT_CHOICE_MANUAL
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_configure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Enter the array's parameters."""
        if user_input is not None:
            data = normalize_pv_array_input(user_input)
            return self.async_create_entry(title=pv_array_title(data), data=data)
        return self.async_show_form(
            step_id="configure", data_schema=build_pv_array_schema(self._defaults)
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a PV array."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data = normalize_pv_array_input(user_input)
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=pv_array_title(data), data=data
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_pv_array_schema(dict(subentry.data)),
        )


# --- gas boiler subentry -----------------------------------------------------------


def build_gas_boiler_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Form for the hybrid gas boiler."""
    return vol.Schema(
        {
            vol.Required(
                CONF_GAS_PRICE_SENSOR,
                description=_suggested(defaults, CONF_GAS_PRICE_SENSOR),
            ): _sensor(),
            vol.Required(
                CONF_GAS_BOILER_EFFICIENCY,
                description=_suggested(
                    defaults, CONF_GAS_BOILER_EFFICIENCY, DEFAULT_GAS_BOILER_EFFICIENCY
                ),
            ): _number(0.5, 1.2, 0.01),
            vol.Required(
                CONF_GAS_CALORIFIC_VALUE,
                description=_suggested(
                    defaults,
                    CONF_GAS_CALORIFIC_VALUE,
                    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
                ),
            ): _number(5.0, 15.0, 0.01, "kWh/m³"),
            vol.Optional(
                CONF_GAS_METER_SENSOR,
                description=_suggested(defaults, CONF_GAS_METER_SENSOR),
            ): _sensor(["gas", "energy"]),
            vol.Required(
                CONF_GAS_COMFORT_BACKUP,
                description=_suggested(
                    defaults, CONF_GAS_COMFORT_BACKUP, DEFAULT_GAS_COMFORT_BACKUP
                ),
            ): BooleanSelector(),
            vol.Required(
                CONF_COMFORT_LOOKAHEAD_HOURS,
                description=_suggested(
                    defaults,
                    CONF_COMFORT_LOOKAHEAD_HOURS,
                    DEFAULT_COMFORT_LOOKAHEAD_HOURS,
                ),
            ): _number(0.5, 12.0, 0.5, "h"),
            vol.Required(
                CONF_COMFORT_TOLERANCE_C,
                description=_suggested(
                    defaults, CONF_COMFORT_TOLERANCE_C, DEFAULT_COMFORT_TOLERANCE_C
                ),
            ): _number(0.0, 1.0, 0.05, "K"),
        }
    )


def normalize_gas_boiler_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize a gas-boiler submission."""
    return {
        CONF_GAS_PRICE_SENSOR: user_input[CONF_GAS_PRICE_SENSOR],
        CONF_GAS_BOILER_EFFICIENCY: float(user_input[CONF_GAS_BOILER_EFFICIENCY]),
        CONF_GAS_CALORIFIC_VALUE: float(user_input[CONF_GAS_CALORIFIC_VALUE]),
        CONF_GAS_COMFORT_BACKUP: bool(
            user_input.get(CONF_GAS_COMFORT_BACKUP, DEFAULT_GAS_COMFORT_BACKUP)
        ),
        CONF_GAS_METER_SENSOR: user_input.get(CONF_GAS_METER_SENSOR),
        CONF_COMFORT_LOOKAHEAD_HOURS: float(
            user_input.get(
                CONF_COMFORT_LOOKAHEAD_HOURS, DEFAULT_COMFORT_LOOKAHEAD_HOURS
            )
        ),
        CONF_COMFORT_TOLERANCE_C: float(
            user_input.get(CONF_COMFORT_TOLERANCE_C, DEFAULT_COMFORT_TOLERANCE_C)
        ),
    }


class HeatingGasBoilerSubentryFlow(ConfigSubentryFlow):
    """Add or edit the (single) hybrid gas boiler."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add the gas boiler."""
        entry = self._get_entry()
        if any(
            sub.subentry_type == GAS_SUBENTRY_TYPE for sub in entry.subentries.values()
        ):
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(
                title="Gas Boiler", data=normalize_gas_boiler_input(user_input)
            )
        defaults: dict[str, Any] = {}
        if (detected := detect_gas_price_sensor(self.hass)) is not None:
            defaults[CONF_GAS_PRICE_SENSOR] = detected.entity_id
        return self.async_show_form(
            step_id="user", data_schema=build_gas_boiler_schema(defaults)
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit the gas boiler."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title="Gas Boiler",
                data=normalize_gas_boiler_input(user_input),
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_gas_boiler_schema(dict(subentry.data)),
        )
