from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


class HeatingCurveOffsetNumber(NumberEntity, RestoreEntity):
    """Number entity to represent the heating curve offset."""

    _attr_has_entity_name = True
    _attr_name = "Heating Curve Offset"
    _attr_translation_key = "heating_curve_offset"
    _attr_native_unit_of_measurement = "°C"
    _attr_native_min_value = -4.0
    _attr_native_max_value = 4.0
    _attr_native_step = 1.0
    _attr_icon = "mdi:chart-line"

    def __init__(self, unique_id: str, device: DeviceInfo) -> None:
        self._attr_unique_id = unique_id
        self._attr_native_value = 0.0
        self._attr_device_info = device
        self._attr_available = True

    async def async_added_to_hass(self) -> None:  # pragma: no cover - simple restore
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                self._attr_native_value = 0.0

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = float(value)
        self.async_write_ha_state()


class HeatCurveMinNumber(NumberEntity, RestoreEntity):
    """Number entity to represent the minimum heating curve temperature."""

    _attr_has_entity_name = True
    _attr_name = "Heating Curve Min"
    _attr_translation_key = "heating_curve_min"
    _attr_native_unit_of_measurement = "°C"
    _attr_native_step = 1.0
    _attr_icon = "mdi:thermometer-low"

    def __init__(
        self,
        unique_id: str,
        device: DeviceInfo,
        *,
        native_min: float,
        native_max: float,
    ) -> None:
        self._attr_unique_id = unique_id
        self._attr_device_info = device
        self._attr_native_min_value = native_min
        self._attr_native_max_value = native_max
        self._attr_native_value = native_min
        self._attr_available = True

    async def async_added_to_hass(self) -> None:  # pragma: no cover - simple restore
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                self._attr_native_value = self._attr_native_min_value
        self.hass.data.setdefault(DOMAIN, {})["heat_curve_min"] = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = float(value)
        self.hass.data.setdefault(DOMAIN, {})["heat_curve_min"] = self._attr_native_value
        self.async_write_ha_state()


class HeatCurveMaxNumber(NumberEntity, RestoreEntity):
    """Number entity to represent the maximum heating curve temperature."""

    _attr_has_entity_name = True
    _attr_name = "Heating Curve Max"
    _attr_translation_key = "heating_curve_max"
    _attr_native_unit_of_measurement = "°C"
    _attr_native_step = 1.0
    _attr_icon = "mdi:thermometer-high"

    def __init__(
        self,
        unique_id: str,
        device: DeviceInfo,
        *,
        native_min: float,
        native_max: float,
    ) -> None:
        self._attr_unique_id = unique_id
        self._attr_device_info = device
        self._attr_native_min_value = native_min
        self._attr_native_max_value = native_max
        self._attr_native_value = native_max
        self._attr_available = True

    async def async_added_to_hass(self) -> None:  # pragma: no cover - simple restore
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                self._attr_native_value = self._attr_native_max_value
        self.hass.data.setdefault(DOMAIN, {})["heat_curve_max"] = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = float(value)
        self.hass.data.setdefault(DOMAIN, {})["heat_curve_max"] = self._attr_native_value
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up heating curve number entities."""
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heating Curve Optimizer",
    )

    offset = HeatingCurveOffsetNumber(
        unique_id=f"{entry.entry_id}_heating_curve_offset", device=device_info
    )
    curve_min = HeatCurveMinNumber(
        unique_id=f"{entry.entry_id}_heating_curve_min",
        device=device_info,
        native_min=20.0,
        native_max=45.0,
    )
    curve_max = HeatCurveMaxNumber(
        unique_id=f"{entry.entry_id}_heating_curve_max",
        device=device_info,
        native_min=35.0,
        native_max=60.0,
    )

    async_add_entities([offset, curve_min, curve_max])

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("entities", {})[offset.entity_id] = offset
    hass.data[DOMAIN]["heat_curve_min"] = curve_min.native_value
    hass.data[DOMAIN]["heat_curve_max"] = curve_max.native_value


__all__ = [
    "HeatingCurveOffsetNumber",
    "HeatCurveMinNumber",
    "HeatCurveMaxNumber",
]
