"""Climate platform for Heating Curve Optimizer (phase 5, REDESIGN.md).

A native HA climate entity for the same target-temperature control the
`number.TargetIndoorTemperatureNumber` entity already exposes - not a
second, independent setpoint: `async_set_temperature` routes through the
number entity's own `set_value` service call (via the entity registry, by
unique_id) so there is exactly one place that setpoint actually lives, and
every other consumer of it (HeatCalculationCoordinator's runtime-data read,
the number entity's own displayed state) stays consistent.

Disabled by default. This integration has never actuated a real heat pump
or thermostat - it only ever publishes sensors and lets the user
automate on them - and a climate entity risks being mistaken for direct
control of the heating system when it is really a read/write view onto
the same target temperature the number entity already manages. Kept
available, opt-in, because the native HA climate card is still the most
natural way to see and adjust "what temperature do I want" for users who
understand that distinction.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HeatCalculationCoordinator

_LOGGER = logging.getLogger(__name__)

# Entities are updated via their coordinator, never by per-entity I/O,
# so there is no reason to serialize updates against each other.
PARALLEL_UPDATES = 0

TARGET_TEMP_MIN = 15.0
TARGET_TEMP_MAX = 25.0
TARGET_TEMP_STEP = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the climate entity from a config entry.

    No entity is created without a primary heating zone (see __init__.py's
    _find_primary_zone_subentry) - there is no target temperature to
    reflect/adjust until one is configured.
    """
    runtime_data = entry.runtime_data
    heat_coordinator = runtime_data.heat_coordinator
    if heat_coordinator is None:
        _LOGGER.debug(
            "Skipping climate entity for %s: no primary heating zone configured yet",
            entry.entry_id,
        )
        return
    device: DeviceInfo = runtime_data.device

    async_add_entities([HeatingOptimizerClimate(hass, entry, device, heat_coordinator)])


class HeatingOptimizerClimate(
    CoordinatorEntity[HeatCalculationCoordinator], ClimateEntity  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
):
    """Climate entity reflecting/adjusting the target indoor temperature."""

    _attr_has_entity_name = True
    _attr_translation_key = "heating"
    _attr_name = "Heating"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp = TARGET_TEMP_MIN
    _attr_max_temp = TARGET_TEMP_MAX
    _attr_target_temperature_step = TARGET_TEMP_STEP
    _attr_entity_registry_enabled_default = False  # opt-in, see module docstring

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device: DeviceInfo,
        heat_coordinator: HeatCalculationCoordinator,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(heat_coordinator)
        self.hass = hass
        self._entry = entry
        self._attr_device_info = device
        self._attr_unique_id = f"{entry.entry_id}_climate"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the current indoor temperature, if a real sensor is configured."""
        if not self.coordinator.data or not self.coordinator.has_real_indoor_sensor:
            return None
        value = self.coordinator.data.get("indoor_temperature")
        return float(value) if value is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target indoor temperature (same value number.py manages)."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("target_temperature")
        return float(value) if value is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode.

        Always HEAT: this integration only ever optimizes/reports heating,
        it never turns the underlying system off (that stays the real
        thermostat's job) - see the module docstring on why this entity
        does not actuate anything.
        """
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return whether the modelled heat demand is currently active."""
        if not self.coordinator.data:
            return None
        return (
            HVACAction.HEATING
            if self.coordinator.data.get("heat_pump_on")
            else HVACAction.IDLE
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """No-op: HEAT is the only supported mode (see hvac_mode)."""
        if hvac_mode != HVACMode.HEAT:
            _LOGGER.warning(
                "Ignoring unsupported HVAC mode %s - this entity only reports "
                "HEAT, it does not turn heating off",
                hvac_mode,
            )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature by routing through the number entity.

        Deliberately not writing runtime data directly: that would leave
        `number.TargetIndoorTemperatureNumber`'s own displayed state stale
        until its next restart, since it holds `_attr_native_value` locally
        rather than reading runtime data back. Going through its
        `set_value` service call keeps there being exactly one place this
        setpoint lives.
        """
        temperature = kwargs.get("temperature")
        if temperature is None:
            return

        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{self._entry.entry_id}_target_indoor_temp"
        )
        if entity_id is None:
            _LOGGER.warning(
                "Cannot set target temperature: target_indoor_temp number "
                "entity not found in the entity registry"
            )
            return

        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": temperature},
            blocking=True,
        )
