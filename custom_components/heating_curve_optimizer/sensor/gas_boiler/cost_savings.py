"""Cost-savings sensor for the hybrid gas-boiler comparison."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo

from .base import BaseGasBoilerSensor


class GasBoilerCostSavingsSensor(BaseGasBoilerSensor):
    """Signed savings per kWh thermal: positive means gas is cheaper."""

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, unique_id, icon, device)

    @property
    def native_value(self) -> float | None:
        """Return heat-pump-cost minus gas-cost, per kWh thermal."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("savings_eur_per_kwh")
        return round(float(value), 5) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the percentage form and the underlying recommendation."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        return {
            "savings_pct": data.get("savings_pct"),
            "prefer_gas_boiler": data.get("prefer_gas_boiler"),
            "heat_currently_needed": data.get("heat_currently_needed"),
        }
