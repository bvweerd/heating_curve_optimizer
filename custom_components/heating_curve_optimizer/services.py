"""Service registration for the Heating Curve Optimizer integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN
from .sensor import UTILITY_ENTITIES


async def async_register_services(hass: HomeAssistant) -> None:
    """Register Heating Curve Optimizer services."""

    async def handle_reset(call: ServiceCall) -> None:
        for entity in UTILITY_ENTITIES:
            await entity.async_reset()

    hass.services.async_register(DOMAIN, "reset", handle_reset)


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister Heating Curve Optimizer services."""
    hass.services.async_remove(DOMAIN, "reset")
