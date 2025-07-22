from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from typing import Any

from .const import (
    HEAT_LOSS_FACTORS,
    CONF_ENERGY_SENSORS,
    CONF_SOLAR_SENSORS,
    CONF_HEAT_LOSS_LABEL,
    CONF_FLOOR_AREA,
    CONF_ROOM_TEMP_SENSOR,
    CONF_MAX_HEATPUMP_POWER,
)

import logging


_LOGGER = logging.getLogger(__name__)


class _DiagnosticSensor(SensorEntity):
    """Internal sensor for exposing diagnostic values."""

    def __init__(
        self,
        name: str,
        unique_id: str,
        unit: str,
        device_class: SensorDeviceClass | str | None = None,
    ) -> None:
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = 0.0
        self._attributes: dict[str, Any] = {}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attributes

    def update_value(self, value: float, attrs: dict[str, Any] | None = None) -> None:
        self._attr_native_value = round(float(value), 3)
        if attrs is not None:
            self._attributes = attrs
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the heatpump optimizer sensor."""
    optimizer = HeatpumpOptimizerSensor(hass, entry)
    entities = [optimizer] + optimizer.diagnostic_sensors
    async_add_entities(entities)


class HeatpumpOptimizerSensor(SensorEntity):
    """Sensor that exposes the optimized heating curve shift."""

    _attr_name = "Heatpump Optimizer"
    _attr_unique_id = "heatpump_optimizer"
    _attr_native_value = 0.0

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        data = entry.data
        # Default entities if none are provided in the config entry
        self.price_entity = data.get("price_sensor", "sensor.energy_price")
        self.energy_entities = data.get(CONF_ENERGY_SENSORS, [])
        self.solar_entities = data.get(CONF_SOLAR_SENSORS, [])
        self.outdoor_entity = data.get("outdoor_temp", "sensor.outdoor_temperature")
        self.supply_entity = data.get(
            "supply_temp", "sensor.heatpump_supply_temperature"
        )
        self.room_entity = data.get(CONF_ROOM_TEMP_SENSOR, "sensor.indoor_temperature")
        self.max_power = float(data.get(CONF_MAX_HEATPUMP_POWER, 5.0))
        label = data.get(CONF_HEAT_LOSS_LABEL, "A/B")
        area = float(data.get(CONF_FLOOR_AREA, 0.0))
        factor = HEAT_LOSS_FACTORS.get(label, 1.5)
        self.heat_loss = factor * area / 1000.0
        self.horizon = int(data.get("planning_horizon", 12))
        self._forecast: list[float] = []
        self._demand: list[float] = []
        # Diagnostic sensors
        self.heat_loss_sensor = _DiagnosticSensor(
            "Heatpump Heat Loss",
            "heatpump_heat_loss",
            "kW/°C",
        )
        self.demand_sensor = _DiagnosticSensor(
            "Heatpump Heat Demand",
            "heatpump_heat_demand",
            "kW",
        )
        self.net_energy_sensor = _DiagnosticSensor(
            "Heatpump Net Energy",
            "heatpump_net_energy",
            "kWh",
            SensorDeviceClass.ENERGY,
        )
        self.diagnostic_sensors = [
            self.heat_loss_sensor,
            self.demand_sensor,
            self.net_energy_sensor,
        ]
        self.heat_loss_sensor._attr_native_value = round(self.heat_loss, 3)

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        """Return additional attributes including the forecast."""
        return {"forecast": self._forecast}

    async def async_update(self) -> None:
        """Calculate the optimal heating curve shift.

        A full MIP approach would be overkill for this simple use case, so a
        lightweight heuristic is applied instead. It looks at the next
        ``planning_horizon`` hours and shifts heating towards the cheapest hour
        considering solar production, COP and the house heat loss.
        """

        _LOGGER.debug("Starting optimizer update")

        # Gather input values from Home Assistant
        price_state = self.hass.states.get(self.price_entity)
        solar_states = [self.hass.states.get(ent) for ent in self.solar_entities]
        outdoor_state = self.hass.states.get(self.outdoor_entity)
        supply_state = self.hass.states.get(self.supply_entity)

        if not price_state or price_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Price sensor unavailable")
            return

        _LOGGER.debug("Price attributes: %s", price_state.attributes)

        prices: list[float] = []
        price_attr = price_state.attributes

        if isinstance(price_attr.get("raw_today"), list):
            today_values = [p.get("value", 0.0) for p in price_attr["raw_today"]]
            prices.extend(today_values)

        if isinstance(price_attr.get("raw_tomorrow"), list):
            tomorrow_values = [p.get("value", 0.0) for p in price_attr["raw_tomorrow"]]
            prices.extend(tomorrow_values)

        if not prices and isinstance(price_attr.get("next_hours"), list):
            prices = price_attr["next_hours"]

        if not prices:
            try:
                prices = [float(price_state.state)] * self.horizon
            except (ValueError, TypeError):
                prices = [0.0] * self.horizon

        prices = prices[: self.horizon]
        _LOGGER.debug("Parsed prices for horizon: %s", prices)

        solar: list[float] = [0.0] * self.horizon
        for state in solar_states:
            if not state:
                continue
            attr = state.attributes
            forecast = (
                attr.get("forecast")
                or attr.get("forecasts")
                or attr.get("detailedForecast")
            )
            if isinstance(forecast, list):
                values = []
                for entry in forecast:
                    val = (
                        entry.get("pv_estimate")
                        or entry.get("pv_estimate_kw")
                        or entry.get("energy")
                    )
                    try:
                        values.append(float(val))
                    except (TypeError, ValueError):
                        values.append(0.0)

                for idx, val in enumerate(values[: self.horizon]):
                    solar[idx] += val

        _LOGGER.debug("Combined solar forecast: %s", solar)

        try:
            outdoor_temp = float(outdoor_state.state) if outdoor_state else 10.0
        except (ValueError, TypeError):
            outdoor_temp = 10.0

        try:
            supply_temp = float(supply_state.state) if supply_state else 35.0
        except (ValueError, TypeError):
            supply_temp = 35.0

        room_state = self.hass.states.get(self.room_entity)
        try:
            room_temp = float(room_state.state) if room_state else 20.0
        except (ValueError, TypeError):
            room_temp = 20.0

        _LOGGER.debug(
            "Temps - outdoor: %.2f, supply: %.2f, room: %.2f",
            outdoor_temp,
            supply_temp,
            room_temp,
        )

        # Simple linear COP approximation
        cop = max(1.0, 6.0 - 0.08 * (supply_temp - outdoor_temp))
        demand_kw = max(0.0, self.heat_loss * (room_temp - outdoor_temp))
        total_energy = demand_kw * self.horizon / cop
        net_energy = max(0.0, total_energy - sum(solar[: self.horizon]))

        _LOGGER.debug(
            "cop=%.2f demand_kw=%.2f total_energy=%.2f net_energy=%.2f",
            cop,
            demand_kw,
            total_energy,
            net_energy,
        )

        allocation = [0.0] * self.horizon
        remaining = net_energy
        for idx in sorted(range(self.horizon), key=prices.__getitem__):
            if remaining <= 0:
                break
            alloc = min(self.max_power, remaining)
            allocation[idx] = alloc
            remaining -= alloc

        _LOGGER.debug("Allocation per hour: %s", allocation)

        costs = [allocation[i] * prices[i] for i in range(self.horizon)]

        if net_energy > 0:
            avg_idx = sum(i * allocation[i] for i in range(self.horizon)) / net_energy
        else:
            avg_idx = self.horizon / 2
        shift = int(round(avg_idx - self.horizon / 2))
        shift = max(-5, min(5, shift))

        _LOGGER.debug("Costs forecast: %s", costs)
        _LOGGER.debug("Calculated shift: %s", shift)

        self._forecast = costs
        self._demand = allocation
        self._attr_native_value = float(shift)
        self.demand_sensor.update_value(demand_kw, {"demand": self._demand})
        self.net_energy_sensor.update_value(net_energy)
