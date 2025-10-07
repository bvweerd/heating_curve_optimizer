"""Binary sensors for the Heating Curve Optimizer integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_AREA_M2, CONF_ENERGY_LABEL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class HeatDemandBinarySensor(BinarySensorEntity):
    """Binary sensor that indicates whether the heat pump has demand."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_should_poll = True

    def __init__(self, hass: HomeAssistant, entry_id: str, device: DeviceInfo) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_heat_pump_demand"
        self._attr_translation_key = "heat_pump_demand"
        self._attr_has_entity_name = True
        self._attr_device_info = device
        self._attr_icon = "mdi:radiator"
        self._attr_available = False
        self._attr_is_on = False
        self._extra_attrs: dict[str, str | float] = {}

    @property
    def extra_state_attributes(self) -> dict[str, str | float]:
        return self._extra_attrs

    def _get_runtime_entry(self) -> dict[str, object]:
        domain_data = self.hass.data.get(DOMAIN, {})
        runtime = domain_data.get("runtime", {})
        entry = runtime.get(self._entry_id)
        return entry or {}

    async def async_update(self) -> None:
        runtime_entry = self._get_runtime_entry()
        entity_id = runtime_entry.get("net_heat_entity")
        if not entity_id:
            if not self.available:
                _LOGGER.debug(
                    "Heat demand binary sensor for %s waiting for net heat sensor",
                    self._entry_id,
                )
            self._attr_available = False
            self._extra_attrs = {}
            return

        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            self._attr_available = False
            self._extra_attrs = {"net_heat_entity_id": entity_id}
            return

        try:
            net_heat = float(state.state)
        except (TypeError, ValueError):
            self._attr_available = False
            self._extra_attrs = {"net_heat_entity_id": entity_id}
            return

        self._attr_available = True
        self._attr_is_on = net_heat > 0.0
        self._extra_attrs = {
            "net_heat_entity_id": entity_id,
            "net_heat_kW": round(net_heat, 3),
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the binary sensors for a config entry."""

    if not entry.data.get(CONF_AREA_M2) or not entry.data.get(CONF_ENERGY_LABEL):
        _LOGGER.debug(
            "Skipping heat demand binary sensor for %s because heat loss configuration is missing",
            entry.entry_id,
        )
        return

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heating Curve Optimizer",
    )

    async_add_entities(
        [HeatDemandBinarySensor(hass, entry.entry_id, device_info)], True
    )
