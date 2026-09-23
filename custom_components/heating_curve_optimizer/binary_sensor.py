"""Binary sensors for the Heating Curve Optimizer integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Entities are updated via their coordinator, never by per-entity I/O,
# so there is no reason to serialize updates against each other.
PARALLEL_UPDATES = 0


class CoordinatorHeatDemandBinarySensor(CoordinatorEntity, BinarySensorEntity):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Binary sensor that indicates heat demand using coordinator."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str, device: DeviceInfo) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_heat_pump_demand"
        self._attr_translation_key = "heat_pump_demand"
        self._attr_has_entity_name = True
        self._attr_device_info = device
        self._attr_icon = "mdi:radiator"

    @property
    def is_on(self) -> bool:
        """Return True if heat pump should be ON based on temperature hysteresis."""
        if not self.coordinator.data:
            return False
        # Use heat_pump_on state which considers temperature hysteresis
        # Falls back to net_heat_loss > 0 for backward compatibility
        return bool(
            self.coordinator.data.get(
                "heat_pump_on", self.coordinator.data.get("net_heat_loss", 0.0) > 0.0
            )
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, float | bool]:
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        attrs = {
            "net_heat_kW": round(data.get("net_heat_loss", 0.0), 3),
            "heat_demand_factor": data.get("heat_demand_factor", 0.0),
            "indoor_temperature": data.get("indoor_temperature"),
            "target_temperature": data.get("target_temperature"),
        }
        # Add hysteresis bounds if available
        if "lower_bound" in data:
            attrs["lower_bound"] = data["lower_bound"]
        if "upper_bound" in data:
            attrs["upper_bound"] = data["upper_bound"]
        return attrs


class GasBoilerPreferredBinarySensor(CoordinatorEntity, BinarySensorEntity):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Whether the gas boiler is currently the cheaper heat source.

    `is_on` is already gated on whether heat is actually needed right now
    (see gas_boiler_coordinator.py's `prefer_gas_boiler` computation) -
    coasting on the thermal buffer costs nothing and always beats both
    paid sources, so this is never True just because gas happens to be
    cheaper in isolation. Advisory only, like every other entity in this
    integration - a user's own automation decides whether/how to act on it.
    """

    _attr_icon = "mdi:gas-burner"
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str, device: DeviceInfo) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_gas_boiler_preferred"
        self._attr_translation_key = "gas_boiler_preferred"
        self._attr_has_entity_name = True
        self._attr_device_info = device

    @property
    def is_on(self) -> bool:
        """Return True when the gas boiler is currently the cheaper choice."""
        if not self.coordinator.data:
            return False
        return bool(self.coordinator.data.get("prefer_gas_boiler", False))

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and bool(self.coordinator.data.get("available", False))
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full comparison, including why the recommendation is
        off (no heat needed vs. heat pump is cheaper) when it is."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        return {
            "heat_currently_needed": data.get("heat_currently_needed"),
            "heat_currently_needed_source": data.get("heat_currently_needed_source"),
            "heat_pump_cost_eur_per_kwh": data.get("heat_pump_cost_eur_per_kwh"),
            "gas_cost_eur_per_kwh": data.get("gas_cost_eur_per_kwh"),
            "savings_eur_per_kwh": data.get("savings_eur_per_kwh"),
            "savings_pct": data.get("savings_pct"),
        }


class HeatDemandBinarySensor(BinarySensorEntity):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
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

        state = self.hass.states.get(str(entity_id))
        if state is None or state.state in ("unknown", "unavailable"):
            self._attr_available = False
            self._extra_attrs = {"net_heat_entity_id": str(entity_id)}
            return

        try:
            net_heat = float(state.state)
        except (TypeError, ValueError):
            self._attr_available = False
            self._extra_attrs = {"net_heat_entity_id": str(entity_id)}
            return

        self._attr_available = True
        self._attr_is_on = net_heat > 0.0
        self._extra_attrs = {
            "net_heat_entity_id": str(entity_id),
            "net_heat_kW": round(net_heat, 3),
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the binary sensors for a config entry."""

    # Check if coordinators are available. heat_coordinator is None when no
    # primary heating-zone subentry is configured yet (see
    # __init__.py's _find_primary_zone_subentry) - a valid, if useless,
    # state that falls through to the legacy fallback below rather than
    # crashing on a raw entry.data lookup for fields that no longer live
    # there at all (every zone, including the first, is a subentry now).
    runtime_data = getattr(entry, "runtime_data", None)
    heat_coordinator = runtime_data.heat_coordinator if runtime_data else None
    device = runtime_data.device if runtime_data else None

    if heat_coordinator and device and runtime_data is not None:
        # Use coordinator-based binary sensor
        _LOGGER.info("Setting up coordinator-based heat demand binary sensor")
        async_add_entities(
            [
                CoordinatorHeatDemandBinarySensor(
                    heat_coordinator, entry.entry_id, device
                )
            ],
            True,
        )

        # Per-zone heat demand (phase 5c, REDESIGN.md): each zone's own
        # coordinator gets the same sensor, associated with its own
        # subentry/device via config_subentry_id (see __init__.py's zone
        # setup and battery_controller's per-battery/per-PV-array pattern).
        for subentry_id, zone_data in runtime_data.zones.items():
            zone_heat_coordinator = zone_data.get("heat_coordinator")
            zone_device = zone_data.get("device")
            if not zone_heat_coordinator or not zone_device:
                continue
            async_add_entities(
                [
                    CoordinatorHeatDemandBinarySensor(
                        zone_heat_coordinator,
                        f"{entry.entry_id}_{subentry_id}",
                        zone_device,
                    )
                ],
                True,
                config_subentry_id=subentry_id,
            )

        # Hybrid gas-boiler comparison (optional, singleton subentry - see
        # __init__.py's gas boiler setup). Absent for every installation
        # that hasn't configured it.
        gas_boiler_coordinator = runtime_data.gas_boiler_coordinator
        gas_boiler_device = runtime_data.gas_boiler_device
        gas_boiler_subentry_id = runtime_data.gas_boiler_subentry_id
        if gas_boiler_coordinator is not None and gas_boiler_device is not None:
            async_add_entities(
                [
                    GasBoilerPreferredBinarySensor(
                        gas_boiler_coordinator, entry.entry_id, gas_boiler_device
                    )
                ],
                True,
                config_subentry_id=gas_boiler_subentry_id,
            )
    else:
        # Fallback to legacy
        _LOGGER.warning(
            "Heat coordinator not available, using legacy heat demand sensor"
        )
        device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Heating Curve Optimizer",
        )
        async_add_entities(
            [HeatDemandBinarySensor(hass, entry.entry_id, device_info)], True
        )
