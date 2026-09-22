"""Real-time PV-surplus offset adjustment sensor (phase 5b, REDESIGN.md).

Reports what `realtime_controller.py` is layering on top of the DP's
planned offset right now, when a grid sensor is configured and
control_mode is optimize_v2. Diagnostic/opt-in - disabled by default like
the other phase 2/4/5 additions, and entirely inert (native_value None)
whenever the real-time loop has not produced anything yet.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import BaseUtilitySensor


class RealtimeOffsetAdjustmentSensor(CoordinatorEntity, BaseUtilitySensor):
    """The real-time controller's current adjustment to the planned offset."""

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
        self._attr_entity_registry_enabled_default = False  # opt-in diagnostic

    def _realtime(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("realtime", {})

    @property
    def available(self) -> bool:
        """Return if entity is available.

        True whenever the real-time loop has produced at least one action
        this session - independent of the coordinator's own last_update_success,
        since a stale grid sensor reading is reported through the action's
        own fields, not through coordinator-wide unavailability.
        """
        return self.coordinator.data is not None and bool(self._realtime())

    @property
    def native_value(self):
        """Return the current offset adjustment, in whole degrees."""
        return self._realtime().get("adjustment")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the planned/effective offset and the inputs behind the adjustment."""
        data = self._realtime()
        if not data:
            return {}
        return {
            "planned_offset": data.get("planned_offset"),
            "effective_offset": data.get("effective_offset"),
            "current_grid_w": data.get("current_grid_w"),
            "shadow_price_eur_per_kwh": data.get("shadow_price_eur_per_kwh"),
        }
