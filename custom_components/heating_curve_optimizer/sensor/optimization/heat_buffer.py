"""Heat buffer sensor using optimization coordinator."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...entity import BaseUtilitySensor


class CoordinatorHeatBufferSensor(CoordinatorEntity, BaseUtilitySensor):
    """Heat buffer sensor using optimization coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kWh",
            device_class="energy",
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        # For energy device_class, state_class must be 'total' not 'measurement'
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return current buffer level."""
        if not self.coordinator.data:
            return None
        buffer_evolution = self.coordinator.data.get("buffer_evolution", [])
        return buffer_evolution[0] if buffer_evolution else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return buffer evolution forecast."""
        if not self.coordinator.data:
            return {}
        return {
            "forecast": self.coordinator.data.get("buffer_evolution", []),
            "forecast_time_base": 60,
        }
