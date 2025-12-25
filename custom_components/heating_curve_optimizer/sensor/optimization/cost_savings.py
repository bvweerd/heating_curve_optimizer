"""Cost savings sensor using optimization coordinator."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...entity import BaseUtilitySensor


class CoordinatorCostSavingsSensor(CoordinatorEntity, BaseUtilitySensor):
    """Cost savings forecast sensor showing predicted optimization savings in EUR."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="€",
            device_class=SensorDeviceClass.MONETARY,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return cost savings in EUR."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("cost_savings", 0.0)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return cost breakdown."""
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data
        return {
            "total_cost_eur": round(data.get("total_cost", 0.0), 2),
            "baseline_cost_eur": round(data.get("baseline_cost", 0.0), 2),
            "cost_savings_eur": round(data.get("cost_savings", 0.0), 2),
            "savings_percentage": (
                round(
                    100
                    * data.get("cost_savings", 0.0)
                    / data.get("baseline_cost", 1.0),
                    1,
                )
                if data.get("baseline_cost", 0.0) > 0
                else 0.0
            ),
            "planning_window_hours": len(data.get("optimized_offsets", [])),
        }
