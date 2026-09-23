from __future__ import annotations

import copy
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant import config_entries

try:
    from homeassistant.config_entries import (
        ConfigFlowContext,
        ConfigFlowResult,
        SubentryFlowResult,
    )
except ImportError:
    # For older versions of Home Assistant that don't have these types
    ConfigFlowContext = dict
    ConfigFlowResult = dict[str, Any]
    SubentryFlowResult = dict[str, Any]
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import selector
import voluptuous as vol

from .const import (
    CONF_AREA_M2,
    CONF_CONFIGS,
    CONF_ENERGY_LABEL,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_INDOOR_TEMP_HYSTERESIS,
    CONF_OFFSET_DELTA_T,
    CONF_PLANNING_WINDOW,
    CONF_TARGET_INDOOR_TEMP,
    CONF_TIME_BASE,
    CONF_POWER_CONSUMPTION,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_K_FACTOR,
    CONF_BASE_COP,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEATING_CURVE_OFFSET,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MAX,
    CONF_PRICE_SENSOR,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_PRICE_SETTINGS,
    CONF_PV_EAST_WP,
    CONF_PV_SOUTH_WP,
    CONF_PV_WEST_WP,
    CONF_PV_TILT,
    CONF_VENTILATION_TYPE,
    CONF_CEILING_HEIGHT,
    CONF_MAX_BUFFER_DEBT,
    CONF_THERMAL_MASS_CLASS,
    CONF_EMITTER_TYPE,
    DEFAULT_INDOOR_TEMP_HYSTERESIS,
    DEFAULT_K_FACTOR,
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_PV_TILT,
    DEFAULT_COP_AT_35,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_MAX_BUFFER_DEBT,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_TARGET_INDOOR_TEMP,
    DEFAULT_TIME_BASE,
    DEFAULT_HEATING_CURVE_OFFSET,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_VENTILATION_TYPE,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_THERMAL_MASS_CLASS,
    DEFAULT_EMITTER_TYPE,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    DOMAIN,
    ENERGY_LABELS,
    SOURCE_TYPES,
    VENTILATION_TYPES,
    THERMAL_MASS_WH_PER_M2_K,
    EMITTER_EXPONENT_MAP,
    ZONE_SUBENTRY_TYPE,
    GAS_SUBENTRY_TYPE,
    CONF_GAS_PRICE_SENSOR,
    CONF_GAS_BOILER_EFFICIENCY,
    CONF_GAS_CALORIFIC_VALUE,
    DEFAULT_GAS_BOILER_EFFICIENCY,
    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
)

STEP_SELECT_SOURCES = "select_sources"
STEP_PRICE_SETTINGS = "price_settings"
STEP_BASIC = "basic"
STEP_HEATING_CURVE_SETTINGS = "heating_curve_settings"


async def _test_api_connection(hass: HomeAssistant) -> str | None:
    """Test reachability of open-meteo.com before creating the entry.

    Every sensor this integration publishes ultimately depends on weather
    data from this API (WeatherDataCoordinator) - creating an entry that
    can never successfully refresh is a worse experience than catching it
    here, at config-flow time. Ported from battery_controller's
    config_flow.py (docs/redesign/REDESIGN.md), which checks the same API
    for the same reason. Returns an error key for async_show_form, or None
    on success.
    """
    session = async_get_clientsession(hass)
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(
        {
            "latitude": hass.config.latitude,
            "longitude": hass.config.longitude,
            "hourly": "shortwave_radiation",
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


def _build_zone_subentry_schema(
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the schema for a heating-zone subentry (phase 5c, REDESIGN.md).

    Deliberately narrow: only what plausibly differs *between rooms* in the
    same home (floor area, insulation, its own thermostat, its own target
    temperature). Price sensor, heating curve limits and heat pump
    parameters are shared from the main entry - see const.py's
    ZONE_SUBENTRY_TYPE comment.
    """
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required("name", default=defaults.get("name")): str,
            vol.Required(CONF_AREA_M2, default=defaults.get(CONF_AREA_M2)): vol.Coerce(
                float
            ),
            vol.Required(
                CONF_ENERGY_LABEL, default=defaults.get(CONF_ENERGY_LABEL)
            ): selector(
                {
                    "select": {
                        "options": ENERGY_LABELS,
                        "mode": "dropdown",
                        "custom_value": False,
                    }
                }
            ),
            vol.Optional(
                CONF_TARGET_INDOOR_TEMP,
                default=defaults.get(
                    CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP
                ),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_INDOOR_TEMPERATURE_SENSOR,
                default=defaults.get(CONF_INDOOR_TEMPERATURE_SENSOR),
            ): selector(
                {"entity": {"domain": "sensor", "device_class": "temperature"}}
            ),
        }
    )


def _validate_zone_subentry(user_input: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a heating-zone subentry's input."""
    name = str(user_input["name"]).strip()
    if not name:
        raise vol.Invalid("name_required")
    area_m2 = float(user_input[CONF_AREA_M2])
    if area_m2 <= 0:
        raise vol.Invalid("area_must_be_positive")
    return {
        "name": name,
        CONF_AREA_M2: area_m2,
        CONF_ENERGY_LABEL: user_input[CONF_ENERGY_LABEL],
        CONF_TARGET_INDOOR_TEMP: float(
            user_input.get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP)
        ),
        CONF_INDOOR_TEMPERATURE_SENSOR: user_input.get(CONF_INDOOR_TEMPERATURE_SENSOR),
    }


# ConfigSubentryFlow (and ConfigEntry.subentries) landed in HA well after
# this integration's floor version, and is not available at all in the HA
# release this repo's test environment can install (2024.3.x - a package-
# index ceiling, not a real HA release date). Rather than hard-depend on
# it and break setup entirely on any HA old enough to lack it, the zone-
# subentry feature (phase 5c, REDESIGN.md) degrades to simply not being
# offered: HeatingZoneSubentryFlow is only defined, and only registered in
# async_get_supported_subentry_types below, when the base class exists.
_ConfigSubentryFlow = getattr(config_entries, "ConfigSubentryFlow", None)

if _ConfigSubentryFlow is not None:

    class HeatingZoneSubentryFlow(_ConfigSubentryFlow):  # type: ignore[misc, valid-type]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
        """Flow for adding or editing an additional heating-zone subentry.

        Modelled directly on battery_controller's
        BatteryControllerBatterySubentryFlow / BatteryControllerPVSubentryFlow
        (docs/redesign/REDESIGN.md phase 5c) - verified against a live,
        running battery_controller install's subentries (config_entry
        diagnostics shared 2026-09-22), since this integration's own test
        environment cannot install an HA release new enough to exercise
        this class at all (see the comment above).
        """

        async def async_step_user(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle adding a new heating zone."""
            errors: dict[str, str] = {}
            if user_input is not None:
                try:
                    data = _validate_zone_subentry(user_input)
                except vol.Invalid:
                    errors["base"] = "invalid_zone_input"
                else:
                    return self.async_create_entry(title=data["name"], data=data)
            return self.async_show_form(
                step_id="user",
                data_schema=_build_zone_subentry_schema(),
                errors=errors,
            )

        async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle editing an existing heating zone."""
            errors: dict[str, str] = {}
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            current_data = dict(subentry.data)

            if user_input is not None:
                try:
                    data = _validate_zone_subentry(user_input)
                except vol.Invalid:
                    errors["base"] = "invalid_zone_input"
                else:
                    return self.async_update_and_abort(
                        entry, subentry, title=data["name"], data=data
                    )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_build_zone_subentry_schema(current_data),
                errors=errors,
            )

else:
    HeatingZoneSubentryFlow = None  # type: ignore[assignment,misc]


def _build_gas_boiler_subentry_schema(
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the schema for the (singleton) hybrid gas-boiler subentry.

    A plain entity selector, not a dropdown filtered by device_class, for
    the gas price sensor: Dutch dynamic-gas-price sensors don't reliably
    carry device_class="monetary" the way electricity price sensors often
    do.
    """
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_GAS_PRICE_SENSOR, default=defaults.get(CONF_GAS_PRICE_SENSOR)
            ): selector({"entity": {"domain": "sensor"}}),
            vol.Optional(
                CONF_GAS_BOILER_EFFICIENCY,
                default=defaults.get(
                    CONF_GAS_BOILER_EFFICIENCY, DEFAULT_GAS_BOILER_EFFICIENCY
                ),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_GAS_CALORIFIC_VALUE,
                default=defaults.get(
                    CONF_GAS_CALORIFIC_VALUE,
                    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
                ),
            ): vol.Coerce(float),
        }
    )


def _validate_gas_boiler_subentry(user_input: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the gas-boiler subentry's input."""
    gas_price_sensor = user_input.get(CONF_GAS_PRICE_SENSOR)
    if not gas_price_sensor:
        raise vol.Invalid("gas_price_sensor_required")
    efficiency = float(
        user_input.get(CONF_GAS_BOILER_EFFICIENCY, DEFAULT_GAS_BOILER_EFFICIENCY)
    )
    if not (0.5 <= efficiency <= 1.2):
        raise vol.Invalid("efficiency_out_of_range")
    calorific_value = float(
        user_input.get(CONF_GAS_CALORIFIC_VALUE, DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3)
    )
    if calorific_value <= 0:
        raise vol.Invalid("calorific_value_must_be_positive")
    return {
        CONF_GAS_PRICE_SENSOR: gas_price_sensor,
        CONF_GAS_BOILER_EFFICIENCY: efficiency,
        CONF_GAS_CALORIFIC_VALUE: calorific_value,
    }


if _ConfigSubentryFlow is not None:

    class HeatingGasBoilerSubentryFlow(_ConfigSubentryFlow):  # type: ignore[misc, valid-type]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
        """Flow for adding or editing the (singleton) hybrid gas-boiler subentry.

        Unlike HeatingZoneSubentryFlow (zero-to-many), a hybrid system has
        exactly one physical gas boiler - async_step_user aborts before
        showing the form if one is already configured, the same
        application-level singleton pattern HA's own single-instance
        integrations use for the main entry itself
        (_abort_if_unique_id_configured), just applied at the subentry
        level since ConfigSubentryFlow has no built-in cap.
        """

        async def async_step_user(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle adding the gas boiler subentry."""
            entry = self._get_entry()
            existing = getattr(entry, "subentries", {})
            if any(sub.subentry_type == GAS_SUBENTRY_TYPE for sub in existing.values()):
                return self.async_abort(reason="single_instance_allowed")

            errors: dict[str, str] = {}
            if user_input is not None:
                try:
                    data = _validate_gas_boiler_subentry(user_input)
                except vol.Invalid as err:
                    errors["base"] = (
                        str(err.error_message) or "invalid_gas_boiler_input"
                    )
                else:
                    return self.async_create_entry(title="Gas Boiler", data=data)
            return self.async_show_form(
                step_id="user",
                data_schema=_build_gas_boiler_subentry_schema(),
                errors=errors,
            )

        async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle editing the gas boiler subentry."""
            errors: dict[str, str] = {}
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            current_data = dict(subentry.data)

            if user_input is not None:
                try:
                    data = _validate_gas_boiler_subentry(user_input)
                except vol.Invalid as err:
                    errors["base"] = (
                        str(err.error_message) or "invalid_gas_boiler_input"
                    )
                else:
                    return self.async_update_and_abort(
                        entry, subentry, title="Gas Boiler", data=data
                    )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_build_gas_boiler_subentry_schema(current_data),
                errors=errors,
            )

else:
    HeatingGasBoilerSubentryFlow = None  # type: ignore[assignment,misc]


class HeatingCurveOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg, misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Handle a config flow for Heating Curve Optimizer."""

    VERSION = 1

    @classmethod
    @callback  # type: ignore[untyped-decorator]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type]:
        """Return supported subentry types (phase 5c, REDESIGN.md; hybrid
        gas-boiler comparison).

        Empty on an HA release too old to have ConfigSubentryFlow at all -
        see HeatingZoneSubentryFlow's/HeatingGasBoilerSubentryFlow's
        definitions above. Each type is independently None-guarded (both
        share the same HA-version gate today, but this stays correct if
        that ever changes).
        """
        types: dict[str, type] = {}
        if HeatingZoneSubentryFlow is not None:
            types[ZONE_SUBENTRY_TYPE] = HeatingZoneSubentryFlow
        if HeatingGasBoilerSubentryFlow is not None:
            types[GAS_SUBENTRY_TYPE] = HeatingGasBoilerSubentryFlow
        return types

    def __init__(self) -> None:
        super().__init__()

        self.context: ConfigFlowContext = {}
        self.configs: list[dict[str, Any]] = []
        self.source_type: str | None = None
        self.sources: list[str] | None = None
        self.price_settings: dict[str, Any] = {}
        self.consumption_price_sensor: str | None = None
        self.production_price_sensor: str | None = None
        self.area_m2: float | None = None
        self.energy_label: str | None = None
        self.glass_east_m2: float | None = None
        self.glass_west_m2: float | None = None
        self.glass_south_m2: float | None = None
        self.glass_u_value: float | None = None
        self.ventilation_type: str = DEFAULT_VENTILATION_TYPE
        self.ceiling_height: float = DEFAULT_CEILING_HEIGHT
        self.thermal_mass_class: str = DEFAULT_THERMAL_MASS_CLASS
        self.emitter_type: str = DEFAULT_EMITTER_TYPE
        self.pv_east_wp: float | None = None
        self.pv_south_wp: float | None = None
        self.pv_west_wp: float | None = None
        self.pv_tilt: float = DEFAULT_PV_TILT
        self.power_consumption: str | None = None
        self.indoor_temperature_sensor: str | None = None
        self.supply_temperature_sensor: str | None = None
        self.grid_import_sensor: str | None = None
        self.grid_export_sensor: str | None = None
        self.k_factor: float | None = None
        self.base_cop: float = DEFAULT_COP_AT_35
        self.outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT
        self.cop_compensation_factor: float = DEFAULT_COP_COMPENSATION_FACTOR
        self.planning_window: int = DEFAULT_PLANNING_WINDOW
        self.time_base: int = DEFAULT_TIME_BASE
        self.max_buffer_debt: float = DEFAULT_MAX_BUFFER_DEBT
        self.target_indoor_temp: float = DEFAULT_TARGET_INDOOR_TEMP
        self.indoor_temp_hysteresis: float = DEFAULT_INDOOR_TEMP_HYSTERESIS
        self.offset_delta_t: int = DEFAULT_OFFSET_DELTA_T
        self.heat_curve_min_outdoor: float = -20.0
        self.heat_curve_max_outdoor: float = 15.0
        self.heating_curve_offset: float = DEFAULT_HEATING_CURVE_OFFSET
        self.heat_curve_min: float = DEFAULT_HEAT_CURVE_MIN
        self.heat_curve_max: float = DEFAULT_HEAT_CURVE_MAX

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            choice = user_input[CONF_SOURCE_TYPE]
            if choice == STEP_BASIC:
                return await self.async_step_basic_options()
            if choice == STEP_HEATING_CURVE_SETTINGS:
                return await self.async_step_heating_curve_settings()
            if choice == STEP_PRICE_SETTINGS:
                return await self.async_step_price_settings()
            if choice == "finish":
                if self.area_m2 is None:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": "missing_basic"},
                    )
                if not self.configs:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": "no_blocks"},
                    )
                connection_error = await _test_api_connection(self.hass)
                if connection_error:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": connection_error},
                    )
                consumption_price_sensor = (
                    self.consumption_price_sensor
                    or self.price_settings.get(CONF_CONSUMPTION_PRICE_SENSOR)
                    or self.price_settings.get(CONF_PRICE_SENSOR)
                )
                production_price_sensor = (
                    self.production_price_sensor
                    or self.price_settings.get(CONF_PRODUCTION_PRICE_SENSOR)
                    or consumption_price_sensor
                )
                return self.async_create_entry(
                    title="Heating Curve Optimizer",
                    data=self._build_entry_data(
                        consumption_price_sensor, production_price_sensor
                    ),
                )
            self.source_type = choice
            return await self.async_step_select_sources()

        return self.async_show_form(step_id="user", data_schema=self._schema_user())

    def _build_entry_data(
        self, consumption_price_sensor: str | None, production_price_sensor: str | None
    ) -> dict[str, Any]:
        """Build entry data dictionary from instance attributes."""
        return {
            CONF_CONFIGS: self.configs,
            CONF_PRICE_SENSOR: consumption_price_sensor,
            CONF_CONSUMPTION_PRICE_SENSOR: consumption_price_sensor,
            CONF_PRODUCTION_PRICE_SENSOR: production_price_sensor,
            CONF_AREA_M2: self.area_m2,
            CONF_ENERGY_LABEL: self.energy_label,
            CONF_GLASS_EAST_M2: self.glass_east_m2,
            CONF_GLASS_WEST_M2: self.glass_west_m2,
            CONF_GLASS_SOUTH_M2: self.glass_south_m2,
            CONF_GLASS_U_VALUE: self.glass_u_value,
            CONF_VENTILATION_TYPE: self.ventilation_type,
            CONF_CEILING_HEIGHT: self.ceiling_height,
            CONF_THERMAL_MASS_CLASS: self.thermal_mass_class,
            CONF_EMITTER_TYPE: self.emitter_type,
            CONF_PV_EAST_WP: self.pv_east_wp,
            CONF_PV_SOUTH_WP: self.pv_south_wp,
            CONF_PV_WEST_WP: self.pv_west_wp,
            CONF_PV_TILT: self.pv_tilt,
            CONF_INDOOR_TEMPERATURE_SENSOR: self.indoor_temperature_sensor,
            CONF_POWER_CONSUMPTION: self.power_consumption,
            CONF_SUPPLY_TEMPERATURE_SENSOR: self.supply_temperature_sensor,
            CONF_GRID_IMPORT_SENSOR: self.grid_import_sensor,
            CONF_GRID_EXPORT_SENSOR: self.grid_export_sensor,
            CONF_K_FACTOR: self.k_factor,
            CONF_BASE_COP: self.base_cop,
            CONF_OUTDOOR_TEMP_COEFFICIENT: self.outdoor_temp_coefficient,
            CONF_COP_COMPENSATION_FACTOR: self.cop_compensation_factor,
            CONF_PLANNING_WINDOW: self.planning_window,
            CONF_TIME_BASE: self.time_base,
            CONF_MAX_BUFFER_DEBT: self.max_buffer_debt,
            CONF_TARGET_INDOOR_TEMP: self.target_indoor_temp,
            CONF_INDOOR_TEMP_HYSTERESIS: self.indoor_temp_hysteresis,
            CONF_OFFSET_DELTA_T: self.offset_delta_t,
            CONF_HEAT_CURVE_MIN_OUTDOOR: self.heat_curve_min_outdoor,
            CONF_HEAT_CURVE_MAX_OUTDOOR: self.heat_curve_max_outdoor,
            CONF_HEATING_CURVE_OFFSET: self.heating_curve_offset,
            CONF_HEAT_CURVE_MIN: self.heat_curve_min,
            CONF_HEAT_CURVE_MAX: self.heat_curve_max,
        }

    def _schema_user(self) -> vol.Schema:
        options = [{"value": STEP_BASIC, "label": "Basic Settings"}]
        options.extend({"value": t, "label": t.title()} for t in SOURCE_TYPES)
        options.append(
            {"value": STEP_HEATING_CURVE_SETTINGS, "label": "Heating Curve Settings"}
        )
        options.append({"value": STEP_PRICE_SETTINGS, "label": "Price Settings"})
        options.append({"value": "finish", "label": "Finish"})

        return vol.Schema(
            {
                vol.Required(CONF_SOURCE_TYPE): selector(
                    {
                        "select": {
                            "options": options,
                            "mode": "dropdown",
                            "custom_value": False,
                        }
                    }
                )
            }
        )

    def _get_energy_sensors(self) -> list[str]:
        """Get sorted list of energy sensors."""
        return sorted(
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") in ("energy", "gas")
        )

    def _get_power_sensors(self) -> list[str]:
        """Get sorted list of power sensors."""
        return sorted(
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") in ("power", "energy")
        )

    def _get_temperature_sensors(self) -> list[str]:
        """Get sorted list of temperature sensors."""
        return sorted(
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "temperature"
            or state.attributes.get("unit_of_measurement") in ["°C", "°F", "K"]
        )

    def _get_price_sensors(self) -> list[str]:
        """Get list of price sensors."""
        return [
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "monetary"
            or state.attributes.get("unit_of_measurement") == "€/kWh"
        ]

    async def async_step_basic_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Redirect to async_step_basic for backward compatibility."""
        return await self.async_step_basic(user_input)

    def _apply_heating_curve_input(self, user_input: dict[str, Any]) -> None:
        """Apply heating curve settings from user input."""
        self.supply_temperature_sensor = user_input.get(CONF_SUPPLY_TEMPERATURE_SENSOR)
        self.k_factor = float(user_input.get(CONF_K_FACTOR, DEFAULT_K_FACTOR))
        self.base_cop = float(user_input.get(CONF_BASE_COP, DEFAULT_COP_AT_35))
        self.outdoor_temp_coefficient = float(
            user_input.get(
                CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
            )
        )
        self.cop_compensation_factor = float(
            user_input.get(
                CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
            )
        )
        self.planning_window = int(
            user_input.get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW)
        )
        self.time_base = int(user_input.get(CONF_TIME_BASE, DEFAULT_TIME_BASE))
        self.max_buffer_debt = float(
            user_input.get(CONF_MAX_BUFFER_DEBT, DEFAULT_MAX_BUFFER_DEBT)
        )
        self.target_indoor_temp = float(
            user_input.get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP)
        )
        self.indoor_temp_hysteresis = float(
            user_input.get(CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS)
        )
        self.offset_delta_t = int(
            user_input.get(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T)
        )
        self.heat_curve_min_outdoor = float(
            user_input.get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0)
        )
        self.heat_curve_max_outdoor = float(
            user_input.get(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0)
        )
        self.heating_curve_offset = float(
            user_input.get(CONF_HEATING_CURVE_OFFSET, DEFAULT_HEATING_CURVE_OFFSET)
        )
        self.heat_curve_min = float(
            user_input.get(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN)
        )
        self.heat_curve_max = float(
            user_input.get(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX)
        )

    def _build_heating_curve_schema(self, temp_sensors: list[str]) -> vol.Schema:
        """Build schema for heating curve settings."""
        return vol.Schema(
            {
                vol.Optional(
                    CONF_SUPPLY_TEMPERATURE_SENSOR,
                    default=self.supply_temperature_sensor,
                ): selector(
                    {
                        "select": {
                            "options": temp_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Optional(
                    CONF_K_FACTOR, default=self.k_factor or DEFAULT_K_FACTOR
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_BASE_COP, default=self.base_cop or DEFAULT_COP_AT_35
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_OUTDOOR_TEMP_COEFFICIENT,
                    default=self.outdoor_temp_coefficient
                    or DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_COP_COMPENSATION_FACTOR,
                    default=self.cop_compensation_factor
                    or DEFAULT_COP_COMPENSATION_FACTOR,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_PLANNING_WINDOW,
                    default=self.planning_window or DEFAULT_PLANNING_WINDOW,
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_TIME_BASE,
                    default=self.time_base or DEFAULT_TIME_BASE,
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_MAX_BUFFER_DEBT,
                    default=self.max_buffer_debt or DEFAULT_MAX_BUFFER_DEBT,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_TARGET_INDOOR_TEMP,
                    default=self.target_indoor_temp or DEFAULT_TARGET_INDOOR_TEMP,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_INDOOR_TEMP_HYSTERESIS,
                    default=self.indoor_temp_hysteresis
                    or DEFAULT_INDOOR_TEMP_HYSTERESIS,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_OFFSET_DELTA_T,
                    default=self.offset_delta_t or DEFAULT_OFFSET_DELTA_T,
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_HEAT_CURVE_MIN_OUTDOOR,
                    default=self.heat_curve_min_outdoor,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HEAT_CURVE_MAX_OUTDOOR,
                    default=self.heat_curve_max_outdoor,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HEATING_CURVE_OFFSET,
                    default=self.heating_curve_offset,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HEAT_CURVE_MIN,
                    default=self.heat_curve_min,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HEAT_CURVE_MAX,
                    default=self.heat_curve_max,
                ): vol.Coerce(float),
            }
        )

    async def async_step_heating_curve_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_heating_curve_input(user_input)
            return await self.async_step_user()

        temp_sensors = self._get_temperature_sensors()
        schema = self._build_heating_curve_schema(temp_sensors)

        return self.async_show_form(
            step_id=STEP_HEATING_CURVE_SETTINGS, data_schema=schema
        )

    def _apply_basic_input(self, user_input: dict[str, Any]) -> None:
        """Apply basic settings from user input."""
        self.area_m2 = float(user_input[CONF_AREA_M2])
        self.energy_label = user_input[CONF_ENERGY_LABEL]
        self.glass_east_m2 = float(user_input.get(CONF_GLASS_EAST_M2, 0))
        self.glass_west_m2 = float(user_input.get(CONF_GLASS_WEST_M2, 0))
        self.glass_south_m2 = float(user_input.get(CONF_GLASS_SOUTH_M2, 0))
        self.glass_u_value = float(user_input.get(CONF_GLASS_U_VALUE, 1.2))
        self.ventilation_type = user_input.get(
            CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE
        )
        self.ceiling_height = float(
            user_input.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)
        )
        self.thermal_mass_class = user_input.get(
            CONF_THERMAL_MASS_CLASS, DEFAULT_THERMAL_MASS_CLASS
        )
        self.emitter_type = user_input.get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE)
        self.pv_east_wp = float(user_input.get(CONF_PV_EAST_WP, 0))
        self.pv_south_wp = float(user_input.get(CONF_PV_SOUTH_WP, 0))
        self.pv_west_wp = float(user_input.get(CONF_PV_WEST_WP, 0))
        self.pv_tilt = float(user_input.get(CONF_PV_TILT, DEFAULT_PV_TILT))
        self.indoor_temperature_sensor = user_input.get(CONF_INDOOR_TEMPERATURE_SENSOR)
        self.power_consumption = user_input.get(CONF_POWER_CONSUMPTION)
        self.grid_import_sensor = user_input.get(CONF_GRID_IMPORT_SENSOR)
        self.grid_export_sensor = user_input.get(CONF_GRID_EXPORT_SENSOR)

    def _build_basic_schema(
        self,
        power_sensors: list[str],
        temp_sensors: list[str],
        *,
        with_defaults: bool = False,
    ) -> vol.Schema:
        """Build schema for basic settings."""
        area_field = (
            vol.Required(CONF_AREA_M2, default=self.area_m2)
            if with_defaults
            else vol.Required(CONF_AREA_M2)
        )
        label_field = (
            vol.Required(CONF_ENERGY_LABEL, default=self.energy_label)
            if with_defaults
            else vol.Required(CONF_ENERGY_LABEL)
        )

        return vol.Schema(
            {
                area_field: vol.Coerce(float),
                label_field: selector(
                    {
                        "select": {
                            "options": ENERGY_LABELS,
                            "mode": "dropdown",
                            "custom_value": False,
                        }
                    }
                ),
                vol.Optional(
                    CONF_GLASS_EAST_M2, default=self.glass_east_m2 or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_GLASS_WEST_M2, default=self.glass_west_m2 or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_GLASS_SOUTH_M2, default=self.glass_south_m2 or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_GLASS_U_VALUE, default=self.glass_u_value or 1.2
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_VENTILATION_TYPE,
                    default=self.ventilation_type or DEFAULT_VENTILATION_TYPE,
                ): selector(
                    {
                        "select": {
                            "options": list(VENTILATION_TYPES.keys()),
                            "mode": "dropdown",
                            "translation_key": "ventilation_type",
                        }
                    }
                ),
                vol.Optional(
                    CONF_CEILING_HEIGHT,
                    default=self.ceiling_height or DEFAULT_CEILING_HEIGHT,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_THERMAL_MASS_CLASS,
                    default=self.thermal_mass_class or DEFAULT_THERMAL_MASS_CLASS,
                ): selector(
                    {
                        "select": {
                            "options": list(THERMAL_MASS_WH_PER_M2_K.keys()),
                            "mode": "dropdown",
                            "translation_key": "thermal_mass_class",
                        }
                    }
                ),
                vol.Optional(
                    CONF_EMITTER_TYPE,
                    default=self.emitter_type or DEFAULT_EMITTER_TYPE,
                ): selector(
                    {
                        "select": {
                            "options": list(EMITTER_EXPONENT_MAP.keys()),
                            "mode": "dropdown",
                            "translation_key": "emitter_type",
                        }
                    }
                ),
                vol.Optional(
                    CONF_PV_EAST_WP, default=self.pv_east_wp or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_PV_SOUTH_WP, default=self.pv_south_wp or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_PV_WEST_WP, default=self.pv_west_wp or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_PV_TILT, default=self.pv_tilt or DEFAULT_PV_TILT
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_INDOOR_TEMPERATURE_SENSOR,
                    default=self.indoor_temperature_sensor,
                ): selector(
                    {
                        "select": {
                            "options": temp_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Optional(
                    CONF_POWER_CONSUMPTION, default=self.power_consumption
                ): selector(
                    {
                        "select": {
                            "options": power_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                # Real-time PV-surplus controller (phase 5b, REDESIGN.md) -
                # optional, only used when control_mode is optimize_v2.
                vol.Optional(
                    CONF_GRID_IMPORT_SENSOR, default=self.grid_import_sensor
                ): selector(
                    {
                        "select": {
                            "options": power_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Optional(
                    CONF_GRID_EXPORT_SENSOR, default=self.grid_export_sensor
                ): selector(
                    {
                        "select": {
                            "options": power_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
            }
        )

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_basic_input(user_input)
            return await self.async_step_user()

        power_sensors = self._get_power_sensors()
        temp_sensors = self._get_temperature_sensors()
        schema = self._build_basic_schema(power_sensors, temp_sensors)

        return self.async_show_form(step_id=STEP_BASIC, data_schema=schema)

    def _update_source_config(self, sources: list[str]) -> None:
        """Update or add source configuration."""
        self.sources = sources
        new_config = {
            CONF_SOURCE_TYPE: self.source_type,
            CONF_SOURCES: self.sources,
        }

        # Find existing config with same source_type
        for i, cfg in enumerate(self.configs):
            if cfg.get(CONF_SOURCE_TYPE) == self.source_type:
                self.configs[i] = new_config
                return

        # Add new config if not found
        self.configs.append(new_config)

    def _get_default_sources(self) -> list[str]:
        """Get default sources for current source type."""
        for block in reversed(self.configs):
            if block[CONF_SOURCE_TYPE] == self.source_type:
                return list(block[CONF_SOURCES])
        return []

    async def async_step_select_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._update_source_config(user_input[CONF_SOURCES])
            return await self.async_step_user()

        all_sensors = self._get_energy_sensors()
        default_sources = self._get_default_sources()

        return self.async_show_form(
            step_id=STEP_SELECT_SOURCES,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCES, default=default_sources): selector(
                        {
                            "select": {
                                "options": all_sensors,
                                "multiple": True,
                                "mode": "dropdown",
                            }
                        }
                    )
                }
            ),
        )

    def _get_current_price_sensors(self) -> tuple[str, str]:
        """Get current consumption and production price sensors."""
        consumption = (
            self.consumption_price_sensor
            or self.price_settings.get(CONF_CONSUMPTION_PRICE_SENSOR)
            or self.price_settings.get(CONF_PRICE_SENSOR, "")
        )
        production = (
            self.production_price_sensor
            or self.price_settings.get(CONF_PRODUCTION_PRICE_SENSOR)
            or consumption
        )
        return consumption, production

    def _build_price_schema(self, price_sensors: list[str]) -> vol.Schema:
        """Build schema for price settings."""
        consumption, production = self._get_current_price_sensors()

        return vol.Schema(
            {
                vol.Required(
                    CONF_CONSUMPTION_PRICE_SENSOR, default=consumption
                ): selector(
                    {
                        "select": {
                            "options": price_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Required(
                    CONF_PRODUCTION_PRICE_SENSOR, default=production
                ): selector(
                    {
                        "select": {
                            "options": price_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
            }
        )

    async def async_step_price_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.consumption_price_sensor = user_input[CONF_CONSUMPTION_PRICE_SENSOR]
            self.production_price_sensor = user_input[CONF_PRODUCTION_PRICE_SENSOR]
            self.price_settings = dict(user_input)
            return await self.async_step_user()

        all_prices = self._get_price_sensors()
        schema = self._build_price_schema(all_prices)

        return self.async_show_form(
            step_id=STEP_PRICE_SETTINGS,
            data_schema=schema,
        )

    @staticmethod
    @callback  # type: ignore[untyped-decorator]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return HeatingCurveOptimizerOptionsFlowHandler(config_entry)


class HeatingCurveOptimizerOptionsFlowHandler(config_entries.OptionsFlow):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Handle updates to a config entry (options)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.configs: list[dict[str, Any]] = list(
            config_entry.options.get(
                CONF_CONFIGS, config_entry.data.get(CONF_CONFIGS, [])
            )
        )

        def _get(key: str, default: Any = None) -> Any:
            return config_entry.options.get(key, config_entry.data.get(key, default))

        self.area_m2 = _get(CONF_AREA_M2)
        self.energy_label = _get(CONF_ENERGY_LABEL)
        self.glass_east_m2 = _get(CONF_GLASS_EAST_M2)
        self.glass_west_m2 = _get(CONF_GLASS_WEST_M2)
        self.glass_south_m2 = _get(CONF_GLASS_SOUTH_M2)
        self.glass_u_value = _get(CONF_GLASS_U_VALUE, 1.2)
        self.ventilation_type = _get(CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE)
        self.ceiling_height = _get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)
        self.thermal_mass_class = _get(
            CONF_THERMAL_MASS_CLASS, DEFAULT_THERMAL_MASS_CLASS
        )
        self.emitter_type = _get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE)
        self.pv_east_wp = _get(CONF_PV_EAST_WP, 0)
        self.pv_south_wp = _get(CONF_PV_SOUTH_WP, 0)
        self.pv_west_wp = _get(CONF_PV_WEST_WP, 0)
        self.pv_tilt = _get(CONF_PV_TILT, DEFAULT_PV_TILT)
        self.indoor_temperature_sensor = _get(CONF_INDOOR_TEMPERATURE_SENSOR)
        self.power_consumption = _get(CONF_POWER_CONSUMPTION)
        self.supply_temperature_sensor = _get(CONF_SUPPLY_TEMPERATURE_SENSOR)
        self.grid_import_sensor = _get(CONF_GRID_IMPORT_SENSOR)
        self.grid_export_sensor = _get(CONF_GRID_EXPORT_SENSOR)
        self.k_factor = _get(CONF_K_FACTOR)
        self.base_cop = _get(CONF_BASE_COP, DEFAULT_COP_AT_35)
        self.outdoor_temp_coefficient = _get(
            CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
        )
        self.cop_compensation_factor = _get(
            CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
        )
        self.planning_window = _get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW)
        self.time_base = _get(CONF_TIME_BASE, DEFAULT_TIME_BASE)
        self.max_buffer_debt = _get(CONF_MAX_BUFFER_DEBT, DEFAULT_MAX_BUFFER_DEBT)
        self.target_indoor_temp = _get(
            CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP
        )
        self.indoor_temp_hysteresis = _get(
            CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS
        )
        self.offset_delta_t = _get(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T)
        self.heat_curve_min_outdoor = _get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0)
        self.heat_curve_max_outdoor = _get(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0)
        self.heating_curve_offset = _get(
            CONF_HEATING_CURVE_OFFSET, DEFAULT_HEATING_CURVE_OFFSET
        )
        self.heat_curve_min = _get(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN)
        self.heat_curve_max = _get(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX)
        self.price_settings = copy.deepcopy(
            config_entry.options.get(
                CONF_PRICE_SETTINGS,
                {},
            )
        )
        self.consumption_price_sensor = _get(CONF_CONSUMPTION_PRICE_SENSOR)
        self.production_price_sensor = _get(CONF_PRODUCTION_PRICE_SENSOR)
        price_sensor = _get(CONF_PRICE_SENSOR)
        if self.consumption_price_sensor is None:
            self.consumption_price_sensor = price_sensor
        if self.production_price_sensor is None:
            self.production_price_sensor = price_sensor
        if (
            self.consumption_price_sensor
            and CONF_CONSUMPTION_PRICE_SENSOR not in self.price_settings
        ):
            self.price_settings[CONF_CONSUMPTION_PRICE_SENSOR] = (
                self.consumption_price_sensor
            )
        if (
            self.production_price_sensor
            and CONF_PRODUCTION_PRICE_SENSOR not in self.price_settings
        ):
            self.price_settings[CONF_PRODUCTION_PRICE_SENSOR] = (
                self.production_price_sensor
            )
        if price_sensor and CONF_PRICE_SENSOR not in self.price_settings:
            self.price_settings[CONF_PRICE_SENSOR] = price_sensor
        self.source_type: str | None = None
        self.sources: list[str] | None = None

    # Reuse helper methods from ConfigFlow
    _get_energy_sensors = HeatingCurveOptimizerConfigFlow._get_energy_sensors
    _get_power_sensors = HeatingCurveOptimizerConfigFlow._get_power_sensors
    _get_temperature_sensors = HeatingCurveOptimizerConfigFlow._get_temperature_sensors
    _get_price_sensors = HeatingCurveOptimizerConfigFlow._get_price_sensors
    _apply_basic_input = HeatingCurveOptimizerConfigFlow._apply_basic_input
    _apply_heating_curve_input = (
        HeatingCurveOptimizerConfigFlow._apply_heating_curve_input
    )
    _build_heating_curve_schema = (
        HeatingCurveOptimizerConfigFlow._build_heating_curve_schema
    )
    _build_basic_schema = HeatingCurveOptimizerConfigFlow._build_basic_schema
    _build_price_schema = HeatingCurveOptimizerConfigFlow._build_price_schema
    _get_current_price_sensors = (
        HeatingCurveOptimizerConfigFlow._get_current_price_sensors
    )
    _update_source_config = HeatingCurveOptimizerConfigFlow._update_source_config
    _get_default_sources = HeatingCurveOptimizerConfigFlow._get_default_sources
    _build_entry_data = HeatingCurveOptimizerConfigFlow._build_entry_data

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input and CONF_SOURCE_TYPE in user_input:
            choice = user_input[CONF_SOURCE_TYPE]
            if choice == STEP_BASIC:
                return await self.async_step_basic()
            if choice == STEP_HEATING_CURVE_SETTINGS:
                return await self.async_step_heating_curve_settings()
            if choice == STEP_PRICE_SETTINGS:
                return await self.async_step_price_settings()
            if choice == "finish":
                if self.area_m2 is None:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": "missing_basic"},
                    )
                if not self.configs:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": "no_blocks"},
                    )
                consumption_price_sensor = (
                    self.consumption_price_sensor
                    or self.price_settings.get(CONF_CONSUMPTION_PRICE_SENSOR)
                    or self.price_settings.get(CONF_PRICE_SENSOR)
                )
                production_price_sensor = (
                    self.production_price_sensor
                    or self.price_settings.get(CONF_PRODUCTION_PRICE_SENSOR)
                    or consumption_price_sensor
                )
                return self.async_create_entry(
                    title="",
                    data=self._build_entry_data(
                        consumption_price_sensor, production_price_sensor
                    ),
                )
            self.source_type = choice
            return await self.async_step_select_sources()

        return self.async_show_form(step_id="user", data_schema=self._schema_user())

    def _schema_user(self) -> vol.Schema:
        options = [{"value": STEP_BASIC, "label": "Basic Settings"}]
        options.extend({"value": t, "label": t.title()} for t in SOURCE_TYPES)
        options.append(
            {"value": STEP_HEATING_CURVE_SETTINGS, "label": "Heating Curve Settings"}
        )
        options.append({"value": STEP_PRICE_SETTINGS, "label": "Price Settings"})
        options.append({"value": "finish", "label": "Finish"})

        return vol.Schema(
            {
                vol.Required(CONF_SOURCE_TYPE): selector(
                    {
                        "select": {
                            "options": options,
                            "mode": "dropdown",
                            "custom_value": False,
                        }
                    }
                )
            }
        )

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_basic_input(user_input)
            return await self.async_step_user()

        power_sensors = self._get_power_sensors()
        temp_sensors = self._get_temperature_sensors()
        schema = self._build_basic_schema(
            power_sensors, temp_sensors, with_defaults=True
        )

        return self.async_show_form(step_id=STEP_BASIC, data_schema=schema)

    async def async_step_heating_curve_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_heating_curve_input(user_input)
            return await self.async_step_user()

        temp_sensors = self._get_temperature_sensors()
        schema = self._build_heating_curve_schema(temp_sensors)

        return self.async_show_form(
            step_id=STEP_HEATING_CURVE_SETTINGS, data_schema=schema
        )

    async def async_step_select_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input and CONF_SOURCES in user_input:
            self._update_source_config(user_input[CONF_SOURCES])
            return await self.async_step_user()

        all_sensors = self._get_energy_sensors()
        default_sources = self._get_default_sources()

        return self.async_show_form(
            step_id=STEP_SELECT_SOURCES,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCES, default=default_sources): selector(
                        {
                            "select": {
                                "options": all_sensors,
                                "multiple": True,
                                "mode": "dropdown",
                            }
                        }
                    )
                }
            ),
        )

    async def async_step_price_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.consumption_price_sensor = user_input[CONF_CONSUMPTION_PRICE_SENSOR]
            self.production_price_sensor = user_input[CONF_PRODUCTION_PRICE_SENSOR]
            self.price_settings = dict(user_input)
            return await self.async_step_user()

        all_prices = self._get_price_sensors()
        schema = self._build_price_schema(all_prices)

        return self.async_show_form(
            step_id=STEP_PRICE_SETTINGS,
            data_schema=schema,
        )
