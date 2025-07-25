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
    CONF_POWER_CONSUMPTION,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_K_FACTOR,
    CONF_PRICE_SENSOR,
    CONF_PRICE_SETTINGS,
    CONF_SOLAR_FORECAST,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    DOMAIN,
    ENERGY_LABELS,
    SOURCE_TYPES,
)

STEP_SELECT_SOURCES = "select_sources"
STEP_PRICE_SETTINGS = "price_settings"
STEP_BASIC = "basic"


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
        self.area_m2: float | None = None
        self.energy_label: str | None = None
        self.glass_east_m2: float | None = None
        self.glass_west_m2: float | None = None
        self.glass_south_m2: float | None = None
        self.glass_u_value: float | None = None
        self.solar_forecast: list[str] | None = None
        self.power_consumption: str | None = None
        self.indoor_temperature_sensor: str | None = None
        self.supply_temperature_sensor: str | None = None
        self.k_factor: float | None = None

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
                return self.async_create_entry(
                    title="Heating Curve Optimizer",
                    data={
                        CONF_CONFIGS: self.configs,
                        CONF_PRICE_SENSOR: self.price_settings.get(CONF_PRICE_SENSOR),
                        CONF_AREA_M2: self.area_m2,
                        CONF_ENERGY_LABEL: self.energy_label,
                        CONF_GLASS_EAST_M2: self.glass_east_m2,
                        CONF_GLASS_WEST_M2: self.glass_west_m2,
                        CONF_GLASS_SOUTH_M2: self.glass_south_m2,
                        CONF_GLASS_U_VALUE: self.glass_u_value,
                        CONF_SOLAR_FORECAST: self.solar_forecast,
                        CONF_INDOOR_TEMPERATURE_SENSOR: self.indoor_temperature_sensor,
                        CONF_POWER_CONSUMPTION: self.power_consumption,
                        CONF_SUPPLY_TEMPERATURE_SENSOR: self.supply_temperature_sensor,
                        CONF_K_FACTOR: self.k_factor,
                    },
                )
            self.source_type = choice
            return await self.async_step_select_sources()

        return self.async_show_form(step_id="user", data_schema=self._schema_user())

    def _schema_user(self) -> vol.Schema:
        options = [{"value": STEP_BASIC, "label": "Basic Settings"}]
        options.extend({"value": t, "label": t.title()} for t in SOURCE_TYPES)
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
            ]
        )

    async def _get_temperature_sensors(self) -> list[str]:
        return sorted(
            [
                state.entity_id
                for state in self.hass.states.async_all("sensor")
                if state.attributes.get("device_class") == "temperature"
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
            sf = user_input[CONF_SOLAR_FORECAST]
            if isinstance(sf, str):
                sf = [sf]
            self.solar_forecast = sf
            self.indoor_temperature_sensor = user_input.get(
                CONF_INDOOR_TEMPERATURE_SENSOR
            )
            self.power_consumption = user_input.get(CONF_POWER_CONSUMPTION)
            self.supply_temperature_sensor = user_input.get(
                CONF_SUPPLY_TEMPERATURE_SENSOR
            )
            self.k_factor = float(user_input.get(CONF_K_FACTOR, 1.0))
            return await self.async_step_user()

        energy_sensors = await self._get_energy_sensors()
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
                vol.Required(CONF_SOLAR_FORECAST): selector(
                    {
                        "select": {
                            "options": energy_sensors,
                            "multiple": True,
                            "mode": "dropdown",
                        }
                    }
                ),
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
                vol.Optional(CONF_K_FACTOR, default=self.k_factor or 3.5): vol.Coerce(
                    float
                ),
            }
        )

        return self.async_show_form(step_id=STEP_BASIC, data_schema=schema)

    async def async_step_basic(self, user_input=None):
        if user_input is not None:
            self.area_m2 = float(user_input[CONF_AREA_M2])
            self.energy_label = user_input[CONF_ENERGY_LABEL]
            self.glass_east_m2 = float(user_input.get(CONF_GLASS_EAST_M2, 0))
            self.glass_west_m2 = float(user_input.get(CONF_GLASS_WEST_M2, 0))
            self.glass_south_m2 = float(user_input.get(CONF_GLASS_SOUTH_M2, 0))
            self.glass_u_value = float(user_input.get(CONF_GLASS_U_VALUE, 1.2))
            sf = user_input[CONF_SOLAR_FORECAST]
            if isinstance(sf, str):
                sf = [sf]
            self.solar_forecast = sf
            self.indoor_temperature_sensor = user_input.get(
                CONF_INDOOR_TEMPERATURE_SENSOR
            )
            self.power_consumption = user_input.get(CONF_POWER_CONSUMPTION)
            self.supply_temperature_sensor = user_input.get(
                CONF_SUPPLY_TEMPERATURE_SENSOR
            )
            self.k_factor = float(user_input.get(CONF_K_FACTOR, 1.0))
            return await self.async_step_user()

        energy_sensors = await self._get_energy_sensors()
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
                vol.Required(CONF_SOLAR_FORECAST): selector(
                    {
                        "select": {
                            "options": energy_sensors,
                            "multiple": True,
                            "mode": "dropdown",
                        }
                    }
                ),
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
                vol.Optional(CONF_K_FACTOR, default=self.k_factor or 3.5): vol.Coerce(
                    float
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
            self.price_settings = dict(user_input)
            return await self.async_step_user()

        all_prices = [
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "monetary"
            or state.attributes.get("unit_of_measurement") == "€/kWh"
        ]
        current_price_sensor = self.price_settings.get(CONF_PRICE_SENSOR, "")

        schema_fields: dict[Any, Any] = {
            vol.Required(CONF_PRICE_SENSOR, default=current_price_sensor): selector(
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
        self.area_m2 = config_entry.data.get(CONF_AREA_M2)
        self.energy_label = config_entry.data.get(CONF_ENERGY_LABEL)
        self.glass_east_m2 = config_entry.data.get(CONF_GLASS_EAST_M2)
        self.glass_west_m2 = config_entry.data.get(CONF_GLASS_WEST_M2)
        self.glass_south_m2 = config_entry.data.get(CONF_GLASS_SOUTH_M2)
        self.glass_u_value = config_entry.data.get(CONF_GLASS_U_VALUE, 1.2)
        sf = config_entry.data.get(CONF_SOLAR_FORECAST)
        if isinstance(sf, str):
            self.solar_forecast = [sf]
        else:
            self.solar_forecast = sf
        self.indoor_temperature_sensor = config_entry.data.get(
            CONF_INDOOR_TEMPERATURE_SENSOR
        )
        self.power_consumption = config_entry.data.get(CONF_POWER_CONSUMPTION)
        self.supply_temperature_sensor = config_entry.data.get(
            CONF_SUPPLY_TEMPERATURE_SENSOR
        )
        self.k_factor = config_entry.data.get(CONF_K_FACTOR)
        self.price_settings = copy.deepcopy(
            config_entry.options.get(
                CONF_PRICE_SETTINGS,
                {},
            )
        )
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
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_CONFIGS: self.configs,
                        CONF_PRICE_SENSOR: self.price_settings.get(CONF_PRICE_SENSOR),
                        CONF_AREA_M2: self.area_m2,
                        CONF_ENERGY_LABEL: self.energy_label,
                        CONF_GLASS_EAST_M2: self.glass_east_m2,
                        CONF_GLASS_WEST_M2: self.glass_west_m2,
                        CONF_GLASS_SOUTH_M2: self.glass_south_m2,
                        CONF_GLASS_U_VALUE: self.glass_u_value,
                        CONF_SOLAR_FORECAST: self.solar_forecast,
                        CONF_INDOOR_TEMPERATURE_SENSOR: self.indoor_temperature_sensor,
                        CONF_POWER_CONSUMPTION: self.power_consumption,
                        CONF_SUPPLY_TEMPERATURE_SENSOR: self.supply_temperature_sensor,
                        CONF_K_FACTOR: self.k_factor,
                    },
                )
            self.source_type = choice
            return await self.async_step_select_sources()

        return self.async_show_form(step_id="user", data_schema=self._schema_user())

    def _schema_user(self) -> vol.Schema:
        options = [{"value": STEP_BASIC, "label": "Basic Settings"}]
        options.extend({"value": t, "label": t.title()} for t in SOURCE_TYPES)
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
            self.price_settings = dict(user_input)
            return await self.async_step_user()

        all_prices = [
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "monetary"
            or state.attributes.get("unit_of_measurement") == "€/kWh"
        ]
        current_price_sensor = self.price_settings.get(CONF_PRICE_SENSOR, "")

        schema_fields = {
            vol.Required(CONF_PRICE_SENSOR, default=current_price_sensor): selector(
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
