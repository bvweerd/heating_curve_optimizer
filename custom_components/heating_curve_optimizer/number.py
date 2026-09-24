"""Number platform for Heating Curve Optimizer."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_TARGET_INDOOR_TEMP,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
    DEFAULT_TARGET_INDOOR_TEMP,
)

_LOGGER = logging.getLogger(__name__)

# Entities are updated via their coordinator, never by per-entity I/O,
# so there is no reason to serialize updates against each other.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the primary zone's comfort setpoint entities.

    Values live in ``entry.options`` (written by the entities themselves);
    until first changed, the primary zone subentry's configured values
    apply. No primary zone -> nothing to control, no entities.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    heat_coordinator = runtime_data.heat_coordinator if runtime_data else None
    if runtime_data is None or heat_coordinator is None:
        return

    config = heat_coordinator.effective_config()

    def _initial(key: str, default: float) -> float:
        return float(config.get(key, default))

    async_add_entities(
        [
            TargetIndoorTemperatureNumber(
                hass=hass,
                entry=entry,
                unique_id=f"{entry.entry_id}_target_indoor_temp",
                device=runtime_data.device,
                initial_value=_initial(
                    CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP
                ),
            ),
            IndoorTempHysteresisLowerNumber(
                hass=hass,
                entry=entry,
                unique_id=f"{entry.entry_id}_indoor_temp_hysteresis_lower",
                device=runtime_data.device,
                initial_value=_initial(
                    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
                    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
                ),
            ),
            IndoorTempHysteresisUpperNumber(
                hass=hass,
                entry=entry,
                unique_id=f"{entry.entry_id}_indoor_temp_hysteresis_upper",
                device=runtime_data.device,
                initial_value=_initial(
                    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
                    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
                ),
            ),
        ]
    )


class BaseTemperatureNumber(NumberEntity):
    """Base class for temperature-related number entities.

    Persists its value to ``entry.options`` (keyed by ``_conf_key``) so it
    survives restarts. __init__.py's update listener recognises these keys:
    instead of reloading, it re-runs the primary zone's heat calculation and
    optimization so the new comfort band takes effect immediately.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    # Subclasses must define these
    _conf_key: str = ""
    _log_name: str = ""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        unique_id: str,
        device: DeviceInfo,
        initial_value: float,
    ) -> None:
        """Initialize the number entity."""
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = unique_id
        self._attr_device_info = device
        self._attr_native_value = initial_value

    @property
    def native_value(self) -> float:
        """Return value from entry.options, falling back to the initial value."""
        val = self._entry.options.get(self._conf_key)
        if val is not None:
            return float(val)
        return float(self._attr_native_value or 0.0)

    async def async_set_native_value(self, value: float) -> None:
        """Persist the new value to entry.options."""
        self._attr_native_value = value
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, self._conf_key: value},
        )
        self.async_write_ha_state()
        _LOGGER.debug("%s set to %.1f°C", self._log_name, value)


class TargetIndoorTemperatureNumber(BaseTemperatureNumber):
    """Number entity for target indoor temperature setpoint."""

    _attr_native_min_value = 15.0
    _attr_native_max_value = 25.0
    _attr_native_step = 0.5
    _attr_translation_key = "target_indoor_temperature"

    _conf_key = CONF_TARGET_INDOOR_TEMP
    _log_name = "Target indoor temperature"


class IndoorTempHysteresisLowerNumber(BaseTemperatureNumber):
    """Number entity for lower hysteresis (how far below target before heat pump ON)."""

    _attr_native_min_value = 0.1
    _attr_native_max_value = 2.0
    _attr_native_step = 0.1
    _attr_translation_key = "indoor_temp_hysteresis_lower"

    _conf_key = CONF_INDOOR_TEMP_HYSTERESIS_LOWER
    _log_name = "Lower hysteresis (heat pump ON)"


class IndoorTempHysteresisUpperNumber(BaseTemperatureNumber):
    """Number entity for upper hysteresis (how far above target before heat pump OFF)."""

    _attr_native_min_value = 0.1
    _attr_native_max_value = 2.0
    _attr_native_step = 0.1
    _attr_translation_key = "indoor_temp_hysteresis_upper"

    _conf_key = CONF_INDOOR_TEMP_HYSTERESIS_UPPER
    _log_name = "Upper hysteresis (heat pump OFF)"
