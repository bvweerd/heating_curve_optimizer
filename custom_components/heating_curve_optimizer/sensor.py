from __future__ import annotations

from typing import Any, cast

import logging
import math

import aiohttp
from pulp import LpMinimize, LpProblem, LpVariable, lpSum, value
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    DOMAIN,
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    CONF_AREA_M2,
    CONF_ENERGY_LABEL,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_K_FACTOR,
    CONF_PRICE_SENSOR,
    CONF_PRICE_SETTINGS,
    CONF_CONFIGS,
    INDOOR_TEMPERATURE,
    DEFAULT_COP_AT_35,
    DEFAULT_K_FACTOR,
    U_VALUE_MAP,
)
from .entity import BaseUtilitySensor

_LOGGER = logging.getLogger(__name__)

UTILITY_ENTITIES: list[BaseUtilitySensor] = []
PARALLEL_UPDATES = 1
HORIZON_HOURS = 6


class OutdoorTemperatureSensor(BaseUtilitySensor):
    """Sensor with current outdoor temperature and 24h forecast."""

    def __init__(
        self, hass: HomeAssistant, name: str, unique_id: str, device: DeviceInfo
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="°C",
            device_class="temperature",
            icon="mdi:thermometer",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = aiohttp.ClientSession()
        self._extra_attrs: dict[str, list[float]] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _fetch_weather(self) -> tuple[float, list[float]]:
        from datetime import datetime

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m&current_weather=true&timezone=UTC"
        )
        async with self.session.get(url) as resp:
            data = await resp.json()

        current = float(data.get("current_weather", {}).get("temperature", 0))

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        values = hourly.get("temperature_2m", [])

        if not times or not values:
            return current, []

        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        start_idx = 0
        for i, ts in enumerate(times):
            try:
                t = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if t >= now:
                start_idx = i
                break

        temps = [float(v) for v in values[start_idx : start_idx + 24]]
        return current, temps

    async def async_update(self):
        current, forecast = await self._fetch_weather()
        self._attr_available = True
        self._attr_native_value = round(current, 2)
        self._extra_attrs = {"forecast": [round(v, 2) for v in forecast]}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        await self.session.close()


class CurrentElectricityPriceSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        price_sensor: str,
        source_type: str,
        price_settings: dict[str, float],
        icon: str,
        device: DeviceInfo,
    ):
        unit = "€/kWh"
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit=unit,
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.price_sensor = price_sensor
        self.source_type = source_type
        self.price_settings = price_settings

    async def async_update(self):
        state = self.hass.states.get(self.price_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            self._attr_available = False
            _LOGGER.warning("Price sensor %s is unavailable", self.price_sensor)
            return
        try:
            base_price = float(state.state)
        except ValueError:
            self._attr_available = False
            _LOGGER.warning("Price sensor %s has invalid state", self.price_sensor)
            return
        self._attr_available = True

        self._attr_native_value = round(base_price, 8)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        await self.session.close()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                self.price_sensor,
                self._handle_price_change,
            )
        )

    async def _handle_price_change(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            _LOGGER.warning("Price sensor %s is unavailable", self.price_sensor)
            return
        await self.async_update()
        self.async_write_ha_state()


class HeatLossSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        area_m2: float,
        energy_label: str,
        indoor_sensor: str | None,
        icon: str,
        device: DeviceInfo,
        outdoor_sensor: str | None = None,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self._extra_attrs: dict[str, list[float]] = {}
        self.area_m2 = area_m2
        self.energy_label = energy_label
        self.indoor_sensor = indoor_sensor
        self.outdoor_sensor = outdoor_sensor
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = aiohttp.ClientSession()

    async def _fetch_weather(self) -> tuple[float, list[float]]:
        """Return current temperature and the next 24h forecast."""
        from datetime import datetime

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m&current_weather=true&timezone=UTC"
        )
        async with self.session.get(url) as resp:
            data = await resp.json()

        current = float(data.get("current_weather", {}).get("temperature", 0))

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        values = hourly.get("temperature_2m", [])

        if not times or not values:
            return current, []

        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        start_idx = 0
        for i, ts in enumerate(times):
            try:
                t = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if t >= now:
                start_idx = i
                break

        temps = [float(v) for v in values[start_idx : start_idx + 24]]
        return current, temps

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _compute_value(self) -> None:
        current = None
        forecast: list[float] = []
        if self.outdoor_sensor:
            state = self.hass.states.get(self.outdoor_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current = float(state.state)
                except ValueError:
                    current = None
                forecast = [
                    float(v)
                    for v in state.attributes.get("forecast", [])
                    if isinstance(v, (int, float, str))
                ]
        if current is None:
            current, forecast = await self._fetch_weather()
        self._attr_available = True
        u_value = U_VALUE_MAP.get(self.energy_label.upper(), 1.0)
        indoor = INDOOR_TEMPERATURE
        if self.indoor_sensor:
            state = self.hass.states.get(self.indoor_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    indoor = float(state.state)
                except ValueError:
                    indoor = INDOOR_TEMPERATURE
        q_loss = self.area_m2 * u_value * (indoor - current) / 1000.0
        self._attr_native_value = round(q_loss, 3)
        forecast_values = [
            round(self.area_m2 * u_value * (indoor - t) / 1000.0, 3) for t in forecast
        ]
        self._extra_attrs = {"forecast": forecast_values}

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()


class WindowSolarGainSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        east_m2: float,
        west_m2: float,
        south_m2: float,
        u_value: float,
        icon: str,
        device: DeviceInfo,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.east = east_m2
        self.west = west_m2
        self.south = south_m2
        self.u_value = u_value
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = aiohttp.ClientSession()
        self._extra_attrs: dict[str, list[float]] = {}

    async def _fetch_radiation(self) -> list[float]:
        from datetime import datetime

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=shortwave_radiation&timezone=UTC"
        )
        async with self.session.get(url) as resp:
            data = await resp.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        values = hourly.get("shortwave_radiation", [])

        if not times or not values:
            return []

        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        start_idx = 0
        for i, ts in enumerate(times):
            try:
                t = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if t >= now:
                start_idx = i
                break

        return [float(v) for v in values[start_idx : start_idx + 24]]

    def _orientation_factor(self, azimuth: float, orientation: float) -> float:
        diff = abs(azimuth - orientation)
        if diff > 180:
            diff = 360 - diff
        return max(math.cos(math.radians(diff)), 0)

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _compute_value(self) -> None:
        rad = await self._fetch_radiation()
        sun = self.hass.states.get("sun.sun")
        az = float(sun.attributes.get("azimuth", 0)) if sun else 0.0
        elev = float(sun.attributes.get("elevation", 0)) if sun else 0.0
        fac = max(math.sin(math.radians(elev)), 0)
        total_area = self.east + self.west + self.south
        orient_factors = (
            self._orientation_factor(az, 90) * self.east
            + self._orientation_factor(az, 270) * self.west
            + self._orientation_factor(az, 180) * self.south
        ) / (total_area or 1)
        if rad:
            current = rad[0] * fac * orient_factors * total_area / self.u_value / 1000.0
            forecast = [
                round(v * fac * orient_factors * total_area / self.u_value / 1000.0, 3)
                for v in rad
            ]
        else:
            current = 0.0
            forecast = []
        self._attr_available = True
        self._attr_native_value = round(current, 3)
        self._extra_attrs = {"forecast": forecast}

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        await self.session.close()


class NetHeatDemandSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        area_m2: float,
        energy_label: str,
        indoor_sensor: str | None,
        icon: str,
        device: DeviceInfo,
        heat_loss_sensor: HeatLossSensor | None = None,
        window_gain_sensor: WindowSolarGainSensor | None = None,
        outdoor_sensor: str | None = None,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.area_m2 = area_m2
        self.energy_label = energy_label
        self.indoor_sensor = indoor_sensor
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = aiohttp.ClientSession()
        self.heat_loss_sensor = heat_loss_sensor
        self.window_gain_sensor = window_gain_sensor
        self.outdoor_sensor = outdoor_sensor
        self._extra_attrs: dict[str, list[float]] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _compute_value(self) -> None:
        t_outdoor = None
        if self.outdoor_sensor:
            state = self.hass.states.get(self.outdoor_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    t_outdoor = float(state.state)
                except ValueError:
                    t_outdoor = None
        if t_outdoor is None:
            url = (
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={self.latitude}&longitude={self.longitude}"
                "&current_weather=true&hourly=temperature_2m&timezone=UTC"
            )
            async with self.session.get(url) as resp:
                data = await resp.json()
            t_outdoor = float(data.get("current_weather", {}).get("temperature", 0))

        solar_total = 0.0
        if self.window_gain_sensor:
            state = self.window_gain_sensor
            try:
                solar_total = float(state.native_value or 0.0)
            except (ValueError, TypeError):
                solar_total = 0.0

        self._attr_available = True
        u_value = U_VALUE_MAP.get(self.energy_label.upper(), 1.0)
        indoor = INDOOR_TEMPERATURE
        if self.indoor_sensor:
            state = self.hass.states.get(self.indoor_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    indoor = float(state.state)
                except ValueError:
                    indoor = INDOOR_TEMPERATURE
        q_loss = self.area_m2 * u_value * (indoor - t_outdoor) / 1000.0
        q_solar = solar_total
        q_net = q_loss - q_solar
        self._attr_native_value = round(q_net, 3)

        loss_fc = []
        gain_fc = []
        if self.heat_loss_sensor:
            loss_fc = self.heat_loss_sensor.extra_state_attributes.get("forecast", [])
        if self.window_gain_sensor:
            gain_fc = self.window_gain_sensor.extra_state_attributes.get("forecast", [])

        n = max(len(loss_fc), len(gain_fc))
        forecast = []
        for i in range(n):
            lf = loss_fc[i] if i < len(loss_fc) else 0.0
            gf = gain_fc[i] if i < len(gain_fc) else 0.0
            forecast.append(round(lf - gf, 3))

        self._extra_attrs = {"forecast": forecast}

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self.window_gain_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self.window_gain_sensor.entity_id,
                    self._handle_change,
                )
            )

    async def _handle_change(self, event):
        await self._compute_value()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        await self.session.close()


class NetPowerConsumptionSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        consumption_sensors: list[str],
        production_sensors: list[str],
        icon: str,
        device: DeviceInfo,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="W",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.consumption_sensors = consumption_sensors
        self.production_sensors = production_sensors

    def _compute_value(self) -> None:
        consumption = 0.0
        for sensor in self.consumption_sensors:
            state = self.hass.states.get(sensor)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            try:
                consumption += float(state.state)
            except ValueError:
                continue

        production = 0.0
        for sensor in self.production_sensors:
            state = self.hass.states.get(sensor)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            try:
                production += float(state.state)
            except ValueError:
                continue

        self._attr_available = True
        self._attr_native_value = round(consumption - production, 3)

    async def async_update(self):
        self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for sensor in self.consumption_sensors + self.production_sensors:
            self.async_on_remove(
                async_track_state_change_event(self.hass, sensor, self._handle_change)
            )

    async def _handle_change(self, event):
        self._compute_value()
        self.async_write_ha_state()


class QuadraticCopSensor(BaseUtilitySensor):
    """COP sensor using a linear k-factor model."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        supply_sensor: str,
        outdoor_sensor: str,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="",
            device_class=None,
            icon="mdi:alpha-c-circle",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self.hass = hass
        self.supply_sensor = supply_sensor
        self.outdoor_sensor = outdoor_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop

    async def async_update(self):
        s_state = self.hass.states.get(self.supply_sensor)
        o_state = self.hass.states.get(self.outdoor_sensor)
        if (
            s_state is None
            or o_state is None
            or s_state.state in ("unknown", "unavailable")
            or o_state.state in ("unknown", "unavailable")
        ):
            self._attr_available = False
            return
        try:
            s_temp = float(s_state.state)
            float(o_state.state)
        except ValueError:
            self._attr_available = False
            return
        delta = s_temp - 35
        cop = self.base_cop - self.k_factor * delta
        self._attr_available = True
        self._attr_native_value = round(cop, 3)


def _optimize_offsets(
    demand: list[float],
    prices: list[float],
    *,
    base_temp: float = 35.0,
    k_factor: float = DEFAULT_K_FACTOR,
) -> list[int]:
    """Return optimal offsets for the given demand and prices."""
    horizon = min(len(demand), len(prices))
    if horizon == 0:
        return []
    model = LpProblem("HeatingCurveOptimization", LpMinimize)
    offsets = LpVariable.dicts("offset", range(horizon), -4, 4, cat="Integer")
    deltas = LpVariable.dicts("delta", range(1, horizon), 0, 8, cat="Integer")

    base_cop = DEFAULT_COP_AT_35 - k_factor * (base_temp - 35)
    reciprocal_base_cop = 1 / base_cop
    cop_derivative = k_factor / (base_cop**2)

    costs = []
    for t in range(horizon):
        q = demand[t]
        offset = offsets[t]
        cost = q * prices[t] * (reciprocal_base_cop + cop_derivative * offset)
        costs.append(cost)

    model += lpSum(costs)

    for t in range(horizon):
        model += (base_temp + offsets[t]) >= 28
        model += (base_temp + offsets[t]) <= 45

    for t in range(1, horizon):
        model += offsets[t] - offsets[t - 1] <= deltas[t]
        model += offsets[t - 1] - offsets[t] <= deltas[t]
        model += deltas[t] <= 1

    model.solve()
    return [int(value(offsets[t])) for t in range(horizon)]


class HeatingCurveOffsetSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        net_heat_sensor: str | SensorEntity,
        price_sensor: str,
        device: DeviceInfo,
        *,
        k_factor: float = DEFAULT_K_FACTOR,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="°C",
            device_class="temperature",
            icon="mdi:chart-line",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self.hass = hass
        self.net_heat_sensor = net_heat_sensor
        self.price_sensor = price_sensor
        self.k_factor = k_factor
        self._extra_attrs: dict[str, list[int]] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[int]]:
        return self._extra_attrs

    def _extract_prices(self, state) -> list[float]:
        from homeassistant.util import dt as dt_util

        now = dt_util.utcnow()
        hour = now.hour
        raw_today = state.attributes.get("raw_today", [])
        raw_tomorrow = state.attributes.get("raw_tomorrow", [])

        prices: list[float] = []
        idx = hour
        for _ in range(HORIZON_HOURS):
            if idx < len(raw_today):
                entry = raw_today[idx]
                val = entry.get("value") if isinstance(entry, dict) else entry
                prices.append(float(val))
                idx += 1
                continue
            t_idx = idx - len(raw_today)
            if t_idx < len(raw_tomorrow):
                entry = raw_tomorrow[t_idx]
                val = entry.get("value") if isinstance(entry, dict) else entry
                prices.append(float(val))
                idx += 1
                continue
            break

        if len(prices) < HORIZON_HOURS:
            try:
                cur = float(state.state)
            except (ValueError, TypeError):
                cur = 0.0
            prices.extend([cur] * (HORIZON_HOURS - len(prices)))
        return prices

    def _extract_demand(self, state) -> list[float]:
        forecast = state.attributes.get("forecast", [])
        demand = [float(v) for v in forecast[:HORIZON_HOURS]]
        if len(demand) < HORIZON_HOURS:
            demand.extend([0.0] * (HORIZON_HOURS - len(demand)))
        return demand

    async def async_update(self):
        ent = (
            self.net_heat_sensor.entity_id
            if hasattr(self.net_heat_sensor, "entity_id")
            else self.net_heat_sensor
        )
        price_state = self.hass.states.get(self.price_sensor)
        net_state = self.hass.states.get(ent) if ent else None

        if (
            net_state is None
            or price_state is None
            or net_state.state in ("unknown", "unavailable")
            or price_state.state in ("unknown", "unavailable")
        ):
            self._attr_available = False
            return

        demand = self._extract_demand(net_state)
        prices = self._extract_prices(price_state)

        offsets = _optimize_offsets(
            demand,
            prices,
            base_temp=35.0,
            k_factor=self.k_factor,
        )

        if offsets:
            self._attr_native_value = offsets[0]
        else:
            self._attr_native_value = 0
        self._extra_attrs = {"forecast": offsets}
        self._attr_available = True

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if not isinstance(self.net_heat_sensor, str):
            self.net_heat_sensor = self.net_heat_sensor.entity_id
        for ent in (self.net_heat_sensor, self.price_sensor):
            if ent:
                self.async_on_remove(
                    async_track_state_change_event(self.hass, ent, self._handle_change)
                )

    async def _handle_change(self, event):
        await self.async_update()
        self.async_write_ha_state()


class EnergyConsumptionForecastSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        consumption_sensors: list[str],
        production_sensors: list[str],
        icon: str,
        device: DeviceInfo,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="kWh",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.consumption_sensors = consumption_sensors
        self.production_sensors = production_sensors
        self._extra_attrs: dict[str, list[float]] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _fetch_history(self, sensors: list[str], start, end) -> dict[str, list]:
        """Fetch history data for the given sensors."""  # mypy: ignore-errors
        from homeassistant.components.recorder import history, get_instance

        if not sensors:
            return {}

        func = cast(Any, history.get_significant_states)
        recorder = get_instance(self.hass)

        try:
            return cast(
                dict[str, list],
                await recorder.async_add_executor_job(
                    func,
                    self.hass,
                    start,
                    end,
                    sensors,
                    None,
                    True,
                    False,
                ),
            )
        except AttributeError:
            # Older HA versions expect a single entity_id
            result: dict[str, list] = {}
            for sensor in sensors:
                data = cast(
                    dict[str, list],
                    await recorder.async_add_executor_job(
                        func,
                        self.hass,
                        start,
                        end,
                        sensor,
                        None,
                        True,
                        False,
                    ),
                )
                for ent_id, states in data.items():
                    result.setdefault(ent_id, []).extend(states)
            return result

    def _hourly_averages(self, data: dict[str, list]) -> list[float]:
        from homeassistant.util import dt as dt_util

        totals = [0.0] * 24
        counts = [0] * 24
        for states in data.values():
            prev_val = None
            prev_ts = None
            for s in states:
                try:
                    val = float(s.state)
                except (ValueError, TypeError):
                    continue
                ts = dt_util.as_utc(
                    getattr(s, "last_updated", None) or dt_util.utcnow()
                )
                if prev_val is None:
                    prev_val = val
                    prev_ts = ts
                    continue
                if prev_ts is not None and ts <= prev_ts:
                    continue
                assert prev_ts is not None
                hour = prev_ts.hour
                totals[hour] += val - prev_val
                counts[hour] += 1
                prev_val = val
                prev_ts = ts
        return [totals[i] / counts[i] if counts[i] else 0.0 for i in range(24)]

    async def _compute_value(self) -> None:
        from datetime import timedelta
        from homeassistant.util import dt as dt_util

        end = dt_util.utcnow()
        start = end - timedelta(days=14)

        cons_hist = await self._fetch_history(self.consumption_sensors, start, end)
        prod_hist = await self._fetch_history(self.production_sensors, start, end)

        cons_avg = self._hourly_averages(cons_hist)
        prod_avg = self._hourly_averages(prod_hist)

        net = [cons_avg[i] - prod_avg[i] for i in range(24)]

        self._attr_native_value = round(net[end.hour], 3)
        self._extra_attrs = {"standby_forecast": [round(v, 3) for v in net]}
        self._attr_available = True

    async def async_update(self):
        await self._compute_value()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    price_settings = entry.options.get(
        CONF_PRICE_SETTINGS, entry.data.get(CONF_PRICE_SETTINGS, {})
    )
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heating Curve Optimizer",
    )
    price_device_info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_prices")},
        name="Energy Prices",
    )
    entities: list[BaseUtilitySensor] = []

    outdoor_sensor_entity = OutdoorTemperatureSensor(
        hass=hass,
        name="Outdoor Temperature",
        unique_id=f"{entry.entry_id}_outdoor_temperature",
        device=device_info,
    )
    entities.append(outdoor_sensor_entity)

    configs = entry.options.get(CONF_CONFIGS, entry.data.get(CONF_CONFIGS, []))
    consumption_sources: list[str] = []
    production_sources: list[str] = []
    for cfg in configs:
        if cfg.get(CONF_SOURCE_TYPE) == SOURCE_TYPE_CONSUMPTION:
            consumption_sources.extend(cfg.get(CONF_SOURCES, []))
        elif cfg.get(CONF_SOURCE_TYPE) == SOURCE_TYPE_PRODUCTION:
            production_sources.extend(cfg.get(CONF_SOURCES, []))

    area_m2 = entry.data.get(CONF_AREA_M2)
    energy_label = entry.data.get(CONF_ENERGY_LABEL)
    indoor_sensor = entry.data.get(CONF_INDOOR_TEMPERATURE_SENSOR)
    supply_temp_sensor = entry.data.get(CONF_SUPPLY_TEMPERATURE_SENSOR)
    k_factor = float(entry.data.get(CONF_K_FACTOR, DEFAULT_K_FACTOR))
    glass_east = float(entry.data.get(CONF_GLASS_EAST_M2, 0))
    glass_west = float(entry.data.get(CONF_GLASS_WEST_M2, 0))
    glass_south = float(entry.data.get(CONF_GLASS_SOUTH_M2, 0))
    glass_u = float(entry.data.get(CONF_GLASS_U_VALUE, 1.2))

    price_sensor = entry.data.get(CONF_PRICE_SENSOR)
    if price_sensor:
        entities.append(
            CurrentElectricityPriceSensor(
                hass=hass,
                name="Current Consumption Price",
                unique_id=f"{entry.entry_id}_current_consumption_price",
                price_sensor=price_sensor,
                source_type=SOURCE_TYPE_CONSUMPTION,
                price_settings=price_settings,
                icon="mdi:transmission-tower-import",
                device=price_device_info,
            )
        )
        entities.append(
            CurrentElectricityPriceSensor(
                hass=hass,
                name="Current Production Price",
                unique_id=f"{entry.entry_id}_current_production_price",
                price_sensor=price_sensor,
                source_type=SOURCE_TYPE_PRODUCTION,
                price_settings=price_settings,
                icon="mdi:transmission-tower-export",
                device=price_device_info,
            )
        )

        UTILITY_ENTITIES.extend(entities)

    heat_loss_sensor = None
    if area_m2 and energy_label:
        heat_loss_sensor = HeatLossSensor(
            hass=hass,
            name="Hourly Heat Loss",
            unique_id=f"{entry.entry_id}_hourly_heat_loss",
            area_m2=float(area_m2),
            energy_label=energy_label,
            indoor_sensor=indoor_sensor,
            icon="mdi:home-thermometer",
            device=device_info,
        )
        entities.append(heat_loss_sensor)

    window_gain_sensor = None
    if area_m2 and (glass_east or glass_west or glass_south):
        window_gain_sensor = WindowSolarGainSensor(
            hass=hass,
            name="Window Solar Gain",
            unique_id=f"{entry.entry_id}_window_solar_gain",
            east_m2=glass_east,
            west_m2=glass_west,
            south_m2=glass_south,
            u_value=glass_u,
            icon="mdi:window-closed-variant",
            device=device_info,
        )
        entities.append(window_gain_sensor)

    if consumption_sources or production_sources:
        entities.append(
            EnergyConsumptionForecastSensor(
                hass=hass,
                name="Expected Energy Consumption",
                unique_id=f"{entry.entry_id}_expected_energy_consumption",
                consumption_sensors=consumption_sources,
                production_sensors=production_sources,
                icon="mdi:flash-clock",
                device=device_info,
            )
        )
        entities.append(
            NetPowerConsumptionSensor(
                hass=hass,
                name="Current Net Consumption",
                unique_id=f"{entry.entry_id}_current_net_consumption",
                consumption_sensors=consumption_sources,
                production_sensors=production_sources,
                icon="mdi:flash",
                device=device_info,
            )
        )

    net_heat_sensor = None
    if area_m2 and energy_label:
        net_heat_sensor = NetHeatDemandSensor(
            hass=hass,
            name="Hourly Net Heat Demand",
            unique_id=f"{entry.entry_id}_hourly_net_heat_demand",
            area_m2=float(area_m2),
            energy_label=energy_label,
            indoor_sensor=indoor_sensor,
            icon="mdi:fire",
            device=device_info,
            heat_loss_sensor=heat_loss_sensor,
            window_gain_sensor=window_gain_sensor,
        )
        entities.append(net_heat_sensor)

    if supply_temp_sensor:
        entities.append(
            QuadraticCopSensor(
                hass=hass,
                name="Heat Pump COP",
                unique_id=f"{entry.entry_id}_cop",
                supply_sensor=supply_temp_sensor,
                outdoor_sensor="sensor.outdoor_temperature",
                k_factor=k_factor,
                base_cop=DEFAULT_COP_AT_35,
                device=device_info,
            )
        )

    if net_heat_sensor and price_sensor:
        entities.append(
            HeatingCurveOffsetSensor(
                hass=hass,
                name="Heating Curve Offset",
                unique_id=f"{entry.entry_id}_heating_curve_offset",
                net_heat_sensor=net_heat_sensor,
                price_sensor=price_sensor,
                device=device_info,
                k_factor=k_factor,
            )
        )

    if entities:
        UTILITY_ENTITIES.extend(ent for ent in entities if ent not in UTILITY_ENTITIES)

    async_add_entities(entities, True)

    hass.data[DOMAIN]["entities"] = {ent.entity_id: ent for ent in entities}
