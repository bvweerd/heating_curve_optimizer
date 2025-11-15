from __future__ import annotations

import copy
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowContext, ConfigFlowResult
from homeassistant.core import callback
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
    CONF_PLANNING_WINDOW,
    CONF_TIME_BASE,
    CONF_POWER_CONSUMPTION,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
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
    DEFAULT_K_FACTOR,
    DEFAULT_PV_TILT,
    DEFAULT_COP_AT_35,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_TIME_BASE,
    DEFAULT_HEATING_CURVE_OFFSET,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_HEAT_CURVE_MAX,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    DOMAIN,
    ENERGY_LABELS,
    SOURCE_TYPES,
)

STEP_SELECT_SOURCES = "select_sources"
STEP_PRICE_SETTINGS = "price_settings"
STEP_BASIC = "basic"
STEP_HEATING_CURVE_SETTINGS = "heating_curve_settings"


class HeatingCurveOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Heating Curve Optimizer."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()

        self.context: ConfigFlowContext = {}
        self.configs: list[dict] = []
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
        self.pv_east_wp: float | None = None
        self.pv_south_wp: float | None = None
        self.pv_west_wp: float | None = None
        self.pv_tilt: float = DEFAULT_PV_TILT
        self.power_consumption: str | None = None
        self.indoor_temperature_sensor: str | None = None
        self.supply_temperature_sensor: str | None = None
        self.k_factor: float | None = None
        self.base_cop: float = DEFAULT_COP_AT_35
        self.outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT
        self.cop_compensation_factor: float = DEFAULT_COP_COMPENSATION_FACTOR
        self.planning_window: int = DEFAULT_PLANNING_WINDOW
        self.time_base: int = DEFAULT_TIME_BASE
        self.heat_curve_min_outdoor: float = -20.0
        self.heat_curve_max_outdoor: float = 15.0
        self.heating_curve_offset: float = DEFAULT_HEATING_CURVE_OFFSET
        self.heat_curve_min: float = DEFAULT_HEAT_CURVE_MIN
        self.heat_curve_max: float = DEFAULT_HEAT_CURVE_MAX

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
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
                    data={
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
                        CONF_PV_EAST_WP: self.pv_east_wp,
                        CONF_PV_SOUTH_WP: self.pv_south_wp,
                        CONF_PV_WEST_WP: self.pv_west_wp,
                        CONF_PV_TILT: self.pv_tilt,
                        CONF_INDOOR_TEMPERATURE_SENSOR: self.indoor_temperature_sensor,
                        CONF_POWER_CONSUMPTION: self.power_consumption,
                        CONF_SUPPLY_TEMPERATURE_SENSOR: self.supply_temperature_sensor,
                        CONF_K_FACTOR: self.k_factor,
                        CONF_BASE_COP: self.base_cop,
                        CONF_OUTDOOR_TEMP_COEFFICIENT: self.outdoor_temp_coefficient,
                        CONF_COP_COMPENSATION_FACTOR: self.cop_compensation_factor,
                        CONF_PLANNING_WINDOW: self.planning_window,
                        CONF_TIME_BASE: self.time_base,
                        CONF_HEAT_CURVE_MIN_OUTDOOR: self.heat_curve_min_outdoor,
                        CONF_HEAT_CURVE_MAX_OUTDOOR: self.heat_curve_max_outdoor,
                        CONF_HEATING_CURVE_OFFSET: self.heating_curve_offset,
                        CONF_HEAT_CURVE_MIN: self.heat_curve_min,
                        CONF_HEAT_CURVE_MAX: self.heat_curve_max,
                    },
                )
            self.source_type = choice
            return await self.async_step_select_sources()

        return self.async_show_form(step_id="user", data_schema=self._schema_user())

    def _schema_user(self) -> vol.Schema:
        options = [{"value": STEP_BASIC, "label": "Basic Settings"}]
        options.extend({"value": t, "label": t.title()} for t in SOURCE_TYPES)
        options.append({"value": STEP_HEATING_CURVE_SETTINGS, "label": "Heating Curve Settings"})
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

    async def _get_energy_sensors(self) -> list[str]:
        return sorted(
            [
                state.entity_id
                for state in self.hass.states.async_all("sensor")
                if state.attributes.get("device_class") == "energy"
                or state.attributes.get("device_class") == "gas"
            ]
        )

    async def _get_power_sensors(self) -> list[str]:
        return sorted(
            [
                state.entity_id
                for state in self.hass.states.async_all("sensor")
                if state.attributes.get("device_class") in ("power", "energy")
            ]
        )

    async def _get_temperature_sensors(self) -> list[str]:
        return sorted(
            [
                state.entity_id
                for state in self.hass.states.async_all("sensor")
                if state.attributes.get("device_class") == "temperature"
                or state.attributes.get("unit_of_measurement") in ["°C", "°F", "K"]
            ]
        )

    async def async_step_basic_options(self, user_input=None):
        if user_input is not None:
            self.area_m2 = float(user_input[CONF_AREA_M2])
            self.energy_label = user_input[CONF_ENERGY_LABEL]
            self.glass_east_m2 = float(user_input.get(CONF_GLASS_EAST_M2, 0))
            self.glass_west_m2 = float(user_input.get(CONF_GLASS_WEST_M2, 0))
            self.glass_south_m2 = float(user_input.get(CONF_GLASS_SOUTH_M2, 0))
            self.glass_u_value = float(user_input.get(CONF_GLASS_U_VALUE, 1.2))
            self.pv_east_wp = float(user_input.get(CONF_PV_EAST_WP, 0))
            self.pv_south_wp = float(user_input.get(CONF_PV_SOUTH_WP, 0))
            self.pv_west_wp = float(user_input.get(CONF_PV_WEST_WP, 0))
            self.pv_tilt = float(user_input.get(CONF_PV_TILT, DEFAULT_PV_TILT))
            self.indoor_temperature_sensor = user_input.get(
                CONF_INDOOR_TEMPERATURE_SENSOR
            )
            self.power_consumption = user_input.get(CONF_POWER_CONSUMPTION)
            return await self.async_step_user()

        power_sensors = await self._get_power_sensors()
        temp_sensors = await self._get_temperature_sensors()

        schema = vol.Schema(
            {
                vol.Required(CONF_AREA_M2): vol.Coerce(float),
                vol.Required(CONF_ENERGY_LABEL): selector(
                    {
                        "select": {
                            "options": ENERGY_LABELS,
                            "mode": "dropdown",
                            "custom_value": False,
                        }
                    }
                ),
                vol.Optional(CONF_GLASS_EAST_M2, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_GLASS_WEST_M2, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_GLASS_SOUTH_M2, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_GLASS_U_VALUE, default=1.2): vol.Coerce(float),
                vol.Optional(CONF_PV_EAST_WP, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_PV_SOUTH_WP, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_PV_WEST_WP, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_PV_TILT, default=DEFAULT_PV_TILT): vol.Coerce(float),
                vol.Optional(CONF_INDOOR_TEMPERATURE_SENSOR): selector(
                    {
                        "select": {
                            "options": temp_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Optional(CONF_POWER_CONSUMPTION): selector(
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

        return self.async_show_form(step_id=STEP_BASIC, data_schema=schema)

    async def async_step_heating_curve_settings(self, user_input=None):
        if user_input is not None:
            self.supply_temperature_sensor = user_input.get(
                CONF_SUPPLY_TEMPERATURE_SENSOR
            )
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
            return await self.async_step_user()

        temp_sensors = await self._get_temperature_sensors()

        schema = vol.Schema(
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

        return self.async_show_form(
            step_id=STEP_HEATING_CURVE_SETTINGS, data_schema=schema
        )

    async def async_step_basic(self, user_input=None):
        if user_input is not None:
            self.area_m2 = float(user_input[CONF_AREA_M2])
            self.energy_label = user_input[CONF_ENERGY_LABEL]
            self.glass_east_m2 = float(user_input.get(CONF_GLASS_EAST_M2, 0))
            self.glass_west_m2 = float(user_input.get(CONF_GLASS_WEST_M2, 0))
            self.glass_south_m2 = float(user_input.get(CONF_GLASS_SOUTH_M2, 0))
            self.glass_u_value = float(user_input.get(CONF_GLASS_U_VALUE, 1.2))
            self.pv_east_wp = float(user_input.get(CONF_PV_EAST_WP, 0))
            self.pv_south_wp = float(user_input.get(CONF_PV_SOUTH_WP, 0))
            self.pv_west_wp = float(user_input.get(CONF_PV_WEST_WP, 0))
            self.pv_tilt = float(user_input.get(CONF_PV_TILT, DEFAULT_PV_TILT))
            self.indoor_temperature_sensor = user_input.get(
                CONF_INDOOR_TEMPERATURE_SENSOR
            )
            self.power_consumption = user_input.get(CONF_POWER_CONSUMPTION)
            return await self.async_step_user()

        power_sensors = await self._get_power_sensors()
        temp_sensors = await self._get_temperature_sensors()

        schema = vol.Schema(
            {
                vol.Required(CONF_AREA_M2): vol.Coerce(float),
                vol.Required(CONF_ENERGY_LABEL): selector(
                    {
                        "select": {
                            "options": ENERGY_LABELS,
                            "mode": "dropdown",
                            "custom_value": False,
                        }
                    }
                ),
                vol.Optional(CONF_GLASS_EAST_M2, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_GLASS_WEST_M2, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_GLASS_SOUTH_M2, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_GLASS_U_VALUE, default=1.2): vol.Coerce(float),
                vol.Optional(CONF_PV_EAST_WP, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_PV_SOUTH_WP, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_PV_WEST_WP, default=0.0): vol.Coerce(float),
                vol.Optional(CONF_PV_TILT, default=DEFAULT_PV_TILT): vol.Coerce(float),
                vol.Optional(CONF_INDOOR_TEMPERATURE_SENSOR): selector(
                    {
                        "select": {
                            "options": temp_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Optional(CONF_POWER_CONSUMPTION): selector(
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

        return self.async_show_form(step_id=STEP_BASIC, data_schema=schema)

    async def async_step_select_sources(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self.sources = user_input[CONF_SOURCES]
            self.configs.append(
                {
                    CONF_SOURCE_TYPE: self.source_type,
                    CONF_SOURCES: self.sources,
                }
            )
            return await self.async_step_user()

        all_sensors = await self._get_energy_sensors()

        last = next(
            (
                block
                for block in reversed(self.configs)
                if block[CONF_SOURCE_TYPE] == self.source_type
            ),
            None,
        )
        default_sources = last[CONF_SOURCES] if last else []

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

    async def async_step_price_settings(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self.consumption_price_sensor = user_input[CONF_CONSUMPTION_PRICE_SENSOR]
            self.production_price_sensor = user_input[CONF_PRODUCTION_PRICE_SENSOR]
            self.price_settings = dict(user_input)
            return await self.async_step_user()

        all_prices = [
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "monetary"
            or state.attributes.get("unit_of_measurement") == "€/kWh"
        ]
        current_consumption_sensor = (
            self.consumption_price_sensor
            or self.price_settings.get(
                CONF_CONSUMPTION_PRICE_SENSOR,
                self.price_settings.get(CONF_PRICE_SENSOR, ""),
            )
        )
        current_production_sensor = (
            self.production_price_sensor
            or self.price_settings.get(
                CONF_PRODUCTION_PRICE_SENSOR,
                current_consumption_sensor,
            )
        )

        schema_fields: dict[Any, Any] = {
            vol.Required(
                CONF_CONSUMPTION_PRICE_SENSOR, default=current_consumption_sensor
            ): selector(
                {
                    "select": {
                        "options": all_prices,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Required(
                CONF_PRODUCTION_PRICE_SENSOR, default=current_production_sensor
            ): selector(
                {
                    "select": {
                        "options": all_prices,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
        }

        return self.async_show_form(
            step_id=STEP_PRICE_SETTINGS,
            data_schema=vol.Schema(schema_fields),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return HeatingCurveOptimizerOptionsFlowHandler(config_entry)


class HeatingCurveOptimizerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle updates to a config entry (options)."""

    def __init__(self, config_entry):
        self.configs = list(
            config_entry.options.get(
                CONF_CONFIGS, config_entry.data.get(CONF_CONFIGS, [])
            )
        )

        def _get(key: str, default=None):
            return config_entry.options.get(key, config_entry.data.get(key, default))

        self.area_m2 = _get(CONF_AREA_M2)
        self.energy_label = _get(CONF_ENERGY_LABEL)
        self.glass_east_m2 = _get(CONF_GLASS_EAST_M2)
        self.glass_west_m2 = _get(CONF_GLASS_WEST_M2)
        self.glass_south_m2 = _get(CONF_GLASS_SOUTH_M2)
        self.glass_u_value = _get(CONF_GLASS_U_VALUE, 1.2)
        self.pv_east_wp = _get(CONF_PV_EAST_WP, 0)
        self.pv_south_wp = _get(CONF_PV_SOUTH_WP, 0)
        self.pv_west_wp = _get(CONF_PV_WEST_WP, 0)
        self.pv_tilt = _get(CONF_PV_TILT, DEFAULT_PV_TILT)
        self.indoor_temperature_sensor = _get(CONF_INDOOR_TEMPERATURE_SENSOR)
        self.power_consumption = _get(CONF_POWER_CONSUMPTION)
        self.supply_temperature_sensor = _get(CONF_SUPPLY_TEMPERATURE_SENSOR)
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
        self.heat_curve_min_outdoor = _get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0)
        self.heat_curve_max_outdoor = _get(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0)
        self.heating_curve_offset = _get(CONF_HEATING_CURVE_OFFSET, DEFAULT_HEATING_CURVE_OFFSET)
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

    async def _get_energy_sensors(self) -> list[str]:
        return sorted(
            [
                state.entity_id
                for state in self.hass.states.async_all("sensor")
                if state.attributes.get("device_class") == "energy"
                or state.attributes.get("device_class") == "gas"
            ]
        )

    async def _get_power_sensors(self) -> list[str]:
        return sorted(
            [
                state.entity_id
                for state in self.hass.states.async_all("sensor")
                if state.attributes.get("device_class") in ("power", "energy")
            ]
        )

    async def async_step_init(self, user_input=None):
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
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
                    data={
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
                        CONF_PV_EAST_WP: self.pv_east_wp,
                        CONF_PV_SOUTH_WP: self.pv_south_wp,
                        CONF_PV_WEST_WP: self.pv_west_wp,
                        CONF_PV_TILT: self.pv_tilt,
                        CONF_INDOOR_TEMPERATURE_SENSOR: self.indoor_temperature_sensor,
                        CONF_POWER_CONSUMPTION: self.power_consumption,
                        CONF_SUPPLY_TEMPERATURE_SENSOR: self.supply_temperature_sensor,
                        CONF_K_FACTOR: self.k_factor,
                        CONF_BASE_COP: self.base_cop,
                        CONF_OUTDOOR_TEMP_COEFFICIENT: self.outdoor_temp_coefficient,
                        CONF_COP_COMPENSATION_FACTOR: self.cop_compensation_factor,
                        CONF_PLANNING_WINDOW: self.planning_window,
                        CONF_TIME_BASE: self.time_base,
                        CONF_HEAT_CURVE_MIN_OUTDOOR: self.heat_curve_min_outdoor,
                        CONF_HEAT_CURVE_MAX_OUTDOOR: self.heat_curve_max_outdoor,
                        CONF_HEATING_CURVE_OFFSET: self.heating_curve_offset,
                        CONF_HEAT_CURVE_MIN: self.heat_curve_min,
                        CONF_HEAT_CURVE_MAX: self.heat_curve_max,
                    },
                )
            self.source_type = choice
            return await self.async_step_select_sources()

        return self.async_show_form(step_id="user", data_schema=self._schema_user())

    def _schema_user(self) -> vol.Schema:
        options = [{"value": STEP_BASIC, "label": "Basic Settings"}]
        options.extend({"value": t, "label": t.title()} for t in SOURCE_TYPES)
        options.append({"value": STEP_HEATING_CURVE_SETTINGS, "label": "Heating Curve Settings"})
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

    async def async_step_basic(self, user_input=None):
        if user_input is not None:
            self.area_m2 = float(user_input[CONF_AREA_M2])
            self.energy_label = user_input[CONF_ENERGY_LABEL]
            self.glass_east_m2 = float(user_input.get(CONF_GLASS_EAST_M2, 0))
            self.glass_west_m2 = float(user_input.get(CONF_GLASS_WEST_M2, 0))
            self.glass_south_m2 = float(user_input.get(CONF_GLASS_SOUTH_M2, 0))
            self.glass_u_value = float(user_input.get(CONF_GLASS_U_VALUE, 1.2))
            self.pv_east_wp = float(user_input.get(CONF_PV_EAST_WP, 0))
            self.pv_south_wp = float(user_input.get(CONF_PV_SOUTH_WP, 0))
            self.pv_west_wp = float(user_input.get(CONF_PV_WEST_WP, 0))
            self.pv_tilt = float(user_input.get(CONF_PV_TILT, DEFAULT_PV_TILT))
            self.indoor_temperature_sensor = user_input.get(
                CONF_INDOOR_TEMPERATURE_SENSOR
            )
            self.power_consumption = user_input.get(CONF_POWER_CONSUMPTION)
            return await self.async_step_user()

        power_sensors = await self._get_power_sensors()
        temp_sensors = await HeatingCurveOptimizerConfigFlow._get_temperature_sensors(
            self
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_AREA_M2, default=self.area_m2): vol.Coerce(float),
                vol.Required(CONF_ENERGY_LABEL, default=self.energy_label): selector(
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
            }
        )

        return self.async_show_form(step_id=STEP_BASIC, data_schema=schema)

    async def async_step_heating_curve_settings(self, user_input=None):
        if user_input is not None:
            self.supply_temperature_sensor = user_input.get(
                CONF_SUPPLY_TEMPERATURE_SENSOR
            )
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
            return await self.async_step_user()

        temp_sensors = await HeatingCurveOptimizerConfigFlow._get_temperature_sensors(
            self
        )

        schema = vol.Schema(
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

        return self.async_show_form(
            step_id=STEP_HEATING_CURVE_SETTINGS, data_schema=schema
        )

    async def async_step_select_sources(self, user_input=None):
        if user_input and CONF_SOURCES in user_input:
            self.sources = user_input[CONF_SOURCES]
            self.configs.append(
                {
                    CONF_SOURCE_TYPE: self.source_type,
                    CONF_SOURCES: self.sources,
                }
            )
            return await self.async_step_user()

        all_sensors = [
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "energy"
        ]

        last = next(
            (
                block
                for block in reversed(self.configs)
                if block[CONF_SOURCE_TYPE] == self.source_type
            ),
            None,
        )
        default_sources = last[CONF_SOURCES] if last else []

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

    async def async_step_price_settings(self, user_input=None):
        if user_input is not None:
            self.consumption_price_sensor = user_input[CONF_CONSUMPTION_PRICE_SENSOR]
            self.production_price_sensor = user_input[CONF_PRODUCTION_PRICE_SENSOR]
            self.price_settings = dict(user_input)
            return await self.async_step_user()

        all_prices = [
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "monetary"
            or state.attributes.get("unit_of_measurement") == "€/kWh"
        ]
        current_consumption_sensor = (
            self.consumption_price_sensor
            or self.price_settings.get(
                CONF_CONSUMPTION_PRICE_SENSOR,
                self.price_settings.get(CONF_PRICE_SENSOR, ""),
            )
        )
        current_production_sensor = (
            self.production_price_sensor
            or self.price_settings.get(
                CONF_PRODUCTION_PRICE_SENSOR,
                current_consumption_sensor,
            )
        )

        schema_fields = {
            vol.Required(
                CONF_CONSUMPTION_PRICE_SENSOR, default=current_consumption_sensor
            ): selector(
                {
                    "select": {
                        "options": all_prices,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Required(
                CONF_PRODUCTION_PRICE_SENSOR, default=current_production_sensor
            ): selector(
                {
                    "select": {
                        "options": all_prices,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
        }

        return self.async_show_form(
            step_id=STEP_PRICE_SETTINGS,
            data_schema=vol.Schema(schema_fields),
        )
