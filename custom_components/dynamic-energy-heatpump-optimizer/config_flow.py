from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, ConfigEntry
import voluptuous as vol
from homeassistant.helpers.selector import selector

from .const import (
    DOMAIN,
    CONF_K_FACTOR,
    CONF_HEAT_LOSS_LABEL,
    CONF_FLOOR_AREA,
    HEAT_LOSS_FACTORS,
    DEFAULT_K_FACTOR,
)


class HeatpumpOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the heatpump optimizer."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return self.async_create_entry(title="Heatpump Optimizer", data=user_input)
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
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)


class HeatpumpOptimizerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for the heatpump optimizer."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
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
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
