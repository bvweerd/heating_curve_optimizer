"""Thermal calibration diagnostic sensor (fase 4, docs/redesign/REDESIGN.md).

Reports what calibration.py has learned about this specific building's UA
and thermal mass from real operation - sample count, whether it is trusted
enough to override the label-based prior yet, and the learned values
themselves. Read-only; resetting is a service
(`heating_curve_optimizer.reset_thermal_calibration`), not an entity
action, the same split battery_controller uses for its efficiency
calibrations.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .calibration import MIN_SAMPLES_TO_APPLY
from .entity import BaseUtilitySensor


class ThermalCalibrationSensor(CoordinatorEntity, BaseUtilitySensor):
    """Sample count and status of the thermal calibration fit."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="samples",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_should_poll = False
        self._attr_entity_registry_enabled_default = False  # opt-in diagnostic

    def _thermal_v2(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("thermal_v2", {})

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self._thermal_v2().get("available", False)
        )

    @property
    def native_value(self):
        """Return the number of samples behind the current fit."""
        return self._thermal_v2().get("calibration_sample_count", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the learned values, whether they're applied, and the prior.

        `building_ua_w_per_k`/`building_thermal_mass_kwh_per_k` in the
        thermal_v2 payload are whatever the last optimization run actually
        used - the label-based prior until `applied` turns true, the
        learned fit from then on. Only surfaced as "learned_*" once they
        really are the learned values, so this never claims a fit that
        hasn't happened yet.
        """
        data = self._thermal_v2()
        applied = data.get("calibration_applied", False)
        return {
            "applied": applied,
            "sample_count": data.get("calibration_sample_count", 0),
            "min_samples_to_apply": MIN_SAMPLES_TO_APPLY,
            "learned_ua_w_per_k": (
                data.get("building_ua_w_per_k") if applied else None
            ),
            "learned_thermal_mass_kwh_per_k": (
                data.get("building_thermal_mass_kwh_per_k") if applied else None
            ),
        }
