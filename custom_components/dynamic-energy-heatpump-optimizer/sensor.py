from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the heatpump optimizer sensor."""
    async_add_entities([HeatpumpOptimizerSensor(hass, entry)])


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
        self.room_entity = data.get(
            CONF_ROOM_TEMP_SENSOR, "sensor.indoor_temperature"
        )
        self.max_power = float(data.get(CONF_MAX_HEATPUMP_POWER, 5.0))
        label = data.get(CONF_HEAT_LOSS_LABEL, "A/B")
        area = float(data.get(CONF_FLOOR_AREA, 0.0))
        factor = HEAT_LOSS_FACTORS.get(label, 1.5)
        self.heat_loss = factor * area / 1000.0
        self.horizon = int(data.get("planning_horizon", 12))
        self._forecast: list[float] = []

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

        # Gather input values from Home Assistant
        price_state = self.hass.states.get(self.price_entity)
        solar_states = [self.hass.states.get(ent) for ent in self.solar_entities]
        outdoor_state = self.hass.states.get(self.outdoor_entity)
        supply_state = self.hass.states.get(self.supply_entity)

        if not price_state or price_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Price sensor unavailable")
            return

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

        # Simple linear COP approximation
        cop = max(1.0, 6.0 - 0.08 * (supply_temp - outdoor_temp))
        demand_kw = max(0.0, self.heat_loss * (room_temp - outdoor_temp))
        total_energy = demand_kw * self.horizon / cop
        net_energy = max(0.0, total_energy - sum(solar[: self.horizon]))

        allocation = [0.0] * self.horizon
        remaining = net_energy
        for idx in sorted(range(self.horizon), key=prices.__getitem__):
            if remaining <= 0:
                break
            alloc = min(self.max_power, remaining)
            allocation[idx] = alloc
            remaining -= alloc

        costs = [allocation[i] * prices[i] for i in range(self.horizon)]

        if net_energy > 0:
            avg_idx = sum(i * allocation[i] for i in range(self.horizon)) / net_energy
        else:
            avg_idx = self.horizon / 2
        shift = int(round(avg_idx - self.horizon / 2))
        shift = max(-5, min(5, shift))

        self._forecast = costs
        self._attr_native_value = float(shift)
