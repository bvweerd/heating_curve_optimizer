from __future__ import annotations

import asyncio
from functools import partial
import logging
import math
from typing import Any, cast

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pycares  # noqa: F401
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_AREA_M2,
    CONF_CONFIGS,
    CONF_ENERGY_LABEL,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_OUTDOOR_TEMPERATURE_SENSOR,
    CONF_K_FACTOR,
    CONF_BASE_COP,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_POWER_CONSUMPTION,
    CONF_PRICE_SENSOR,
    CONF_PRICE_SETTINGS,
    CONF_PLANNING_WINDOW,
    CONF_TIME_BASE,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    DEFAULT_COP_AT_35,
    DEFAULT_K_FACTOR,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_TIME_BASE,
    DOMAIN,
    INDOOR_TEMPERATURE,
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
    U_VALUE_MAP,
)
from .entity import BaseUtilitySensor

# Create a persistent pycares channel to ensure the library's background
# thread is started before test fixtures snapshot running threads. Without
# this, the thread may be flagged as lingering during teardown.
_PYCARES_CHANNEL = pycares.Channel()

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


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
        self.session = async_get_clientsession(hass)
        self._extra_attrs: dict[str, list[float]] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _fetch_weather(self) -> tuple[float, list[float]]:
        from datetime import datetime

        _LOGGER.debug("Fetching weather for %.4f, %.4f", self.latitude, self.longitude)

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m&current_weather=true&timezone=UTC"
        )
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error fetching weather data: %s", err)
            self._attr_available = False
            return 0.0, []
        self._attr_available = True

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
        _LOGGER.debug("Weather data current=%s forecast=%s", current, temps)
        return current, temps

    async def async_update(self):
        current, forecast = await self._fetch_weather()
        if not self._attr_available:
            return
        self._attr_native_value = round(current, 2)
        self._extra_attrs = {"forecast": [round(v, 2) for v in forecast]}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()


class SunIntensityPredictionSensor(BaseUtilitySensor):
    """Sensor providing current and forecasted sun intensity."""

    def __init__(
        self, hass: HomeAssistant, name: str, unique_id: str, device: DeviceInfo
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="W/m²",
            device_class=None,
            icon="mdi:white-balance-sunny",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = async_get_clientsession(hass)
        self._extra_attrs: dict[str, list[float]] = {}
        self._history: list[float] = []

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        if not self._attr_available:
            return {}
        attrs = dict(self._extra_attrs)
        attrs["history"] = self._history
        return attrs

    async def _fetch_radiation(self) -> list[float]:
        from datetime import datetime

        _LOGGER.debug(
            "Fetching sun intensity for %.4f, %.4f", self.latitude, self.longitude
        )

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=shortwave_radiation&timezone=UTC"
        )
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error fetching sun intensity data: %s", err)
            self._attr_available = False
            return []
        self._attr_available = True

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

    async def async_update(self):
        rad = await self._fetch_radiation()
        if not self._attr_available:
            return
        current = rad[0] if rad else 0.0
        self._attr_native_value = round(current, 2)
        self._extra_attrs = {"forecast": [round(v, 2) for v in rad]}
        self._history.append(self._attr_native_value)
        if len(self._history) > 24:
            self._history = self._history[-24:]

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()


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
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                self.price_sensor,
                self._handle_price_change,
            )
        )

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()

    async def _handle_price_change(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            _LOGGER.warning("Price sensor %s is unavailable", self.price_sensor)
            return
        await self.async_update()
        # During unit tests the entity is not added via an EntityComponent and
        # therefore does not get an entity_id assigned. In that case
        # ``async_write_ha_state`` would raise ``NoEntitySpecifiedError``. We
        # still want to update the internal value but only call
        # ``async_write_ha_state`` when an entity_id is present.
        if self.entity_id:
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
        self.session = async_get_clientsession(hass)

    async def _fetch_weather(self) -> tuple[float, list[float]]:
        """Return current temperature and the next 24h forecast."""
        from datetime import datetime

        _LOGGER.debug(
            "Fetching heat loss weather for %.4f, %.4f",
            self.latitude,
            self.longitude,
        )

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m&current_weather=true&timezone=UTC"
        )
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error fetching heat loss weather data: %s", err)
            self._attr_available = False
            return 0.0, []
        self._attr_available = True

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
        _LOGGER.debug("Heat loss weather current=%s forecast=%s", current, temps)
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
            if not self._attr_available:
                return
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
        _LOGGER.debug(
            "Heat loss calculation area=%.2f u=%.2f indoor=%.2f outdoor=%.2f",
            self.area_m2,
            u_value,
            indoor,
            current,
        )
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
        self.session = async_get_clientsession(hass)
        self._extra_attrs: dict[str, list[float]] = {}

    async def _fetch_radiation(self) -> list[float]:
        from datetime import datetime

        _LOGGER.debug(
            "Fetching radiation for %.4f, %.4f",
            self.latitude,
            self.longitude,
        )

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=shortwave_radiation&timezone=UTC"
        )
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error fetching radiation data: %s", err)
            self._attr_available = False
            return []
        self._attr_available = True

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

        values_list = [float(v) for v in values[start_idx : start_idx + 24]]
        _LOGGER.debug("Radiation forecast=%s", values_list)
        return values_list

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
        if not self._attr_available:
            return
        _LOGGER.debug("Solar gain raw radiation=%s", rad)
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
        _LOGGER.debug("Solar gain current=%s forecast=%s", current, forecast)
        self._attr_available = True
        self._attr_native_value = round(current, 3)
        self._extra_attrs = {"forecast": forecast}

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()


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
        self.session = async_get_clientsession(hass)
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
        _LOGGER.debug("Net heat demand loss=%s solar=%s net=%s", q_loss, q_solar, q_net)
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
        net = consumption - production
        _LOGGER.debug(
            "Power consumption total=%s production=%s net=%s",
            consumption,
            production,
            net,
        )
        self._attr_native_value = round(net, 3)

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
    """COP sensor using an empirical formula."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        supply_sensor: str,
        device: DeviceInfo,
        outdoor_sensor: str | None = None,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
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
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = async_get_clientsession(hass)

    async def _fetch_outdoor_temp(self) -> float | None:
        """Fetch current outdoor temperature from Open-Meteo."""
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&current_weather=true&timezone=UTC"
        )
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error fetching outdoor temperature: %s", err)
            self._attr_available = False
            return None
        self._attr_available = True
        return float(data.get("current_weather", {}).get("temperature", 0))

    async def async_update(self):
        s_state = self.hass.states.get(self.supply_sensor)
        if s_state is None or s_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            return
        try:
            s_temp = float(s_state.state)
        except ValueError:
            self._attr_available = False
            return

        o_temp = None
        if self.outdoor_sensor:
            o_state = self.hass.states.get(self.outdoor_sensor)
            if o_state and o_state.state not in ("unknown", "unavailable"):
                try:
                    o_temp = float(o_state.state)
                except ValueError:
                    o_temp = None
        if o_temp is None:
            o_temp = await self._fetch_outdoor_temp()
            if o_temp is None:
                return

        cop = (
            self.base_cop
            + self.outdoor_temp_coefficient * o_temp
            - self.k_factor * (s_temp - 35)
        )
        _LOGGER.debug(
            "Calculated COP with supply=%s outdoor=%s -> %s", s_temp, o_temp, cop
        )
        self._attr_available = True
        self._attr_native_value = round(cop, 3)


class HeatPumpThermalPowerSensor(BaseUtilitySensor):
    """Calculate current thermal output of the heat pump."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        power_sensor: str,
        supply_sensor: str,
        outdoor_sensor: str,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon="mdi:fire",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.power_sensor = power_sensor
        self.supply_sensor = supply_sensor
        self.outdoor_sensor = outdoor_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop

    async def async_update(self):
        p_state = self.hass.states.get(self.power_sensor)
        s_state = self.hass.states.get(self.supply_sensor)
        o_state = self.hass.states.get(self.outdoor_sensor)
        if (
            p_state is None
            or s_state is None
            or o_state is None
            or p_state.state in ("unknown", "unavailable")
            or s_state.state in ("unknown", "unavailable")
            or o_state.state in ("unknown", "unavailable")
        ):
            self._attr_available = False
            return
        try:
            power = float(p_state.state)
            s_temp = float(s_state.state)
            o_temp = float(o_state.state)
        except ValueError:
            self._attr_available = False
            return
        cop = self.base_cop + 0.08 * o_temp - self.k_factor * (s_temp - 35)
        thermal_power = power * cop / 1000.0
        _LOGGER.debug(
            "Thermal power calc power=%s cop=%s -> %s",
            power,
            cop,
            thermal_power,
        )
        self._attr_available = True
        self._attr_native_value = round(thermal_power, 3)


class CalculatedSupplyTemperatureSensor(BaseUtilitySensor):
    """Calculate target supply temperature based on the heating curve."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        *,
        outdoor_sensor: str | SensorEntity,
        offset_entity: str | SensorEntity,
        device: DeviceInfo,
    ) -> None:
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
        self.outdoor_sensor = outdoor_sensor
        self.offset_entity = offset_entity

    async def async_added_to_hass(self) -> None:  # pragma: no cover - simple track
        await super().async_added_to_hass()
        if isinstance(self.outdoor_sensor, SensorEntity):
            self.outdoor_sensor = self.outdoor_sensor.entity_id
        if isinstance(self.offset_entity, SensorEntity):
            self.offset_entity = self.offset_entity.entity_id
        entities = [
            cast(str, self.outdoor_sensor),
            cast(str, self.offset_entity),
            "number.heating_curve_min",
            "number.heating_curve_max",
        ]
        for ent in entities:
            self.async_on_remove(
                async_track_state_change_event(self.hass, ent, self._handle_change)
            )

    async def _handle_change(self, event):  # pragma: no cover - simple callback
        await self.async_update()
        if self.entity_id:
            self.async_write_ha_state()

    async def async_update(self) -> None:
        out_state = self.hass.states.get(cast(str, self.outdoor_sensor))
        off_state = self.hass.states.get(cast(str, self.offset_entity))

        if out_state is None or out_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            return
        try:
            outdoor = float(out_state.state)
        except ValueError:
            self._attr_available = False
            return

        offset = 0.0
        if off_state is not None and off_state.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                offset = float(off_state.state)
            except ValueError:
                offset = 0.0

        try:
            min_temp = float(self.hass.data[DOMAIN]["heat_curve_min"])
            max_temp = float(self.hass.data[DOMAIN]["heat_curve_max"])
            outdoor_min = float(self.hass.data[DOMAIN]["heat_curve_min_outdoor"])
            outdoor_max = float(self.hass.data[DOMAIN]["heat_curve_max_outdoor"])
        except (KeyError, TypeError, ValueError):
            self._attr_available = False
            return

        base = _calculate_supply_temperature(
            outdoor,
            water_min=min_temp,
            water_max=max_temp,
            outdoor_min=outdoor_min,
            outdoor_max=outdoor_max,
        )
        target = base + offset

        _LOGGER.debug(
            "Calculated supply temp outdoor=%s min=%s max=%s offset=%s -> %s",
            outdoor,
            min_temp,
            max_temp,
            offset,
            target,
        )
        self._attr_available = True
        self._attr_native_value = round(target, 3)


class OptimizedSupplyTemperatureSensor(CalculatedSupplyTemperatureSensor):
    """Supply temperature using the optimized heating curve offset."""

    # Inherits behaviour from ``CalculatedSupplyTemperatureSensor``.
    pass


class DiagnosticsSensor(BaseUtilitySensor):
    """Expose forecast arrays for debugging purposes."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        *,
        heat_loss_sensor: HeatLossSensor | None = None,
        window_gain_sensor: WindowSolarGainSensor | None = None,
        price_sensor: str | None = None,
        cop_sensor: str | SensorEntity | None = None,
        device: DeviceInfo,
        planning_window: int = DEFAULT_PLANNING_WINDOW,
        time_base: int = DEFAULT_TIME_BASE,
    ) -> None:
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="",
            device_class=None,
            icon="mdi:information-outline",
            visible=False,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self.hass = hass
        self.heat_loss_sensor = heat_loss_sensor
        self.window_gain_sensor = window_gain_sensor
        self.price_sensor = price_sensor
        self.cop_sensor = cop_sensor
        self.planning_window = planning_window
        self.time_base = time_base
        self.steps = max(1, int(planning_window * 60 // time_base))
        self._extra_attrs: dict[str, list[float]] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    def _resample(self, data: list[float]) -> list[float]:
        result: list[float] = []
        for step in range(self.steps):
            hour_idx = int(step * self.time_base / 60)
            if hour_idx < len(data):
                result.append(float(data[hour_idx]))
            else:
                result.append(0.0)
        return result

    def _extract_prices(self, state) -> list[float]:
        from homeassistant.util import dt as dt_util

        now = dt_util.utcnow()
        hour = now.hour
        raw_today = state.attributes.get("raw_today", [])
        raw_tomorrow = state.attributes.get("raw_tomorrow", [])

        prices: list[float] = []
        for step in range(self.steps):
            hour_offset = int(step * self.time_base / 60)
            idx = hour + hour_offset
            if idx < len(raw_today):
                entry = raw_today[idx]
                val = entry.get("value") if isinstance(entry, dict) else entry
                prices.append(float(val))
                continue
            t_idx = idx - len(raw_today)
            if t_idx < len(raw_tomorrow):
                entry = raw_tomorrow[t_idx]
                val = entry.get("value") if isinstance(entry, dict) else entry
                prices.append(float(val))
                continue
            break

        if len(prices) < self.steps:
            try:
                cur = float(state.state)
            except (ValueError, TypeError):
                cur = 0.0
            prices.extend([cur] * (self.steps - len(prices)))
        return prices

    async def async_update(self):
        attrs: dict[str, list[float]] = {}

        if self.heat_loss_sensor is not None:
            ent = (
                self.heat_loss_sensor.entity_id
                if hasattr(self.heat_loss_sensor, "entity_id")
                else self.heat_loss_sensor
            )
            state = self.hass.states.get(ent)
            if state:
                forecast = state.attributes.get("forecast", [])
                attrs["forecast_heat_loss"] = self._resample(
                    [float(v) for v in forecast]
                )

        if self.window_gain_sensor is not None:
            ent = (
                self.window_gain_sensor.entity_id
                if hasattr(self.window_gain_sensor, "entity_id")
                else self.window_gain_sensor
            )
            state = self.hass.states.get(ent)
            if state:
                forecast = state.attributes.get("forecast", [])
                attrs["forecast_window_gain"] = self._resample(
                    [float(v) for v in forecast]
                )

        if self.price_sensor:
            state = self.hass.states.get(self.price_sensor)
            if state:
                attrs["forecast_prices"] = [
                    float(v) for v in self._extract_prices(state)
                ]

        if self.cop_sensor:
            ent = (
                self.cop_sensor.entity_id
                if hasattr(self.cop_sensor, "entity_id")
                else self.cop_sensor
            )
            state = self.hass.states.get(ent)
            if state:
                forecast = state.attributes.get("forecast", [])
                if forecast:
                    attrs["forecast_cop"] = [float(v) for v in forecast]

        self._extra_attrs = attrs
        self._attr_available = True


def _calculate_supply_temperature(
    outdoor_temp: float,
    *,
    water_min: float,
    water_max: float,
    outdoor_min: float,
    outdoor_max: float,
) -> float:
    """Return supply temperature for a given outdoor temperature."""
    if outdoor_temp <= outdoor_min:
        return water_max
    if outdoor_temp >= outdoor_max:
        return water_min
    ratio = (outdoor_temp - outdoor_min) / (outdoor_max - outdoor_min)
    return water_max + (water_min - water_max) * ratio


def _optimize_offsets(
    demand: list[float],
    prices: list[float],
    *,
    base_temp: float = 35.0,
    k_factor: float = DEFAULT_K_FACTOR,
    buffer: float = 0.0,
    water_min: float = 28.0,
    water_max: float = 45.0,
) -> tuple[list[int], list[float]]:
    r"""Return cost optimized offsets for the given demand and prices.

    The ``demand`` list contains the net heat demand per hour.  The
    algorithm uses the total energy over the complete horizon and
    distributes that energy over the hours with a dynamic-programming
    approach.  Offsets are restricted to ``\-4`` .. ``+4`` and may only
    change by one degree per step.  The optional ``buffer`` parameter
    represents the current heat surplus (positive) or deficit (negative)
    and the returned buffer evolution shows how this value changes with
    the chosen offsets.  After the planning window the buffer will be close
    to zero.
    """
    _LOGGER.debug(
        "Optimizing offsets demand=%s prices=%s base=%s k=%s buffer=%s",
        demand,
        prices,
        base_temp,
        k_factor,
        buffer,
    )
    horizon = min(len(demand), len(prices))
    if horizon == 0:
        return [], []

    base_cop = DEFAULT_COP_AT_35 - k_factor * (base_temp - 35)
    reciprocal_base_cop = 1 / base_cop
    cop_derivative = k_factor / (base_cop**2)

    allowed_offsets = [
        o for o in range(-4, 5) if water_min <= base_temp + o <= water_max
    ]
    if not allowed_offsets:
        return [0 for _ in range(horizon)], [buffer for _ in range(horizon)]

    target_sum = -int(round(buffer))

    # dynamic programming table storing (cost, prev_offset, prev_sum)
    dp: list[dict[int, dict[int, tuple[float, int | None, int | None]]]] = [
        {} for _ in range(horizon)
    ]

    for off in allowed_offsets:
        cost = demand[0] * prices[0] * (reciprocal_base_cop + cop_derivative * off)
        dp[0][off] = {off: (cost, None, None)}

    for t in range(1, horizon):
        for off in allowed_offsets:
            step_cost = (
                demand[t] * prices[t] * (reciprocal_base_cop + cop_derivative * off)
            )
            for prev_off, sums in dp[t - 1].items():
                if abs(off - prev_off) <= 1:
                    for prev_sum, (prev_cost, _, _) in sums.items():
                        new_sum = prev_sum + off
                        total = prev_cost + step_cost
                        dp[t].setdefault(off, {})
                        cur = dp[t][off].get(new_sum)
                        if cur is None or total < cur[0]:
                            dp[t][off][new_sum] = (total, prev_off, prev_sum)

    if not dp[horizon - 1]:
        return [0 for _ in range(horizon)], [buffer for _ in range(horizon)]

    best_off: int | None = None
    best_sum: int | None = None
    best_cost = math.inf
    for off, sums in dp[horizon - 1].items():
        for sum_off, (cost, _, _) in sums.items():
            if sum_off == target_sum and cost < best_cost:
                best_cost = cost
                best_off = off
                best_sum = sum_off
    if best_off is None:
        for off, sums in dp[horizon - 1].items():
            for sum_off, (cost, _, _) in sums.items():
                if cost < best_cost:
                    best_cost = cost
                    best_off = off
                    best_sum = sum_off

    assert best_off is not None and best_sum is not None

    result = [0] * horizon
    last_off = best_off
    last_sum = best_sum
    result[-1] = last_off
    for t in range(horizon - 1, 0, -1):
        _, prev_off, prev_sum = dp[t][last_off][last_sum]
        assert prev_off is not None and prev_sum is not None
        result[t - 1] = prev_off
        last_off = prev_off
        last_sum = prev_sum

    buffer_evolution: list[float] = []
    cur = buffer
    for off in result:
        cur += off
        buffer_evolution.append(cur)

    _LOGGER.debug(
        "Optimized offsets result=%s buffer_evolution=%s", result, buffer_evolution
    )

    return result, buffer_evolution


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
        planning_window: int = DEFAULT_PLANNING_WINDOW,
        time_base: int = DEFAULT_TIME_BASE,
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
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.net_heat_sensor = net_heat_sensor
        self.price_sensor = price_sensor
        self.k_factor = k_factor
        self.planning_window = planning_window
        self.time_base = time_base
        self.steps = max(1, int(planning_window * 60 // time_base))
        self._extra_attrs: dict[str, list[int] | list[float] | float] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[int] | list[float] | float]:
        return self._extra_attrs

    def _extract_prices(self, state) -> list[float]:
        from homeassistant.util import dt as dt_util

        now = dt_util.utcnow()
        hour = now.hour
        raw_today = state.attributes.get("raw_today", [])
        raw_tomorrow = state.attributes.get("raw_tomorrow", [])

        prices: list[float] = []
        for step in range(self.steps):
            hour_offset = int(step * self.time_base / 60)
            idx = hour + hour_offset
            if idx < len(raw_today):
                entry = raw_today[idx]
                val = entry.get("value") if isinstance(entry, dict) else entry
                prices.append(float(val))
                continue
            t_idx = idx - len(raw_today)
            if t_idx < len(raw_tomorrow):
                entry = raw_tomorrow[t_idx]
                val = entry.get("value") if isinstance(entry, dict) else entry
                prices.append(float(val))
                continue
            break

        if len(prices) < self.steps:
            try:
                cur = float(state.state)
            except (ValueError, TypeError):
                cur = 0.0
            prices.extend([cur] * (self.steps - len(prices)))
        return prices

    def _extract_demand(self, state) -> list[float]:
        forecast = state.attributes.get("forecast", [])
        demand: list[float] = []
        for step in range(self.steps):
            hour_idx = int(step * self.time_base / 60)
            if hour_idx < len(forecast):
                demand.append(float(forecast[hour_idx]))
            else:
                demand.append(0.0)
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
        total_energy = sum(demand)
        _LOGGER.debug("Offset sensor demand=%s prices=%s", demand, prices)

        data = self.hass.data.get(DOMAIN, {})
        water_min = float(data.get("heat_curve_min", 28.0))
        water_max = float(data.get("heat_curve_max", 45.0))
        outdoor_min = float(data.get("heat_curve_min_outdoor", -20.0))
        outdoor_max = float(data.get("heat_curve_max_outdoor", 15.0))

        o_state = self.hass.states.get("sensor.outdoor_temperature")
        if o_state is None or o_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            return
        try:
            outdoor_temp = float(o_state.state)
        except ValueError:
            self._attr_available = False
            return

        base_temp = _calculate_supply_temperature(
            outdoor_temp,
            water_min=water_min,
            water_max=water_max,
            outdoor_min=outdoor_min,
            outdoor_max=outdoor_max,
        )

        offsets, buffer_evolution = await self.hass.async_add_executor_job(
            partial(
                _optimize_offsets,
                base_temp=base_temp,
                k_factor=self.k_factor,
                buffer=0.0,
                water_min=water_min,
                water_max=water_max,
            ),
            demand,
            prices,
        )
        _LOGGER.debug(
            "Calculated offsets=%s buffer_evolution=%s", offsets, buffer_evolution
        )

        if offsets:
            self._attr_native_value = offsets[0]
        else:
            self._attr_native_value = 0
        supply_temps = [
            round(
                max(min(base_temp + off, water_max), water_min),
                1,
            )
            for off in offsets
        ]
        self._extra_attrs = {
            "future_offsets": offsets,
            "prices": prices,
            "buffer_evolution": buffer_evolution,
            "total_energy": round(total_energy, 3),
            "future_supply_temperatures": supply_temps,
            "base_supply_temperature": round(base_temp, 1),
        }
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
        heatpump_sensor: str | None = None,
        pv_sensor: str | None = None,
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
        self.heatpump_sensor = heatpump_sensor
        self.pv_sensor = pv_sensor
        self.session = async_get_clientsession(hass)
        self._extra_attrs: dict[str, list[float]] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _fetch_pv_forecast(self) -> list[float]:
        if not self.pv_sensor:
            return [0.0] * 24

        state = self.hass.states.get(self.pv_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            return [0.0] * 24
        try:
            coeff = float(state.state)
        except ValueError:
            _LOGGER.warning("PV sensor %s has invalid state", self.pv_sensor)
            return [0.0] * 24

        from datetime import datetime

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.hass.config.latitude}&longitude={self.hass.config.longitude}"
            "&hourly=shortwave_radiation&timezone=UTC"
        )
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error fetching PV forecast data: %s", err)
            return [0.0] * 24

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        radiation = hourly.get("shortwave_radiation", [])
        if not times or not radiation:
            return [0.0] * 24

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

        rad = [float(v) for v in radiation[start_idx : start_idx + 24]]
        return [r / 1000 * coeff for r in rad]

    async def _fetch_history(self, sensors: list[str], start, end) -> dict[str, list]:
        """Fetch history data for the given sensors."""  # mypy: ignore-errors
        from homeassistant.components.recorder import history

        if not sensors:
            return {}

        _LOGGER.debug(
            "Fetching history for sensors=%s from=%s to=%s",
            sensors,
            start,
            end,
        )

        func = cast(Any, history.get_significant_states)

        try:
            return cast(
                dict[str, list],
                await self.hass.async_add_executor_job(
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
                    await self.hass.async_add_executor_job(
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
        hp_avg = [0.0] * 24
        if self.heatpump_sensor:
            hp_hist = await self._fetch_history([self.heatpump_sensor], start, end)
            hp_avg = self._hourly_averages(hp_hist)

        cons_adj = [max(cons_avg[i] - hp_avg[i], 0.0) for i in range(24)]
        gross = [cons_adj[i] - prod_avg[i] for i in range(24)]
        pv_forecast = await self._fetch_pv_forecast()
        net = [gross[i] - pv_forecast[i] for i in range(24)]
        _LOGGER.debug(
            "Energy consumption forecast gross=%s net=%s pv=%s",
            gross,
            net,
            pv_forecast,
        )

        self._attr_native_value = round(net[end.hour], 3)
        forecast_net = [round(v, 3) for v in net]
        self._extra_attrs = {
            "standby_forecast": forecast_net,
            "standby_forecast_gross": [round(v, 3) for v in gross],
            "standby_forecast_net": forecast_net,
            "pv_forecast": [round(v, 3) for v in pv_forecast],
        }
        self._attr_available = True

    async def async_update(self):
        await self._compute_value()


class EnergyPriceLevelSensor(BaseUtilitySensor):
    """Compare forecasted consumption with multiple price levels."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        price_sensor: str,
        forecast_sensor: str,
        price_levels: dict[str, float],
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
        self.price_sensor = price_sensor
        self.forecast_sensor = forecast_sensor
        self.price_levels = price_levels
        self._extra_attrs: dict[str, float] = {}

    @property
    def extra_state_attributes(self) -> dict[str, float]:
        return self._extra_attrs

    def _get_price_forecast(self, state) -> list[float]:
        prices: list[float] = []
        attr_forecast = state.attributes.get("forecast")
        if isinstance(attr_forecast, list) and attr_forecast:
            prices = [float(p) for p in attr_forecast]
        else:
            today = state.attributes.get("today")
            tomorrow = state.attributes.get("tomorrow", [])
            if isinstance(today, list):
                combined = today + (tomorrow if isinstance(tomorrow, list) else [])
                prices = [float(p) for p in combined]
        if not prices:
            try:
                price = float(state.state)
            except (ValueError, TypeError):
                return []
            prices = [price] * 24
        return prices

    async def async_update(self):
        price_state = self.hass.states.get(self.price_sensor)
        forecast_state = self.hass.states.get(self.forecast_sensor)
        if (
            price_state is None
            or forecast_state is None
            or price_state.state in ("unknown", "unavailable")
            or forecast_state.state in ("unknown", "unavailable")
        ):
            self._attr_available = False
            return

        energy = forecast_state.attributes.get("standby_forecast_net")
        try:
            energy_forecast = [float(v) for v in energy or []]
        except (TypeError, ValueError):
            energy_forecast = []
        if not energy_forecast:
            self._attr_available = False
            return

        price_forecast = self._get_price_forecast(price_state)
        if not price_forecast:
            self._attr_available = False
            return

        horizon = min(len(price_forecast), len(energy_forecast))
        sorted_levels = sorted(self.price_levels.items(), key=lambda x: x[1])
        attrs: dict[str, float] = {}
        for level, threshold in sorted_levels:
            total = sum(
                energy_forecast[i]
                for i in range(horizon)
                if price_forecast[i] <= threshold
            )
            attrs[level] = round(total, 3)

        current_price = price_forecast[0]
        state_val = 0.0
        for level, threshold in sorted_levels:
            if current_price <= threshold:
                state_val = attrs[level]
                break

        self._extra_attrs = attrs
        self._attr_native_value = round(state_val, 3)
        self._attr_available = True


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

    sun_intensity_sensor = SunIntensityPredictionSensor(
        hass=hass,
        name="Sun Intensity Prediction",
        unique_id=f"{entry.entry_id}_sun_intensity_prediction",
        device=device_info,
    )
    entities.append(sun_intensity_sensor)

    entities.append(
        CalculatedSupplyTemperatureSensor(
            hass=hass,
            name="Calculated Supply Temperature",
            unique_id=f"{entry.entry_id}_calculated_supply_temperature",
            outdoor_sensor=outdoor_sensor_entity,
            offset_entity="number.heating_curve_offset",
            device=device_info,
        )
    )

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
    outdoor_temp_sensor = entry.data.get(CONF_OUTDOOR_TEMPERATURE_SENSOR)
    power_sensor = entry.data.get(CONF_POWER_CONSUMPTION)
    k_factor = float(entry.data.get(CONF_K_FACTOR, DEFAULT_K_FACTOR))
    base_cop = float(entry.data.get(CONF_BASE_COP, DEFAULT_COP_AT_35))
    outdoor_temp_coefficient = float(
        entry.data.get(CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT)
    )
    glass_east = float(entry.data.get(CONF_GLASS_EAST_M2, 0))
    glass_west = float(entry.data.get(CONF_GLASS_WEST_M2, 0))
    glass_south = float(entry.data.get(CONF_GLASS_SOUTH_M2, 0))
    glass_u = float(entry.data.get(CONF_GLASS_U_VALUE, 1.2))
    planning_window = int(entry.data.get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW))
    time_base = int(entry.data.get(CONF_TIME_BASE, DEFAULT_TIME_BASE))

    price_sensor = entry.data.get(CONF_PRICE_SENSOR)
    consumption_price_entity = None
    if price_sensor:
        consumption_price_entity = CurrentElectricityPriceSensor(
            hass=hass,
            name="Current Consumption Price",
            unique_id=f"{entry.entry_id}_current_consumption_price",
            price_sensor=price_sensor,
            source_type=SOURCE_TYPE_CONSUMPTION,
            price_settings=price_settings,
            icon="mdi:transmission-tower-import",
            device=price_device_info,
        )
        entities.append(consumption_price_entity)
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
            outdoor_sensor=outdoor_temp_sensor,
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

    forecast_entity = None
    if consumption_sources or production_sources:
        forecast_entity = EnergyConsumptionForecastSensor(
            hass=hass,
            name="Expected Energy Consumption",
            unique_id=f"{entry.entry_id}_expected_energy_consumption",
            consumption_sensors=consumption_sources,
            production_sensors=production_sources,
            heatpump_sensor=power_sensor,
            icon="mdi:flash-clock",
            device=device_info,
        )
        entities.append(forecast_entity)
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

    if price_sensor and forecast_entity and consumption_price_entity:
        price_levels = {
            k: float(v) for k, v in price_settings.items() if k != CONF_PRICE_SENSOR
        }
        if price_levels:
            entities.append(
                EnergyPriceLevelSensor(
                    hass=hass,
                    name="Available Energy by Price",
                    unique_id=f"{entry.entry_id}_available_energy_by_price",
                    price_sensor=f"sensor.{consumption_price_entity.translation_key}",
                    forecast_sensor=f"sensor.{forecast_entity.translation_key}",
                    price_levels=price_levels,
                    icon="mdi:scale-balance",
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
            outdoor_sensor=outdoor_temp_sensor,
        )
        entities.append(net_heat_sensor)

    cop_sensor_entity = None
    if supply_temp_sensor:
        cop_sensor_entity = QuadraticCopSensor(
            hass=hass,
            name="Heat Pump COP",
            unique_id=f"{entry.entry_id}_cop",
            supply_sensor=supply_temp_sensor,
            outdoor_sensor=outdoor_temp_sensor,
            k_factor=k_factor,
            base_cop=base_cop,
            outdoor_temp_coefficient=outdoor_temp_coefficient,
            device=device_info,
        )
        entities.append(cop_sensor_entity)
        if power_sensor:
            entities.append(
                HeatPumpThermalPowerSensor(
                    hass=hass,
                    name="Heat Pump Thermal Power",
                    unique_id=f"{entry.entry_id}_thermal_power",
                    power_sensor=power_sensor,
                    supply_sensor=supply_temp_sensor,
                    outdoor_sensor=outdoor_temp_sensor
                    or outdoor_sensor_entity.entity_id,
                    device=device_info,
                    k_factor=k_factor,
                    base_cop=base_cop,
                )
            )

    if heat_loss_sensor or window_gain_sensor or price_sensor or cop_sensor_entity:
        entities.append(
            DiagnosticsSensor(
                hass=hass,
                name="Optimizer Diagnostics",
                unique_id=f"{entry.entry_id}_diagnostics",
                heat_loss_sensor=heat_loss_sensor,
                window_gain_sensor=window_gain_sensor,
                price_sensor=price_sensor,
                cop_sensor=cop_sensor_entity,
                device=device_info,
                planning_window=planning_window,
                time_base=time_base,
            )
        )

    heating_curve_offset_sensor = None
    if net_heat_sensor and price_sensor:
        heating_curve_offset_sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id=f"{entry.entry_id}_heating_curve_offset",
            net_heat_sensor=net_heat_sensor,
            price_sensor=price_sensor,
            device=device_info,
            k_factor=k_factor,
        )
        entities.append(heating_curve_offset_sensor)

    if heating_curve_offset_sensor is not None:
        entities.append(
            OptimizedSupplyTemperatureSensor(
                hass=hass,
                name="Optimized Supply Temperature",
                unique_id=f"{entry.entry_id}_optimized_supply_temperature",
                outdoor_sensor=outdoor_sensor_entity,
                offset_entity=heating_curve_offset_sensor,
                device=device_info,
                k_factor=k_factor,
                planning_window=planning_window,
                time_base=time_base,
            )
        )

    if entities:
        async_add_entities(entities, True)

    hass.data[DOMAIN]["entities"] = {ent.entity_id: ent for ent in entities}
