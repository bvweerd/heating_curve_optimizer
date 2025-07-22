from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, ConfigEntry

from .const import DOMAIN


class HeatpumpOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the heatpump optimizer."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return self.async_create_entry(title="Heatpump Optimizer", data={})
        return self.async_show_form(step_id="user")


class HeatpumpOptimizerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for the heatpump optimizer."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init")
