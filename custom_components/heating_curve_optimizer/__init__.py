# custom_components/heating_curve_optimizer/__init__.py

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS

import logging

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the base integration (no YAML)."""
    hass.data.setdefault(DOMAIN, {})
    _LOGGER.info("Initialized Heating Curve Optimizer")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry by forwarding to sensor & number platforms."""
    _LOGGER.info("Setting up entry %s", entry.entry_id)

    # Store entry data
    hass.data[DOMAIN][entry.entry_id] = entry.data

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    # Forward entry to ALL our platforms in one call:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Forwarded entry %s to platforms %s", entry.entry_id, PLATFORMS)
    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the config entry."""
    _LOGGER.debug("Reloading config entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and its platforms."""
    _LOGGER.info("Unloading entry %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop("entities", None)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.debug("Successfully unloaded entry %s", entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    else:
        _LOGGER.warning("Failed to unload entry %s", entry.entry_id)
    return unload_ok
