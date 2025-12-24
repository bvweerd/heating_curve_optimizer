"""Heating curve offset sensor using optimization coordinator."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...entity import BaseUtilitySensor


class CoordinatorHeatingCurveOffsetSensor(CoordinatorEntity, BaseUtilitySensor):
    """Heating curve offset sensor using optimization coordinator."""

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
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return optimized offset."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("optimized_offset")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return optimization results."""
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data
        return {
            "optimized_offsets": data.get("optimized_offsets", []),
            "buffer_evolution": data.get("buffer_evolution", []),
            "future_supply_temperatures": data.get("future_supply_temperatures", []),
            "total_cost": data.get("total_cost", 0.0),
            "baseline_cost": data.get("baseline_cost", 0.0),
            "cost_savings": data.get("cost_savings", 0.0),
            "forecast_time_base": 60,
            "prices": data.get("prices", []),
            "demand_forecast": data.get("demand_forecast", []),
            "baseline_cop": data.get("baseline_cop", []),
            "optimized_cop": data.get("optimized_cop", []),
            "baseline_supply_temperatures": data.get(
                "baseline_supply_temperatures", []
            ),
            "outdoor_forecast": data.get("outdoor_forecast", []),
        }
