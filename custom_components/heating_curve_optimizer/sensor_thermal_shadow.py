"""Shadow-mode diagnostic sensor for the redesigned thermal optimizer.

Phase 2 of docs/redesign/REDESIGN.md: exposes `OptimizationCoordinator.data
["thermal_v2"]` (produced by `_run_thermal_v2_optimization`, see
coordinator.py) purely as a diagnostic entity. It reports what the
redesigned optimizer (building_model.py / heatpump_model.py /
thermal_optimizer.py) *would* choose and what it estimates that would cost,
so the new model can be compared against the legacy optimizer's real
behaviour on real data before phase 3 lets it drive anything.

This file lives at the package root rather than under `sensor/`, following
the flat-module convention adopted for the redesign (see REDESIGN.md §3.3) -
new sensors from here on are plain modules like battery_controller's
`sensor.py`, rather than a new subfolder per category.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import BaseUtilitySensor


class ThermalShadowOffsetSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """What the redesigned optimizer would set the offset to right now.

    Diagnostic only - does not affect `sensor.heating_curve_offset` (the
    legacy optimizer, still in control until phase 3) or any number/runtime
    state.
    """

    _unrecorded_attributes = frozenset(
        {
            "offsets",
            "supply_temps",
            "indoor_temps",
            "thermal_power_kw",
            "electrical_power_kw",
            "cost_eur",
        }
    )

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

    def _thermal_v2(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("thermal_v2", {})

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Deliberately independent of the legacy optimizer's own success -
        this must be able to report "the shadow calculation itself failed"
        without that looking like a coordinator-wide outage.
        """
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self._thermal_v2().get("available", False)
        )

    @property
    def native_value(self):
        """Return the offset the redesigned optimizer would choose."""
        return self._thermal_v2().get("offset")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full planned schedule and building/heat pump parameters."""
        data = self._thermal_v2()
        if not data.get("available"):
            return {"error": data.get("error", "no data yet")}
        return {
            "offsets": data.get("offsets", []),
            "supply_temps": data.get("supply_temps", []),
            "indoor_temps": data.get("indoor_temps", []),
            "thermal_power_kw": data.get("thermal_power_kw", []),
            "electrical_power_kw": data.get("electrical_power_kw", []),
            "cost_eur": data.get("cost_eur", []),
            "building_ua_w_per_k": data.get("building_ua_w_per_k"),
            "building_thermal_mass_kwh_per_k": data.get(
                "building_thermal_mass_kwh_per_k"
            ),
            "building_time_constant_hours": data.get("building_time_constant_hours"),
            "emitter_nominal_power_kw": data.get("emitter_nominal_power_kw"),
            "heatpump_max_thermal_power_kw": data.get("heatpump_max_thermal_power_kw"),
        }


class ThermalShadowCostComparisonSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Forecast cost of the redesigned optimizer's plan vs. the legacy one.

    Positive means the redesigned optimizer expects to be cheaper over the
    same planning window - the headline number for judging phase 2 before
    phase 3 switches control over.
    """

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="EUR",
            device_class="monetary",
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = None  # monetary device_class forbids MEASUREMENT
        self._attr_should_poll = False
        self._attr_entity_registry_enabled_default = False

    def _thermal_v2(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("thermal_v2", {})

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._thermal_v2()
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and data.get("available", False)
            and data.get("legacy_total_cost_eur") is not None
        )

    @property
    def native_value(self):
        """Return estimated savings (legacy cost minus redesigned cost), EUR."""
        data = self._thermal_v2()
        legacy_cost = data.get("legacy_total_cost_eur")
        new_cost = data.get("total_cost_eur")
        if legacy_cost is None or new_cost is None:
            return None
        return round(legacy_cost - new_cost, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return both costs and the shadow price for context."""
        data = self._thermal_v2()
        return {
            "legacy_total_cost_eur": data.get("legacy_total_cost_eur"),
            "redesigned_total_cost_eur": data.get("total_cost_eur"),
            "shadow_price_eur_per_kwh": data.get("shadow_price_eur_per_kwh"),
        }
