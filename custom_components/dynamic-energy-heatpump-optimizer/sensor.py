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
    CONF_CURRENT_POWER_SENSOR,
    CONF_MAX_HEATPUMP_POWER,
    CONF_PLANNING_HORIZON,
    DEFAULT_PLANNING_HORIZON,
    COP_OUTDOOR_COEFFS,
    COP_SUPPLY_COEFFS,
    CONF_K_FACTOR,
    DEFAULT_K_FACTOR,
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
        self.power_entity = data.get(CONF_CURRENT_POWER_SENSOR)
        self.max_power = float(data.get(CONF_MAX_HEATPUMP_POWER, 5.0))
        label = data.get(CONF_HEAT_LOSS_LABEL, "A/B")
        self.floor_area = float(data.get(CONF_FLOOR_AREA, 0.0))
        self.loss_factor = HEAT_LOSS_FACTORS.get(label, 1.5)
        self.heat_loss_per_degree = self.loss_factor * self.floor_area / 1000.0
        self.horizon = int(data.get(CONF_PLANNING_HORIZON, DEFAULT_PLANNING_HORIZON))
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
        self.heat_loss_sensor._attr_native_value = round(self.heat_loss_per_degree, 3)

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        """Return additional attributes including the forecast."""
        return {"forecast": self._forecast}

    async def async_update(self) -> None:
        """Calculate the optimal heating curve shift.

        The house itself acts as a heat buffer. The stooklijn keeps the
        temperature stable, so the optimizer only needs to determine when to run
        the heatpump within the ``planning_horizon`` to minimise costs. The
        sensor therefore returns the number of hours the stooklijn should be
        advanced or delayed based solely on the electricity price forecast.
        """

        _LOGGER.debug("Starting optimizer update")

        price_state = self.hass.states.get(self.price_entity)
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
        _LOGGER.debug("Parsed prices for horizon: %s", prices)

        outdoor_state = self.hass.states.get(self.outdoor_entity)
        supply_state = self.hass.states.get(self.supply_entity)
        room_state = self.hass.states.get(self.room_entity)
        if not outdoor_state or outdoor_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Outdoor temperature unavailable")
            return
        if not supply_state or supply_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Supply temperature unavailable")
            return
        if not room_state or room_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Room temperature unavailable")
            return

        try:
            outdoor_temp = float(outdoor_state.state)
            supply_temp = float(supply_state.state)
            room_temp = float(room_state.state)
        except ValueError:
            _LOGGER.debug("Invalid temperature value")
            return

        delta_t = room_temp - outdoor_temp
        heat_loss_kw = self.loss_factor * self.floor_area * delta_t / 1000.0

        a_o, b_o, c_o = COP_OUTDOOR_COEFFS
        a_s, b_s, c_s = COP_SUPPLY_COEFFS
        k_factor = float(self._entry.data.get(CONF_K_FACTOR, DEFAULT_K_FACTOR))
        cop_out = a_o + b_o * outdoor_temp + c_o * outdoor_temp**2
        cop_sup = a_s + b_s * supply_temp + c_s * supply_temp**2
        cop = max(1.0, (cop_out + cop_sup) / 2 * k_factor)

        demand_kw = heat_loss_kw / cop
        total_energy = demand_kw * self.horizon

        power_now = 0.0
        if self.power_entity:
            power_state = self.hass.states.get(self.power_entity)
            if power_state and power_state.state not in ("unknown", "unavailable"):
                try:
                    power_now = float(power_state.state)
                except ValueError:
                    power_now = 0.0

        max_power = max(self.max_power, power_now)

        indices = sorted(range(len(prices)), key=lambda i: prices[i])
        energy_alloc = [0.0] * len(prices)
        remaining = total_energy
        for idx in indices:
            alloc = min(max_power, remaining)
            energy_alloc[idx] = alloc
            remaining -= alloc
            if remaining <= 0:
                break

        if total_energy > 0:
            weighted_avg = (
                sum(i * energy_alloc[i] for i in range(len(prices))) / total_energy
            )
        else:
            weighted_avg = 0.0

        shift = round(weighted_avg - self.horizon / 2)
        shift = max(-5, min(5, shift))

        _LOGGER.debug("Calculated shift %s with demand %.2f kW", shift, demand_kw)

        self._forecast = prices
        self._attr_native_value = float(shift)
        self.demand_sensor.update_value(demand_kw, {"demand": energy_alloc})
        self.net_energy_sensor.update_value(total_energy)
