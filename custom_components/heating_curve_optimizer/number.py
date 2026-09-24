"""Number platform for Heating Curve Optimizer."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_INDOOR_TEMP_HYSTERESIS,
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_TARGET_INDOOR_TEMP,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
    DEFAULT_TARGET_INDOOR_TEMP,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Entities are updated via their coordinator, never by per-entity I/O,
# so there is no reason to serialize updates against each other.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities from a config entry.

    Target temperature/hysteresis are zone-specific settings (every zone,
    including the first, is a subentry now - see __init__.py's
    _find_primary_zone_subentry) - they're read from the primary zone's
    own coordinator config (`heat_coordinator.config`, already merged with
    that subentry's data), not from `entry.data`/`.options` directly,
    which no longer carry them at all. No primary zone configured yet ->
    nothing to control, so no entities are created.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    heat_coordinator = runtime_data.heat_coordinator if runtime_data else None
    if heat_coordinator is None:
        _LOGGER.debug(
            "Skipping number entities for %s: no primary heating zone configured yet",
            entry.entry_id,
        )
        return

    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heating Curve Optimizer",
        manufacturer="Heating Curve Optimizer",
        model="Virtual",
    )

    config = heat_coordinator.config

    # Get initial values with fallback to legacy symmetric hysteresis.
    # Prefer entry.options (written live by the entities themselves on
    # previous runs) over the subentry config, so user adjustments survive
    # a reload without going through a full options flow.
    def _get(key: str, default: float) -> float:
        val = entry.options.get(key)
        if val is not None:
            return float(val)
        return float(config.get(key, default))

    # Determine initial hysteresis values.  Priority order:
    #   1. entry.options (persisted by the entity on a previous run)
    #   2. explicit lower/upper keys in the zone config (subentry data)
    #   3. legacy symmetric CONF_INDOOR_TEMP_HYSTERESIS value, if present
    #   4. built-in asymmetric defaults (lower=0.3 °C, upper=0.5 °C)
    _legacy_key_present = CONF_INDOOR_TEMP_HYSTERESIS in config
    legacy_hysteresis = config.get(CONF_INDOOR_TEMP_HYSTERESIS)

    def _hysteresis(key: str, asymmetric_default: float) -> float:
        """Return the best value for a hysteresis key."""
        val = entry.options.get(key)
        if val is not None:
            return float(val)
        explicit = config.get(key)
        if explicit is not None:
            return float(explicit)
        if _legacy_key_present and legacy_hysteresis is not None:
            return float(legacy_hysteresis)
        return asymmetric_default

    initial_lower = _hysteresis(
        CONF_INDOOR_TEMP_HYSTERESIS_LOWER, DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER
    )
    initial_upper = _hysteresis(
        CONF_INDOOR_TEMP_HYSTERESIS_UPPER, DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER
    )

    entities = [
        TargetIndoorTemperatureNumber(
            hass=hass,
            entry=entry,
            unique_id=f"{entry.entry_id}_target_indoor_temp",
            device=device,
            initial_value=_get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP),
        ),
        IndoorTempHysteresisLowerNumber(
            hass=hass,
            entry=entry,
            unique_id=f"{entry.entry_id}_indoor_temp_hysteresis_lower",
            device=device,
            initial_value=initial_lower,
        ),
        IndoorTempHysteresisUpperNumber(
            hass=hass,
            entry=entry,
            unique_id=f"{entry.entry_id}_indoor_temp_hysteresis_upper",
            device=device,
            initial_value=initial_upper,
        ),
    ]

    async_add_entities(entities)


class BaseTemperatureNumber(NumberEntity):  # type: ignore[misc]  # HA base class untyped
    """Base class for temperature-related number entities.

    Persists its value to ``entry.options`` (keyed by ``_conf_key``) so
    that it survives reloads without needing RestoreEntity / recorder.
    The _update_listener in __init__.py skips a full reload for these keys
    (see _NO_RELOAD_KEYS) so writing them doesn't cause a restart.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.SLIDER

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
        return float(self._attr_native_value)

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
    _attr_icon = "mdi:home-thermometer"
    _attr_translation_key = "target_indoor_temperature"

    _conf_key = CONF_TARGET_INDOOR_TEMP
    _log_name = "Target indoor temperature"


class IndoorTempHysteresisLowerNumber(BaseTemperatureNumber):
    """Number entity for lower hysteresis (how far below target before heat pump ON)."""

    _attr_native_min_value = 0.1
    _attr_native_max_value = 2.0
    _attr_native_step = 0.1
    _attr_icon = "mdi:thermometer-chevron-down"
    _attr_translation_key = "indoor_temp_hysteresis_lower"

    _conf_key = CONF_INDOOR_TEMP_HYSTERESIS_LOWER
    _log_name = "Lower hysteresis (heat pump ON)"


class IndoorTempHysteresisUpperNumber(BaseTemperatureNumber):
    """Number entity for upper hysteresis (how far above target before heat pump OFF)."""

    _attr_native_min_value = 0.1
    _attr_native_max_value = 2.0
    _attr_native_step = 0.1
    _attr_icon = "mdi:thermometer-chevron-up"
    _attr_translation_key = "indoor_temp_hysteresis_upper"

    _conf_key = CONF_INDOOR_TEMP_HYSTERESIS_UPPER
    _log_name = "Upper hysteresis (heat pump OFF)"
