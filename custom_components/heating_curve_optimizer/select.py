"""Select platform for Heating Curve Optimizer.

Fase 3 of docs/redesign/REDESIGN.md: `control_mode` decides which engine
actually drives `optimized_offset` (and therefore every sensor and
automation built on it):

- ``legacy`` (default) - today's dynamic-programming optimizer, unchanged.
  Existing installations see zero behaviour change unless they explicitly
  opt in below.
- ``follow_curve`` - a clean baseline: offset forced to 0, pure heating
  curve, no optimization at all. Useful both as a manual override and as
  the "what would doing nothing cost" comparison point.
- ``optimize_v2`` - the redesigned thermal optimizer (building_model.py /
  heatpump_model.py / thermal_optimizer.py) takes over. Falls back to the
  legacy result for any cycle where it errors, so switching this on can
  never leave the heating system without a decision.

Modelled directly on battery_controller's `select.py`
(`BatteryControlModeSelect`): persist to config-entry options only (no
reload), mirror the value onto the coordinator, and request a refresh so
the change takes effect immediately instead of waiting for the next
scheduled update.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONTROL_MODE, CONTROL_MODES, DOMAIN
from .coordinator import OptimizationCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    optimization_coordinator: OptimizationCoordinator = entry_data[
        "optimization_coordinator"
    ]
    device: DeviceInfo = entry_data["device"]

    async_add_entities(
        [HeatingControlModeSelect(hass, entry, device, optimization_coordinator)]
    )


class HeatingControlModeSelect(
    CoordinatorEntity[OptimizationCoordinator], SelectEntity
):
    """Select entity choosing which optimizer drives the heating curve."""

    _attr_has_entity_name = True
    _attr_translation_key = "control_mode"
    _attr_name = "Control Mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = CONTROL_MODES

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device: DeviceInfo,
        optimization_coordinator: OptimizationCoordinator,
    ):
        """Initialize the select entity."""
        super().__init__(optimization_coordinator)
        self.hass = hass
        self._entry = entry
        self._attr_device_info = device
        self._attr_unique_id = f"{entry.entry_id}_control_mode"

    @property
    def current_option(self) -> str:
        """Return the active control mode."""
        return str(self.coordinator.control_mode)

    async def async_select_option(self, option: str) -> None:
        """Set the active control mode."""
        if option not in CONTROL_MODES:
            _LOGGER.warning("Invalid control mode: %s", option)
            return

        _LOGGER.info("Setting heating control mode to: %s", option)
        self.coordinator.control_mode = option

        # Persist to config-entry options only. __init__.py's
        # _update_listener recognizes CONF_CONTROL_MODE as a
        # _NO_RELOAD_KEYS entry and skips the reload it would otherwise
        # trigger - the live coordinator above already has the new mode,
        # so a reload here would only discard its in-flight buffer/offset
        # state for nothing.
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_CONTROL_MODE: option},
        )

        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
