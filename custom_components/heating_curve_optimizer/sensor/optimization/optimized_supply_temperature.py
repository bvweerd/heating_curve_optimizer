"""Optimized supply temperature sensor using optimization coordinator."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...entity import BaseUtilitySensor


class CoordinatorOptimizedSupplyTemperatureSensor(CoordinatorEntity, BaseUtilitySensor):
    """Optimized supply temperature sensor using optimization coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="°C",
            device_class="temperature",
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return optimized supply temperature."""
        if not self.coordinator.data:
            return None

        # Get future supply temperatures from coordinator
        future_temps = self.coordinator.data.get("future_supply_temperatures", [])
        if not future_temps:
            return None

        # Return first future supply temperature (current optimized temperature)
        return future_temps[0]

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "optimized_offsets": self.coordinator.data.get("optimized_offsets", []),
            "future_supply_temperatures": self.coordinator.data.get(
                "future_supply_temperatures", []
            ),
            "forecast_time_base": 60,
        }
