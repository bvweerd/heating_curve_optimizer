"""Binary sensors for the Heating Curve Optimizer integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

# Entities are updated via their coordinator, never by per-entity I/O,
# so there is no reason to serialize updates against each other.
PARALLEL_UPDATES = 0


class CoordinatorHeatDemandBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that indicates heat demand using coordinator."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_has_entity_name = True
    _attr_translation_key = "heat_pump_demand"
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str, device: DeviceInfo) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_heat_pump_demand"
        self._attr_device_info = device

    @property
    def is_on(self) -> bool:
        """Return True if heat pump should be ON based on temperature hysteresis."""
        if not self.coordinator.data:
            return False
        return bool(self.coordinator.data.get("heat_pump_on", False))

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
            "indoor_temperature_source": data.get("indoor_temperature_source"),
            "target_temperature": data.get("target_temperature"),
        }
        # Add hysteresis bounds if available
        if "lower_bound" in data:
            attrs["lower_bound"] = data["lower_bound"]
        if "upper_bound" in data:
            attrs["upper_bound"] = data["upper_bound"]
        return attrs


class GasBoilerPreferredBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Recommendation to heat with the gas boiler instead of the heat pump.

    On only when comfort is at risk (the heat pump cannot keep the house in
    its comfort band) and gas is cheaper per kWh of heat - see
    gas_boiler_coordinator.py. Advisory: an automation decides what to do.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "gas_boiler_preferred"
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str, device: DeviceInfo) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_gas_boiler_preferred"
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
            "comfort_at_risk": data.get("comfort_at_risk"),
            "comfort_reason": data.get("comfort_reason"),
            "gas_cheaper": data.get("gas_cheaper"),
            "lowest_planned_indoor_temp": data.get("lowest_planned_indoor_temp"),
            "heat_pump_cost_eur_per_kwh": data.get("heat_pump_cost_eur_per_kwh"),
            "gas_cost_eur_per_kwh": data.get("gas_cost_eur_per_kwh"),
            "savings_eur_per_kwh": data.get("savings_eur_per_kwh"),
            "savings_pct": data.get("savings_pct"),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors for a config entry."""

    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None or runtime_data.heat_coordinator is None:
        return

    async_add_entities(
        [
            CoordinatorHeatDemandBinarySensor(
                runtime_data.heat_coordinator, entry.entry_id, runtime_data.device
            )
        ]
    )

    for subentry_id, zone_data in runtime_data.zones.items():
        async_add_entities(
            [
                CoordinatorHeatDemandBinarySensor(
                    zone_data["heat_coordinator"],
                    f"{entry.entry_id}_{subentry_id}",
                    zone_data["device"],
                )
            ],
            config_subentry_id=subentry_id,
        )

    if runtime_data.gas_boiler_coordinator is not None:
        async_add_entities(
            [
                GasBoilerPreferredBinarySensor(
                    runtime_data.gas_boiler_coordinator,
                    entry.entry_id,
                    runtime_data.gas_boiler_device,
                )
            ],
            config_subentry_id=runtime_data.gas_boiler_subentry_id,
        )
