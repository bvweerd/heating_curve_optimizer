from homeassistant.helpers.entity import Entity
from .const import DOMAIN

async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([DynamicOffsetSensor(coordinator)], True)

class DynamicOffsetSensor(Entity):
    def __init__(self, coordinator):
        self._coordinator = coordinator
        self._attr_name = "Dynamic Heat Offset"
        self._attr_unique_id = "dynamic_heat_offset"

    @property
    def state(self):
        return self._coordinator.data.get("offsets", [0])[0]

    @property
    def extra_state_attributes(self):
        return {
            "future_offsets": self._coordinator.data.get("offsets", []),
            "indoor_temperature_forecast": self._coordinator.data.get("indoor_forecast", [])
        }

    async def async_update(self):
        await self._coordinator.async_request_refresh()