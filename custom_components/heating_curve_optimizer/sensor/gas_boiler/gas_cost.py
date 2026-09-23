"""Gas boiler cost-per-kWh sensor for the hybrid gas-boiler comparison."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo

from .base import BaseGasBoilerSensor


class GasBoilerGasCostSensor(BaseGasBoilerSensor):
    """Current gas-boiler cost per kWh of heat delivered."""

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
        """Return the gas boiler's cost per kWh thermal."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("gas_cost_eur_per_kwh")
        return round(float(value), 5) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return supporting values behind the cost figure."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        return {
            "gas_price_eur_per_m3": data.get("gas_price_eur_per_m3"),
            "efficiency": self.coordinator.config.get("gas_boiler_efficiency"),
            "calorific_value_kwh_per_m3": self.coordinator.config.get(
                "gas_calorific_value_kwh_per_m3"
            ),
        }
