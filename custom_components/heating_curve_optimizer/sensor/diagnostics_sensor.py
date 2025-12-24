"""Diagnostics sensor with all coordinator data."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity import BaseUtilitySensor


class CoordinatorDiagnosticsSensor(CoordinatorEntity, BaseUtilitySensor):
    """Diagnostics sensor with all coordinator data."""

    def __init__(
        self,
        weather_coordinator,
        heat_coordinator,
        optimization_coordinator,
        name: str,
        unique_id: str,
        device: DeviceInfo,
    ):
        """Initialize the diagnostics sensor."""
        # Use weather coordinator as primary
        CoordinatorEntity.__init__(self, weather_coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit=None,  # No unit for string sensor
            device_class=None,
            icon="mdi:information-outline",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_should_poll = False
        # Diagnostics sensor returns string, so no state_class or unit
        self._attr_state_class = None
        self._attr_native_unit_of_measurement = None
        self.weather_coordinator = weather_coordinator
        self.heat_coordinator = heat_coordinator
        self.optimization_coordinator = optimization_coordinator

    @property
    def native_value(self):
        """Return OK if all coordinators are working."""
        if (
            self.weather_coordinator.last_update_success
            and self.heat_coordinator.last_update_success
            and self.optimization_coordinator.last_update_success
        ):
            return "OK"
        return "ERROR"

    @property
    def available(self) -> bool:
        """Always available to show diagnostics."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all coordinator data as diagnostics."""
        attrs = {}

        # Weather coordinator data
        if self.weather_coordinator.data:
            attrs["weather_last_update"] = str(
                self.weather_coordinator.data.get("timestamp")
            )
            attrs["outdoor_temperature"] = self.weather_coordinator.data.get(
                "current_temperature"
            )
            attrs["weather_success"] = self.weather_coordinator.last_update_success

        # Heat coordinator data
        if self.heat_coordinator.data:
            attrs["heat_last_update"] = str(self.heat_coordinator.data.get("timestamp"))
            attrs["heat_loss"] = self.heat_coordinator.data.get("heat_loss")
            attrs["solar_gain"] = self.heat_coordinator.data.get("solar_gain")
            attrs["net_heat_loss"] = self.heat_coordinator.data.get("net_heat_loss")
            attrs["heat_success"] = self.heat_coordinator.last_update_success

        # Optimization coordinator data
        if self.optimization_coordinator.data:
            attrs["optimization_last_update"] = str(
                self.optimization_coordinator.data.get("timestamp")
            )
            attrs["optimized_offset"] = self.optimization_coordinator.data.get(
                "optimized_offset"
            )
            attrs["total_cost"] = self.optimization_coordinator.data.get("total_cost")
            attrs["optimization_success"] = (
                self.optimization_coordinator.last_update_success
            )

        return attrs
