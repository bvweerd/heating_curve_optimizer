"""Heat pump cost-per-kWh sensor for the hybrid gas-boiler comparison."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo

from .base import BaseGasBoilerSensor


class GasBoilerHeatPumpCostSensor(BaseGasBoilerSensor):
    """Current heat-pump cost per kWh of heat delivered.

    unit="€/kWh" with device_class=None matches CurrentElectricityPriceSensor's
    precedent - HA's MONETARY device class is for absolute amounts, not a
    per-kWh rate.
    """

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
        """Return the heat pump's cost per kWh thermal."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("heat_pump_cost_eur_per_kwh")
        return round(float(value), 5) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return supporting values behind the cost figure."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        return {
            "electricity_price_eur_per_kwh": data.get("electricity_price_eur_per_kwh"),
            "heat_pump_cop": data.get("heat_pump_cop"),
            "supply_temp": data.get("supply_temp"),
            "outdoor_temp": data.get("outdoor_temp"),
        }
