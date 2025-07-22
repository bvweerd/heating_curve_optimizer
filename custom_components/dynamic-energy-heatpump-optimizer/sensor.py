from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the placeholder sensor."""
    async_add_entities([HeatpumpOptimizerSensor(entry)])


class HeatpumpOptimizerSensor(SensorEntity):
    """Placeholder sensor for future optimization output."""

    _attr_name = "Heatpump Optimizer"
    _attr_unique_id = "heatpump_optimizer"
    _attr_native_value = 0

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
