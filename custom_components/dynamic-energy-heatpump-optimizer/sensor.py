from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    CONF_PRICE_SENSOR,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_SUPPLY_TEMP_SENSOR,
    CONF_MAX_HEATPUMP_POWER,
    DEFAULT_MAX_HEATPUMP_POWER,
    COP_OUTDOOR_COEFFS,
    COP_SUPPLY_COEFFS,
    CONF_K_FACTOR,
    DEFAULT_K_FACTOR,
    DEFAULT_PLANNING_HORIZON,
)

import logging


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the heatpump optimizer sensor."""
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Dynamic Energy Heatpump Optimizer",
        entry_type=DeviceEntryType.SERVICE,
        manufacturer="DynamicEnergy",
        model="heatpump_optimizer",
    )

    optimizer = HeatpumpOptimizerSensor(hass, entry, device)
    entities = [optimizer]
    async_add_entities(entities)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["entities"] = {ent.entity_id: ent for ent in entities}


class HeatpumpOptimizerSensor(SensorEntity):
    """Sensor that exposes the optimized heating curve shift."""

    _attr_name = "Heatpump Optimizer"
    _attr_unique_id = "heatpump_optimizer"
    _attr_native_unit_of_measurement = "°C"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_value = 0.0

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_device_info = device
        data = entry.data
        # Required sensors
        self.price_entity = data.get(CONF_PRICE_SENSOR)
        self.outdoor_entity = data.get(CONF_OUTDOOR_TEMP_SENSOR)
        self.supply_entity = data.get(CONF_SUPPLY_TEMP_SENSOR)
        self.max_power = float(data.get(CONF_MAX_HEATPUMP_POWER, DEFAULT_MAX_HEATPUMP_POWER))
        self.horizon = DEFAULT_PLANNING_HORIZON
        self._forecast: list[float] = []

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        """Return additional attributes including the forecast."""
        return {"forecast": self._forecast}

    async def async_update(self) -> None:
        """Calculate the optimal heating curve shift."""

        _LOGGER.debug("Starting optimizer update")

        price_state = self.hass.states.get(self.price_entity)
        if not price_state or price_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Price sensor unavailable")
            return

        prices: list[float] = []
        price_attr = price_state.attributes

        if isinstance(price_attr.get("raw_today"), list):
            prices.extend([p.get("value", 0.0) for p in price_attr["raw_today"]])

        if isinstance(price_attr.get("raw_tomorrow"), list):
            prices.extend([p.get("value", 0.0) for p in price_attr["raw_tomorrow"]])

        if not prices and isinstance(price_attr.get("next_hours"), list):
            prices = price_attr["next_hours"]

        if not prices:
            try:
                prices = [float(price_state.state)] * self.horizon
            except (ValueError, TypeError):
                prices = [0.0] * self.horizon

        prices = prices[: self.horizon]
        _LOGGER.debug("Parsed prices for horizon: %s", prices)

        outdoor_state = self.hass.states.get(self.outdoor_entity)
        supply_state = self.hass.states.get(self.supply_entity)
        if not outdoor_state or outdoor_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Outdoor temperature unavailable")
            return
        if not supply_state or supply_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Supply temperature unavailable")
            return

        try:
            outdoor_temp = float(outdoor_state.state)
            supply_temp = float(supply_state.state)
        except ValueError:
            _LOGGER.debug("Invalid temperature value")
            return

        k_factor = float(self._entry.data.get(CONF_K_FACTOR, DEFAULT_K_FACTOR))

        def calc_cop(o_temp: float, s_temp: float) -> float:
            a_o, b_o, c_o = COP_OUTDOOR_COEFFS
            a_s, b_s, c_s = COP_SUPPLY_COEFFS
            cop_out = a_o + b_o * o_temp + c_o * o_temp**2
            cop_sup = a_s + b_s * s_temp + c_s * s_temp**2
            return max(1.0, (cop_out + cop_sup) / 2 * k_factor)

        base_cop = calc_cop(outdoor_temp, supply_temp)
        future_avg = sum(prices[1:]) / max(1, len(prices) - 1)

        best_shift = 0
        best_cost = prices[0] / base_cop + future_avg / base_cop
        for delta in range(1, 6):
            new_cop = calc_cop(outdoor_temp, supply_temp + delta)
            cost = prices[0] / new_cop + future_avg / base_cop
            if cost < best_cost:
                best_cost = cost
                best_shift = delta

        _LOGGER.debug("Calculated shift %s (cost %.3f)", best_shift, best_cost)

        self._forecast = prices
        self._attr_native_value = float(best_shift)
