from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    DOMAIN,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_SENSOR,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_SUPPLY_TEMP_SENSOR,
    CONF_HEAT_LOSS_SENSOR,
)


class HeatpumpOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the heatpump optimizer."""

    VERSION = 1

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
            return self.async_create_entry(title="Heatpump Optimizer", data=user_input)

        price_options = await self._get_price_sensors()
        energy_options = await self._get_sensors({"energy", "power"})
        temp_options = await self._get_sensors({"temperature"})

        return self.async_show_form(
            step_id="user",
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
                    vol.Required(CONF_SOLAR_SENSOR): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": False,
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
                    vol.Required(CONF_HEAT_LOSS_SENSOR): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                }
            ),
        )


class HeatpumpOptimizerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for the heatpump optimizer."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

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
            return self.async_create_entry(title="", data=user_input)

        price_options = await self._get_price_sensors()
        energy_options = await self._get_sensors({"energy", "power"})
        temp_options = await self._get_sensors({"temperature"})

        defaults = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="init",
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
                        CONF_SOLAR_SENSOR, default=defaults.get(CONF_SOLAR_SENSOR, "")
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
                        CONF_HEAT_LOSS_SENSOR,
                        default=defaults.get(CONF_HEAT_LOSS_SENSOR, ""),
                    ): selector(
                        {
                            "select": {
                                "options": energy_options,
                                "mode": "dropdown",
                                "multiple": False,
                            }
                        }
                    ),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        return HeatpumpOptimizerOptionsFlowHandler(config_entry)
