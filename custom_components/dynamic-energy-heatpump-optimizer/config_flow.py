from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, ConfigEntry
from typing import Any
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    DOMAIN,
    CONF_K_FACTOR,
    CONF_HEAT_LOSS_LABEL,
    CONF_FLOOR_AREA,
    CONF_PLANNING_HORIZON,
    HEAT_LOSS_FACTORS,
    DEFAULT_K_FACTOR,
    DEFAULT_PLANNING_HORIZON,
    CONF_PRICE_SENSOR,
    CONF_ENERGY_SENSORS,
    CONF_SOLAR_SENSORS,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_SUPPLY_TEMP_SENSOR,
    CONF_ROOM_TEMP_SENSOR,
    CONF_MAX_HEATPUMP_POWER,
    CONF_CURRENT_POWER_SENSOR,
)


class HeatpumpOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the heatpump optimizer."""

    VERSION = 1

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def _get_sensors(self, device_classes: set[str] | None = None) -> list[str]:
        sensors = []
        for state in self.hass.states.async_all("sensor"):
            if (
                device_classes is None
                or state.attributes.get("device_class") in device_classes
            ):
                sensors.append(state.entity_id)
        return sorted(sensors)

    async def _get_price_sensors(self) -> list[str]:
        return sorted(
            [
                state.entity_id
                for state in self.hass.states.async_all("sensor")
                if state.attributes.get("device_class") == "monetary"
                or state.attributes.get("unit_of_measurement") == "€/kWh"
            ]
        )

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_sensors()

        schema = vol.Schema(
            {
                vol.Required(CONF_K_FACTOR, default=DEFAULT_K_FACTOR): vol.Coerce(
                    float
                ),
                vol.Required(CONF_HEAT_LOSS_LABEL, default="A/B"): selector(
                    {
                        "select": {
                            "options": list(HEAT_LOSS_FACTORS.keys()),
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Required(CONF_FLOOR_AREA): vol.Coerce(float),
                vol.Required(
                    CONF_PLANNING_HORIZON,
                    default=DEFAULT_PLANNING_HORIZON,
                ): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_sensors(self, user_input=None):
        if user_input is not None:
            data = {**self.data, **user_input}
            return self.async_create_entry(title="Heatpump Optimizer", data=data)

        price_options = await self._get_price_sensors()
        energy_options = await self._get_sensors({"energy", "power"})
        temp_options = await self._get_sensors({"temperature"})

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRICE_SENSOR): selector(
                        {
                            "select": {
                                "options": price_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(CONF_ENERGY_SENSORS): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": True,
                            }
                        }
                    ),
                    vol.Required(CONF_SOLAR_SENSORS): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": True,
                            }
                        }
                    ),
                    vol.Required(CONF_OUTDOOR_TEMP_SENSOR): selector(
                        {
                            "select": {
                                "options": temp_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(CONF_SUPPLY_TEMP_SENSOR): selector(
                        {
                            "select": {
                                "options": temp_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(CONF_ROOM_TEMP_SENSOR): selector(
                        {
                            "select": {
                                "options": temp_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(CONF_CURRENT_POWER_SENSOR): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(CONF_MAX_HEATPUMP_POWER, default=5.0): vol.Coerce(
                        float
                    ),
                }
            ),
        )


class HeatpumpOptimizerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for the heatpump optimizer."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry
        self.data: dict[str, Any] = {}

    async def _get_sensors(self, device_classes: set[str] | None = None) -> list[str]:
        sensors = []
        for state in self.hass.states.async_all("sensor"):
            if (
                device_classes is None
                or state.attributes.get("device_class") in device_classes
            ):
                sensors.append(state.entity_id)
        return sorted(sensors)

    async def _get_price_sensors(self) -> list[str]:
        return sorted(
            [
                state.entity_id
                for state in self.hass.states.async_all("sensor")
                if state.attributes.get("device_class") == "monetary"
                or state.attributes.get("unit_of_measurement") == "€/kWh"
            ]
        )

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_sensors()

        data = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_K_FACTOR, default=data.get(CONF_K_FACTOR, DEFAULT_K_FACTOR)
                ): vol.Coerce(float),
                vol.Required(
                    CONF_HEAT_LOSS_LABEL, default=data.get(CONF_HEAT_LOSS_LABEL, "A/B")
                ): selector(
                    {
                        "select": {
                            "options": list(HEAT_LOSS_FACTORS.keys()),
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Required(
                    CONF_FLOOR_AREA, default=data.get(CONF_FLOOR_AREA, 0.0)
                ): vol.Coerce(float),
                vol.Required(
                    CONF_PLANNING_HORIZON,
                    default=data.get(CONF_PLANNING_HORIZON, DEFAULT_PLANNING_HORIZON),
                ): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_sensors(self, user_input=None):
        defaults = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            data = {**self.data, **user_input}
            return self.async_create_entry(title="", data=data)

        price_options = await self._get_price_sensors()
        energy_options = await self._get_sensors({"energy", "power"})
        temp_options = await self._get_sensors({"temperature"})

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRICE_SENSOR, default=defaults.get(CONF_PRICE_SENSOR, "")
                    ): selector(
                        {
                            "select": {
                                "options": price_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(
                        CONF_ENERGY_SENSORS,
                        default=defaults.get(CONF_ENERGY_SENSORS, []),
                    ): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": True,
                            }
                        }
                    ),
                    vol.Required(
                        CONF_SOLAR_SENSORS, default=defaults.get(CONF_SOLAR_SENSORS, [])
                    ): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": True,
                            }
                        }
                    ),
                    vol.Required(
                        CONF_OUTDOOR_TEMP_SENSOR,
                        default=defaults.get(CONF_OUTDOOR_TEMP_SENSOR, ""),
                    ): selector(
                        {
                            "select": {
                                "options": temp_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(
                        CONF_SUPPLY_TEMP_SENSOR,
                        default=defaults.get(CONF_SUPPLY_TEMP_SENSOR, ""),
                    ): selector(
                        {
                            "select": {
                                "options": temp_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(
                        CONF_ROOM_TEMP_SENSOR,
                        default=defaults.get(CONF_ROOM_TEMP_SENSOR, ""),
                    ): selector(
                        {
                            "select": {
                                "options": temp_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CURRENT_POWER_SENSOR,
                        default=defaults.get(CONF_CURRENT_POWER_SENSOR, ""),
                    ): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                    vol.Required(
                        CONF_MAX_HEATPUMP_POWER,
                        default=defaults.get(CONF_MAX_HEATPUMP_POWER, 5.0),
                    ): vol.Coerce(float),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        return HeatpumpOptimizerOptionsFlowHandler(config_entry)
