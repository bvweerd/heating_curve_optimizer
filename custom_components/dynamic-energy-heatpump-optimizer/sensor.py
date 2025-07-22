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
        label = data.get(CONF_HEAT_LOSS_LABEL, "A/B")
        area = float(data.get(CONF_FLOOR_AREA, 0.0))
        factor = HEAT_LOSS_FACTORS.get(label, 1.5)
        self.heat_loss = factor * area / 1000.0
        self.horizon = int(data.get("planning_horizon", 12))

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

        # Simple linear COP approximation
        cop = max(1.0, 6.0 - 0.08 * (supply_temp - outdoor_temp))
        demand = max(0.0, self.heat_loss * (20.0 - outdoor_temp))

        costs = []
        for p, s in zip(prices, solar):
            net = (demand / cop - s) * p
            costs.append(net)

        best_hour = min(range(len(costs)), key=costs.__getitem__)
        shift = best_hour - len(costs) // 2
        shift = max(-5, min(5, shift))

        self._attr_native_value = float(shift)
