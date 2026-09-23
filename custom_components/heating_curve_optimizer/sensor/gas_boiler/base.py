"""Base class for hybrid gas-boiler cost comparison sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...entity import BaseUtilitySensor


class BaseGasBoilerSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Base class for sensors reading from GasBoilerCoordinator.

    Unlike BaseOptimizationSensor, additionally gates `available` on the
    coordinator data's own `available` flag - a soft, self-healing
    UpdateFailed (e.g. the other coordinators haven't refreshed yet) still
    leaves `coordinator.data` as the last-good dict, but that dict's
    `available` is only True when this cycle's comparison is genuinely
    fresh.
    """

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
        *,
        unit: str = "€/kWh",
        device_class: str | None = None,
        state_class: SensorStateClass = SensorStateClass.MEASUREMENT,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit=unit,
            device_class=device_class,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = state_class
        self._attr_should_poll = False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and bool(self.coordinator.data.get("available", False))
        )
