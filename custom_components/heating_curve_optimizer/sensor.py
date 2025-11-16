from __future__ import annotations

import asyncio
from functools import partial
import logging
import math
from collections.abc import Iterable
from typing import Any, cast
from datetime import datetime, timedelta

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pycares  # noqa: F401
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
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
    CONF_K_FACTOR,
    CONF_BASE_COP,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_POWER_CONSUMPTION,
    CONF_PRICE_SENSOR,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_PLANNING_WINDOW,
    CONF_TIME_BASE,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_HEATING_CURVE_OFFSET,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_PV_EAST_WP,
    CONF_PV_SOUTH_WP,
    CONF_PV_WEST_WP,
    CONF_PV_TILT,
    DEFAULT_COP_AT_35,
    DEFAULT_K_FACTOR,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_THERMAL_STORAGE_EFFICIENCY,
    DEFAULT_HEATING_CURVE_OFFSET,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_TIME_BASE,
    DEFAULT_PV_TILT,
    DOMAIN,
    INDOOR_TEMPERATURE,
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
    calculate_htc_from_energy_label,
)
from .entity import BaseUtilitySensor

# Create a persistent pycares channel to ensure the library's background
# thread is started before test fixtures snapshot running threads. Without
# this, the thread may be flagged as lingering during teardown.
_PYCARES_CHANNEL = pycares.Channel()

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


def _coerce_time_base(value: Any) -> int | None:
    """Return a positive integer time-base in minutes if possible."""

    try:
        base = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(base) or base <= 0:
        return None
    return int(round(base))


def _normalize_price_value(value: Any) -> float | None:
    """Normalize a raw price value to a float if possible."""

    if isinstance(value, dict):
        value = value.get("value")

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_price_forecast(state: State) -> list[float]:
    """Extract an hourly price forecast from a Home Assistant price state."""

    forecast_attr = state.attributes.get("forecast_prices")
    if isinstance(forecast_attr, (list, tuple)):
        forecast: list[float] = []
        for entry in forecast_attr:
            price = _normalize_price_value(entry)
            if price is not None:
                forecast.append(price)
        if forecast:
            return forecast

    generic_forecast = state.attributes.get("forecast")
    if isinstance(generic_forecast, (list, tuple)):
        forecast = []
        for entry in generic_forecast:
            price = _normalize_price_value(entry)
            if price is not None:
                forecast.append(price)
        if forecast:
            return forecast

    from homeassistant.util import dt as dt_util

    now = dt_util.utcnow()

    interval_forecast: list[float] = []

    def _extend_interval_forecast(entries: Any, *, skip_past: bool = False) -> bool:
        if not isinstance(entries, (list, tuple)):
            return False

        added = False
        for entry in entries:
            if skip_past and isinstance(entry, dict):
                start = entry.get("start") or entry.get("from")
                if isinstance(start, str):
                    start_dt = dt_util.parse_datetime(start)
                    if start_dt is not None:
                        start_dt = dt_util.as_utc(start_dt)
                        if start_dt < now:
                            continue

            price = _normalize_price_value(entry)
            if price is not None:
                interval_forecast.append(price)
                added = True
        return added

    _extend_interval_forecast(state.attributes.get("net_prices_today"), skip_past=True)
    _extend_interval_forecast(state.attributes.get("net_prices_tomorrow"))

    if interval_forecast:
        return interval_forecast

    hour = now.hour

    forecast: list[float] = []
    raw_today = state.attributes.get("raw_today")
    if isinstance(raw_today, list):
        for entry in raw_today[hour:]:
            price = _normalize_price_value(entry)
            if price is not None:
                forecast.append(price)

    raw_tomorrow = state.attributes.get("raw_tomorrow")
    if isinstance(raw_tomorrow, list):
        for entry in raw_tomorrow:
            price = _normalize_price_value(entry)
            if price is not None:
                forecast.append(price)

    if forecast:
        return forecast

    combined: list[Any] = []
    for key in ("today", "tomorrow"):
        attr = state.attributes.get(key)
        if isinstance(attr, list):
            combined.extend(attr)

    for entry in combined:
        price = _normalize_price_value(entry)
        if price is not None:
            forecast.append(price)

    if forecast:
        return forecast

    try:
        price = float(state.state)
    except (TypeError, ValueError):
        return []

    return [price]


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
            translation_key=name.lower().replace(" ", "_").replace(".", "_"),
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

    async def _fetch_weather(self) -> tuple[float, list[float], list[float]]:
        _LOGGER.debug("Fetching weather for %.4f, %.4f", self.latitude, self.longitude)

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m,relative_humidity_2m&current_weather=true&timezone=UTC"
        )
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error fetching weather data: %s", err)
            self._attr_available = False
            return 0.0, [], []
        self._attr_available = True

        current = float(data.get("current_weather", {}).get("temperature", 0))

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        values = hourly.get("temperature_2m", [])
        humidity_values = hourly.get("relative_humidity_2m", [])

        if not times or not values:
            return current, [], []

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
        humidity = (
            [float(v) for v in humidity_values[start_idx : start_idx + 24]]
            if humidity_values
            else []
        )
        _LOGGER.debug(
            "Weather data current=%s forecast=%s humidity=%s", current, temps, humidity
        )
        return current, temps, humidity

    async def async_update(self):
        current, forecast, humidity = await self._fetch_weather()
        if not self._attr_available:
            return
        self._attr_native_value = round(current, 2)
        self._extra_attrs = {
            "forecast": [round(v, 2) for v in forecast],
            "humidity_forecast": [round(v, 1) for v in humidity] if humidity else [],
            "forecast_time_base": 60,
        }

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
        self._extra_attrs: dict[str, Any] = {}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._extra_attrs

    async def async_update(self):
        state = self.hass.states.get(self.price_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            self._attr_available = False
            self._extra_attrs = {}
            _LOGGER.warning("Price sensor %s is unavailable", self.price_sensor)
            return
        try:
            base_price = float(state.state)
        except ValueError:
            self._attr_available = False
            self._extra_attrs = {}
            _LOGGER.warning("Price sensor %s has invalid state", self.price_sensor)
            return
        self._attr_available = True

        self._attr_native_value = round(base_price, 8)
        attrs: dict[str, Any] = dict(state.attributes)
        forecast = extract_price_forecast(state)
        if forecast:
            attrs["forecast_prices"] = forecast
        self._extra_attrs = attrs

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
        outdoor_sensor: str | SensorEntity,
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

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _compute_value(self) -> None:
        entity_id = (
            self.outdoor_sensor.entity_id
            if isinstance(self.outdoor_sensor, SensorEntity)
            else cast(str, self.outdoor_sensor)
        )
        sensor_name = entity_id or str(self.outdoor_sensor)

        if entity_id is None:
            self._set_unavailable("geen buitensensor gevonden")
            return

        state = self.hass.states.get(entity_id)
        if (
            isinstance(self.outdoor_sensor, SensorEntity)
            and self.outdoor_sensor.entity_id
        ):
            self.outdoor_sensor = self.outdoor_sensor.entity_id
            entity_id = cast(str, self.outdoor_sensor)
            sensor_name = entity_id

        if state is None:
            self._set_unavailable(f"geen buitensensor gevonden ({sensor_name})")
            return
        if state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"buitensensor {sensor_name} heeft status '{state.state}'"
            )
            return
        try:
            current = float(state.state)
        except ValueError:
            self._set_unavailable(f"waarde van buitensensor {sensor_name} is ongeldig")
            return
        forecast = [
            float(v)
            for v in state.attributes.get("forecast", [])
            if isinstance(v, (int, float, str))
        ]
        self._mark_available()

        # Calculate HTC (Heat Transfer Coefficient) from energy label
        # This properly converts energy label to building heat loss rate
        htc = calculate_htc_from_energy_label(self.energy_label, self.area_m2)

        indoor = INDOOR_TEMPERATURE
        if self.indoor_sensor:
            state = self.hass.states.get(self.indoor_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    indoor = float(state.state)
                except ValueError:
                    indoor = INDOOR_TEMPERATURE

        # Heat loss (kW) = HTC (W/K) × ΔT (K) / 1000
        delta_t = indoor - current
        q_loss = htc * delta_t / 1000.0

        _LOGGER.debug(
            "Heat loss calculation: label=%s area=%.2f HTC=%.1f W/K indoor=%.2f outdoor=%.2f ΔT=%.2f q_loss=%.3f kW",
            self.energy_label,
            self.area_m2,
            htc,
            indoor,
            current,
            delta_t,
            q_loss,
        )
        self._attr_native_value = round(q_loss, 3)

        # Calculate forecast values using HTC
        forecast_values = [round(htc * (indoor - t) / 1000.0, 3) for t in forecast]
        self._extra_attrs = {
            "forecast": forecast_values,
            "forecast_time_base": 60,
            "htc_w_per_k": round(htc, 1),
            "energy_label": self.energy_label,
            "calculation_method": "HTC from energy label (NTA 8800)",
        }

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if isinstance(self.outdoor_sensor, SensorEntity):
            self.outdoor_sensor = self.outdoor_sensor.entity_id


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
        self._radiation_history: list[float] = []

    async def _fetch_radiation(self) -> list[float]:
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
        rad_forecast = [round(v, 2) for v in rad]
        if rad:
            current = rad[0] * fac * orient_factors * total_area / self.u_value / 1000.0
            forecast = [
                round(v * fac * orient_factors * total_area / self.u_value / 1000.0, 3)
                for v in rad
            ]
        else:
            current = 0.0
            forecast = []

        self._radiation_history.append(rad[0] if rad else 0.0)
        if len(self._radiation_history) > 24:
            self._radiation_history = self._radiation_history[-24:]
        _LOGGER.debug("Solar gain current=%s forecast=%s", current, forecast)
        self._attr_available = True
        self._attr_native_value = round(current, 3)
        self._extra_attrs = {
            "forecast": forecast,
            "radiation_forecast": rad_forecast,
            "radiation_history": [round(v, 2) for v in self._radiation_history],
            "forecast_time_base": 60,
            "radiation_forecast_time_base": 60,
        }

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()


class PVProductionForecastSensor(BaseUtilitySensor):
    """Sensor that calculates PV production forecast from solar radiation and Wp parameters."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        east_wp: float,
        west_wp: float,
        south_wp: float,
        tilt: float,
        icon: str,
        device: DeviceInfo,
    ):
        """Initialize the PV production forecast sensor.

        Args:
            hass: Home Assistant instance
            name: Sensor name
            unique_id: Unique identifier
            east_wp: East-facing PV capacity in Watt-peak
            west_wp: West-facing PV capacity in Watt-peak
            south_wp: South-facing PV capacity in Watt-peak
            tilt: Panel tilt angle in degrees (0=horizontal, 90=vertical)
            icon: Icon to use
            device: Device info
        """
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
        self.east_wp = east_wp
        self.west_wp = west_wp
        self.south_wp = south_wp
        self.tilt = tilt
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = async_get_clientsession(hass)
        self._extra_attrs: dict[str, list[float] | float] = {}
        self._radiation_history: list[float] = []

    async def _fetch_radiation(self) -> list[float]:
        """Fetch solar radiation forecast from open-meteo API."""
        _LOGGER.debug(
            "Fetching radiation for PV production at %.4f, %.4f",
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
            _LOGGER.error("Error fetching radiation data for PV forecast: %s", err)
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
        _LOGGER.debug("PV radiation forecast=%s", values_list)
        return values_list

    def _orientation_factor(self, azimuth: float, orientation: float) -> float:
        """Calculate orientation factor based on sun azimuth and panel orientation.

        Args:
            azimuth: Sun azimuth angle in degrees
            orientation: Panel orientation (90=east, 180=south, 270=west)

        Returns:
            Factor between 0 and 1
        """
        diff = abs(azimuth - orientation)
        if diff > 180:
            diff = 360 - diff
        return max(math.cos(math.radians(diff)), 0)

    def _tilt_factor(self, elevation: float) -> float:
        """Calculate tilt factor based on sun elevation and panel tilt.

        For optimal energy capture, panels should be perpendicular to sun rays.
        This is simplified - real calculation would consider both elevation and azimuth.

        Args:
            elevation: Sun elevation angle in degrees

        Returns:
            Factor between 0 and 1
        """
        # Optimal panel angle is 90° - elevation
        # Tilt factor = cos(elevation - (90 - tilt))
        optimal_angle = 90 - elevation
        angle_diff = abs(self.tilt - optimal_angle)
        return max(math.cos(math.radians(angle_diff)), 0)

    @property
    def extra_state_attributes(self) -> dict[str, list[float] | float]:
        """Return extra state attributes."""
        return self._extra_attrs

    async def _compute_value(self) -> None:
        """Compute PV production from radiation forecast."""
        rad = await self._fetch_radiation()
        if not self._attr_available:
            return

        _LOGGER.debug("PV production raw radiation=%s", rad)

        # Get sun position
        sun = self.hass.states.get("sun.sun")
        az = float(sun.attributes.get("azimuth", 0)) if sun else 180.0
        elev = float(sun.attributes.get("elevation", 0)) if sun else 30.0

        # Calculate weighted orientation factor
        total_wp = self.east_wp + self.west_wp + self.south_wp
        if total_wp == 0:
            # No PV capacity configured
            self._attr_native_value = 0.0
            self._attr_available = True
            self._extra_attrs = {
                "forecast": [],
                "radiation_forecast": [],
                "forecast_time_base": 60,
            }
            return

        orient_factors = (
            self._orientation_factor(az, 90) * self.east_wp
            + self._orientation_factor(az, 270) * self.west_wp
            + self._orientation_factor(az, 180) * self.south_wp
        ) / total_wp

        # Calculate tilt factor
        tilt_fac = self._tilt_factor(elev)

        # Standard test conditions: 1000 W/m²
        # PV power = (radiation / 1000) × Wp × orientation_factor × tilt_factor / 1000 kW
        # Simplified: radiation × Wp × factors / 1,000,000
        rad_forecast = [round(v, 2) for v in rad]
        if rad:
            # Current production
            current = rad[0] * total_wp * orient_factors * tilt_fac / 1_000_000.0  # kW

            # Forecast production (use average factors - could be improved with hourly sun position)
            forecast = [
                round(v * total_wp * orient_factors * tilt_fac / 1_000_000.0, 3)
                for v in rad
            ]
        else:
            current = 0.0
            forecast = []

        self._radiation_history.append(rad[0] if rad else 0.0)
        if len(self._radiation_history) > 24:
            self._radiation_history = self._radiation_history[-24:]

        _LOGGER.debug("PV production current=%s kW forecast=%s", current, forecast)
        self._attr_available = True
        self._attr_native_value = round(current, 3)
        self._extra_attrs = {
            "forecast": forecast,
            "radiation_forecast": rad_forecast,
            "radiation_history": [round(v, 2) for v in self._radiation_history],
            "forecast_time_base": 60,
            "total_pv_capacity_wp": total_wp,
            "east_wp": self.east_wp,
            "west_wp": self.west_wp,
            "south_wp": self.south_wp,
            "tilt_degrees": self.tilt,
        }

    async def async_update(self):
        """Update sensor state."""
        await self._compute_value()

    async def async_added_to_hass(self):
        """Handle entity added to hass."""
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """Handle entity removal."""
        await super().async_will_remove_from_hass()


class NetHeatLossSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
        heat_loss_sensor: HeatLossSensor | None = None,
        window_gain_sensor: WindowSolarGainSensor | None = None,
        *,
        config_entry_id: str | None = None,
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
        self.heat_loss_sensor = heat_loss_sensor
        self.window_gain_sensor = window_gain_sensor
        self._extra_attrs: dict[str, list[float]] = {}
        self._config_entry_id = config_entry_id

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _compute_value(self) -> None:
        # Get heat loss from HeatLossSensor
        q_loss = 0.0
        if self.heat_loss_sensor:
            try:
                q_loss = float(self.heat_loss_sensor.native_value or 0.0)
            except (ValueError, TypeError):
                q_loss = 0.0

        # Get solar gain from WindowSolarGainSensor
        q_solar = 0.0
        if self.window_gain_sensor:
            try:
                q_solar = float(self.window_gain_sensor.native_value or 0.0)
            except (ValueError, TypeError):
                q_solar = 0.0

        # Calculate net heat loss
        self._mark_available()
        q_net = q_loss - q_solar
        _LOGGER.debug("Net heat loss: loss=%s solar=%s net=%s", q_loss, q_solar, q_net)
        self._attr_native_value = round(q_net, 3)

        loss_fc: list[Any] = []
        gain_fc: list[Any] = []
        loss_base = None
        gain_base = None
        if self.heat_loss_sensor:
            hl_attrs = getattr(self.heat_loss_sensor, "extra_state_attributes", {})
            if isinstance(hl_attrs, dict):
                loss_fc = hl_attrs.get("forecast", []) or []
                loss_base = _coerce_time_base(hl_attrs.get("forecast_time_base"))
        if self.window_gain_sensor:
            wg_attrs = getattr(self.window_gain_sensor, "extra_state_attributes", {})
            if isinstance(wg_attrs, dict):
                gain_fc = wg_attrs.get("forecast", []) or []
                gain_base = _coerce_time_base(wg_attrs.get("forecast_time_base"))

        n = max(len(loss_fc), len(gain_fc))
        forecast = []
        for i in range(n):
            lf = float(loss_fc[i]) if i < len(loss_fc) else 0.0
            gf = float(gain_fc[i]) if i < len(gain_fc) else 0.0
            forecast.append(round(lf - gf, 3))

        forecast_base = loss_base or gain_base or 60
        base_sources: dict[str, int] = {}
        if loss_base:
            base_sources["heat_loss"] = loss_base
        if gain_base:
            base_sources["window_gain"] = gain_base
        if loss_base and gain_base and loss_base != gain_base:
            _LOGGER.warning(
                "Time-base mismatch between heat loss (%s min) and window gain (%s min)",
                loss_base,
                gain_base,
            )

        attrs: dict[str, Any] = {
            "forecast": forecast,
            "forecast_time_base": forecast_base,
        }
        if base_sources:
            attrs["forecast_time_base_sources"] = base_sources

        self._extra_attrs = attrs

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self.heat_loss_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self.heat_loss_sensor.entity_id,
                    self._handle_change,
                )
            )
        if self.window_gain_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self.window_gain_sensor.entity_id,
                    self._handle_change,
                )
            )
        if self._config_entry_id:
            runtime = self.hass.data.setdefault(DOMAIN, {}).setdefault("runtime", {})
            entry_data = runtime.setdefault(self._config_entry_id, {})
            entry_data["net_heat_entity"] = self.entity_id
            entry_data["net_heat_unique_id"] = self.unique_id
            entry_data.setdefault("net_heat_sensor_object", self)

    async def _handle_change(self, event):
        await self._compute_value()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        if self._config_entry_id:
            runtime = self.hass.data.get(DOMAIN, {}).get("runtime")
            if runtime:
                entry_data = runtime.get(self._config_entry_id)
                if entry_data:
                    if entry_data.get("net_heat_entity") == self.entity_id:
                        entry_data.pop("net_heat_entity", None)
                    if entry_data.get("net_heat_unique_id") == self.unique_id:
                        entry_data.pop("net_heat_unique_id", None)
                    if entry_data.get("net_heat_sensor_object") is self:
                        entry_data.pop("net_heat_sensor_object", None)
                    if not entry_data:
                        runtime.pop(self._config_entry_id, None)
                if not runtime:
                    domain_data = self.hass.data.get(DOMAIN)
                    if domain_data is not None:
                        domain_data.pop("runtime", None)
        await super().async_will_remove_from_hass()


class QuadraticCopSensor(BaseUtilitySensor):
    """COP sensor using an empirical formula."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        supply_sensor: str,
        device: DeviceInfo,
        outdoor_sensor: str | SensorEntity,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        cop_compensation_factor: float = 1.0,
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
        self.cop_compensation_factor = cop_compensation_factor

    async def async_update(self):
        s_state = self.hass.states.get(self.supply_sensor)
        if s_state is None:
            self._set_unavailable(
                f"aanvoersensor {self.supply_sensor} werd niet gevonden"
            )
            return
        if s_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"aanvoersensor {self.supply_sensor} heeft status '{s_state.state}'"
            )
            return
        try:
            s_temp = float(s_state.state)
        except ValueError:
            self._set_unavailable(
                f"waarde van aanvoersensor {self.supply_sensor} is ongeldig"
            )
            return

        entity_id = (
            self.outdoor_sensor.entity_id
            if isinstance(self.outdoor_sensor, SensorEntity)
            else cast(str, self.outdoor_sensor)
        )
        sensor_name = entity_id or str(self.outdoor_sensor)

        if entity_id is None:
            self._set_unavailable("geen buitensensor gevonden")
            return

        o_state = self.hass.states.get(entity_id)
        if (
            isinstance(self.outdoor_sensor, SensorEntity)
            and self.outdoor_sensor.entity_id
        ):
            self.outdoor_sensor = self.outdoor_sensor.entity_id
            entity_id = cast(str, self.outdoor_sensor)
            sensor_name = entity_id

        if o_state is None:
            self._set_unavailable(f"geen buitensensor gevonden ({sensor_name})")
            return
        if o_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"buitensensor {sensor_name} heeft status '{o_state.state}'"
            )
            return
        try:
            o_temp = float(o_state.state)
        except ValueError:
            self._set_unavailable(f"waarde van buitensensor {sensor_name} is ongeldig")
            return

        # Calculate base COP
        cop_base = (
            self.base_cop
            + self.outdoor_temp_coefficient * o_temp
            - self.k_factor * (s_temp - 35)
        ) * self.cop_compensation_factor

        # Apply defrost factor if outdoor sensor has humidity data
        # Otherwise use default 80% humidity (typical for Dutch maritime climate)
        humidity = 80.0
        if o_state.attributes and "humidity_forecast" in o_state.attributes:
            humidity_list = o_state.attributes.get("humidity_forecast", [])
            if humidity_list and len(humidity_list) > 0:
                humidity = float(humidity_list[0])  # Use current hour humidity

        defrost_factor = _calculate_defrost_factor(o_temp, humidity)
        cop = cop_base * defrost_factor

        _LOGGER.debug(
            "Calculated COP: supply=%s outdoor=%s humidity=%.0f%% base_cop=%.2f defrost_factor=%.3f final_cop=%.2f",
            s_temp,
            o_temp,
            humidity,
            cop_base,
            defrost_factor,
            cop,
        )
        self._mark_available()
        self._attr_native_value = round(cop, 3)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if isinstance(self.outdoor_sensor, SensorEntity):
            self.outdoor_sensor = self.outdoor_sensor.entity_id


class HeatPumpThermalPowerSensor(BaseUtilitySensor):
    """Calculate current thermal output of the heat pump."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        power_sensor: str,
        supply_sensor: str,
        outdoor_sensor: str | SensorEntity,
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
        if p_state is None:
            self._set_unavailable(
                f"vermogenssensor {self.power_sensor} werd niet gevonden"
            )
            return
        if p_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"vermogenssensor {self.power_sensor} heeft status '{p_state.state}'"
            )
            return

        s_state = self.hass.states.get(self.supply_sensor)
        if s_state is None:
            self._set_unavailable(
                f"aanvoersensor {self.supply_sensor} werd niet gevonden"
            )
            return
        if s_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"aanvoersensor {self.supply_sensor} heeft status '{s_state.state}'"
            )
            return

        entity_id = (
            self.outdoor_sensor.entity_id
            if isinstance(self.outdoor_sensor, SensorEntity)
            else cast(str, self.outdoor_sensor)
        )
        sensor_name = entity_id or str(self.outdoor_sensor)

        if entity_id is None:
            self._set_unavailable("geen buitensensor gevonden")
            return

        o_state = self.hass.states.get(entity_id)
        if (
            isinstance(self.outdoor_sensor, SensorEntity)
            and self.outdoor_sensor.entity_id
        ):
            self.outdoor_sensor = self.outdoor_sensor.entity_id
            entity_id = cast(str, self.outdoor_sensor)
            sensor_name = entity_id

        if o_state is None:
            self._set_unavailable(f"geen buitensensor gevonden ({sensor_name})")
            return
        if o_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"buitensensor {sensor_name} heeft status '{o_state.state}'"
            )
            return

        try:
            power = float(p_state.state)
        except ValueError:
            self._set_unavailable(
                f"waarde van vermogenssensor {self.power_sensor} is ongeldig"
            )
            return
        try:
            s_temp = float(s_state.state)
        except ValueError:
            self._set_unavailable(
                f"waarde van aanvoersensor {self.supply_sensor} is ongeldig"
            )
            return
        try:
            o_temp = float(o_state.state)
        except ValueError:
            self._set_unavailable(f"waarde van buitensensor {sensor_name} is ongeldig")
            return
        cop = self.base_cop + 0.08 * o_temp - self.k_factor * (s_temp - 35)
        thermal_power = power * cop / 1000.0
        _LOGGER.debug(
            "Thermal power calc power=%s cop=%s -> %s",
            power,
            cop,
            thermal_power,
        )
        self._mark_available()
        self._attr_native_value = round(thermal_power, 3)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if isinstance(self.outdoor_sensor, SensorEntity):
            self.outdoor_sensor = self.outdoor_sensor.entity_id


# New sensor classes start here


class CopEfficiencyDeltaSensor(BaseUtilitySensor):
    """Predict COP deltas for future offsets."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        *,
        cop_sensor: str | SensorEntity,
        offset_entity: str | SensorEntity,
        outdoor_sensor: str | SensorEntity,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        cop_compensation_factor: float = 1.0,
    ) -> None:
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
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.cop_sensor = cop_sensor
        self.offset_entity = offset_entity
        self.outdoor_sensor = outdoor_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.cop_compensation_factor = cop_compensation_factor
        self._extra_attrs: dict[str, list[float] | float] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float] | float]:
        return self._extra_attrs

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for ent in (
            self._resolve_entity_id(self.cop_sensor),
            self._resolve_entity_id(self.offset_entity),
            self._resolve_entity_id(self.outdoor_sensor),
        ):
            if ent is None:
                continue
            self.async_on_remove(
                async_track_state_change_event(self.hass, ent, self._handle_change)
            )

    async def _handle_change(self, event):  # pragma: no cover - simple callback
        await self.async_update()
        self.async_write_ha_state()

    def _resolve_entity_id(self, entity_ref: str | SensorEntity) -> str | None:
        """Return the entity_id for a reference or None if unavailable."""

        if isinstance(entity_ref, SensorEntity):
            entity_id = entity_ref.entity_id
            if entity_id is not None:
                if entity_ref is self.cop_sensor:
                    self.cop_sensor = entity_id
                elif entity_ref is self.offset_entity:
                    self.offset_entity = entity_id
                elif entity_ref is self.outdoor_sensor:
                    self.outdoor_sensor = entity_id
            return entity_id
        return cast(str, entity_ref)

    def _get_state(self, entity_ref: str | SensorEntity) -> State | None:
        """Return hass state for the given entity reference."""

        entity_id = self._resolve_entity_id(entity_ref)
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return state

    async def async_update(self):
        cop_state = self._get_state(self.cop_sensor)
        offset_state = self._get_state(self.offset_entity)
        outdoor_state = self._get_state(self.outdoor_sensor)
        if (
            cop_state is None
            or offset_state is None
            or outdoor_state is None
            or cop_state.state in ("unknown", "unavailable")
            or outdoor_state.state in ("unknown", "unavailable")
        ):
            self._attr_available = False
            return
        try:
            reference_cop = float(cop_state.state)
            outdoor_temp = float(outdoor_state.state)
        except ValueError:
            self._attr_available = False
            return

        supply_temps = offset_state.attributes.get("future_supply_temperatures")
        if not supply_temps:
            self._attr_available = False
            return

        predicted_cops = [
            (
                self.base_cop
                + self.outdoor_temp_coefficient * outdoor_temp
                - self.k_factor * (float(s_temp) - 35)
            )
            * self.cop_compensation_factor
            for s_temp in supply_temps
        ]
        cop_deltas = [round(c - reference_cop, 3) for c in predicted_cops]
        self._extra_attrs = {
            "future_cop": [round(c, 3) for c in predicted_cops],
            "cop_deltas": cop_deltas,
            "reference_cop": round(reference_cop, 3),
        }
        self._attr_native_value = cop_deltas[0] if cop_deltas else 0.0
        self._attr_available = True


class HeatGenerationDeltaSensor(BaseUtilitySensor):
    """Predict heat generation deltas based on future COPs."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        *,
        thermal_power_sensor: str | SensorEntity,
        cop_sensor: str | SensorEntity,
        offset_entity: str | SensorEntity,
        outdoor_sensor: str | SensorEntity,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        cop_compensation_factor: float = 1.0,
    ) -> None:
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
        self.thermal_power_sensor = thermal_power_sensor
        self.cop_sensor = cop_sensor
        self.offset_entity = offset_entity
        self.outdoor_sensor = outdoor_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.cop_compensation_factor = cop_compensation_factor
        self._extra_attrs: dict[str, list[float] | float] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float] | float]:
        return self._extra_attrs

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for ent in (
            self._resolve_entity_id(self.thermal_power_sensor),
            self._resolve_entity_id(self.cop_sensor),
            self._resolve_entity_id(self.offset_entity),
            self._resolve_entity_id(self.outdoor_sensor),
        ):
            if ent is None:
                continue
            self.async_on_remove(
                async_track_state_change_event(self.hass, ent, self._handle_change)
            )

    async def _handle_change(self, event):  # pragma: no cover - simple callback
        await self.async_update()
        self.async_write_ha_state()

    def _resolve_entity_id(self, entity_ref: str | SensorEntity) -> str | None:
        """Return the entity_id for a reference or None if unavailable."""

        if isinstance(entity_ref, SensorEntity):
            entity_id = entity_ref.entity_id
            if entity_id is not None:
                if entity_ref is self.thermal_power_sensor:
                    self.thermal_power_sensor = entity_id
                elif entity_ref is self.cop_sensor:
                    self.cop_sensor = entity_id
                elif entity_ref is self.offset_entity:
                    self.offset_entity = entity_id
                elif entity_ref is self.outdoor_sensor:
                    self.outdoor_sensor = entity_id
            return entity_id
        return cast(str, entity_ref)

    def _get_state(self, entity_ref: str | SensorEntity) -> State | None:
        """Return hass state for the given entity reference."""

        entity_id = self._resolve_entity_id(entity_ref)
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return state

    async def async_update(self):
        power_state = self._get_state(self.thermal_power_sensor)
        cop_state = self._get_state(self.cop_sensor)
        offset_state = self._get_state(self.offset_entity)
        outdoor_state = self._get_state(self.outdoor_sensor)
        if (
            power_state is None
            or cop_state is None
            or offset_state is None
            or outdoor_state is None
            or power_state.state in ("unknown", "unavailable")
            or cop_state.state in ("unknown", "unavailable")
            or outdoor_state.state in ("unknown", "unavailable")
        ):
            self._attr_available = False
            return
        try:
            reference_heat = float(power_state.state)
            reference_cop = float(cop_state.state)
            outdoor_temp = float(outdoor_state.state)
        except ValueError:
            self._attr_available = False
            return

        supply_temps = offset_state.attributes.get("future_supply_temperatures")
        if not supply_temps or reference_cop == 0:
            self._attr_available = False
            return

        predicted_cops = [
            (
                self.base_cop
                + self.outdoor_temp_coefficient * outdoor_temp
                - self.k_factor * (float(s_temp) - 35)
            )
            * self.cop_compensation_factor
            for s_temp in supply_temps
        ]
        predicted_heat = [reference_heat * (c / reference_cop) for c in predicted_cops]
        heat_deltas = [round(h - reference_heat, 3) for h in predicted_heat]
        self._extra_attrs = {
            "future_heat_generation": [round(h, 3) for h in predicted_heat],
            "heat_deltas": heat_deltas,
            "reference_heat_generation": round(reference_heat, 3),
        }
        self._attr_native_value = heat_deltas[0] if heat_deltas else 0.0
        self._attr_available = True


class CalculatedSupplyTemperatureSensor(BaseUtilitySensor):
    """Calculate target supply temperature based on the heating curve."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        *,
        outdoor_sensor: str | SensorEntity,
        entry: ConfigEntry,
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
        self._entry = entry

    async def async_added_to_hass(self) -> None:  # pragma: no cover - simple track
        await super().async_added_to_hass()
        if isinstance(self.outdoor_sensor, SensorEntity):
            self.outdoor_sensor = self.outdoor_sensor.entity_id
        # Track outdoor sensor for changes
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, cast(str, self.outdoor_sensor), self._handle_change
            )
        )

    async def _handle_change(self, event):  # pragma: no cover - simple callback
        await self.async_update()
        if self.entity_id:
            self.async_write_ha_state()

    def _resolve_entity_id(self, entity_ref: str | SensorEntity) -> str | None:
        """Return the entity_id for a reference or None if unavailable."""

        if isinstance(entity_ref, SensorEntity):
            return entity_ref.entity_id
        return cast(str, entity_ref)

    def _get_state(self, entity_ref: str | SensorEntity) -> State | None:
        """Return hass state for the given entity reference."""

        entity_id = self._resolve_entity_id(entity_ref)
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        if isinstance(entity_ref, SensorEntity) and entity_ref.entity_id is not None:
            if entity_ref is self.outdoor_sensor:
                self.outdoor_sensor = entity_ref.entity_id
        return state

    async def async_update(self) -> None:
        out_state = self._get_state(self.outdoor_sensor)

        if out_state is None or out_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            return
        try:
            outdoor = float(out_state.state)
        except ValueError:
            self._attr_available = False
            return

        # Get values from config entry
        try:
            offset = float(
                self._entry.options.get(
                    CONF_HEATING_CURVE_OFFSET,
                    self._entry.data.get(
                        CONF_HEATING_CURVE_OFFSET, DEFAULT_HEATING_CURVE_OFFSET
                    ),
                )
            )
            min_temp = float(
                self._entry.options.get(
                    CONF_HEAT_CURVE_MIN,
                    self._entry.data.get(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN),
                )
            )
            max_temp = float(
                self._entry.options.get(
                    CONF_HEAT_CURVE_MAX,
                    self._entry.data.get(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX),
                )
            )
            outdoor_min = float(
                self._entry.options.get(
                    CONF_HEAT_CURVE_MIN_OUTDOOR,
                    self._entry.data.get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0),
                )
            )
            outdoor_max = float(
                self._entry.options.get(
                    CONF_HEAT_CURVE_MAX_OUTDOOR,
                    self._entry.data.get(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0),
                )
            )
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

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        *,
        outdoor_sensor: str | SensorEntity,
        entry: ConfigEntry,
        device: DeviceInfo,
        offset_entity: SensorEntity | None = None,
        k_factor: float = DEFAULT_K_FACTOR,
        planning_window: int = DEFAULT_PLANNING_WINDOW,
        time_base: int = DEFAULT_TIME_BASE,
    ) -> None:
        super().__init__(
            hass=hass,
            name=name,
            unique_id=unique_id,
            outdoor_sensor=outdoor_sensor,
            entry=entry,
            device=device,
        )
        self.offset_entity = offset_entity
        self.k_factor = k_factor
        self.planning_window = planning_window
        self.time_base = time_base
        self.steps = max(1, int(planning_window * 60 // time_base))
        self._extra_attrs: dict[str, Any] = {}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._extra_attrs

    async def async_update(self) -> None:
        await super().async_update()
        if not self._attr_available:
            self._extra_attrs = {}
            return

        offset_entity_id = (
            self.offset_entity.entity_id
            if isinstance(self.offset_entity, SensorEntity)
            else self.offset_entity
        )
        offset_state = (
            self.hass.states.get(cast(str, offset_entity_id))
            if offset_entity_id
            else None
        )

        if not offset_state:
            self._extra_attrs = {}
            return

        supply_temps = offset_state.attributes.get("future_supply_temperatures")
        if isinstance(supply_temps, list) and supply_temps:
            self._extra_attrs = {
                "future_supply_temperatures": supply_temps[: self.steps]
            }
            return

        self._extra_attrs = {}


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

    def _resample(
        self, data: list[float], source_base: int | None = None
    ) -> list[float]:
        result: list[float] = []
        base = source_base or 60
        for step in range(self.steps):
            idx = int(step * self.time_base / base)
            if idx < len(data):
                result.append(float(data[idx]))
            else:
                result.append(0.0)
        return result

    def _extract_prices(self, state) -> list[float]:
        prices = extract_price_forecast(state)
        result: list[float] = []
        for step in range(self.steps):
            hour_offset = int(step * self.time_base / 60)
            if hour_offset < len(prices):
                result.append(float(prices[hour_offset]))
            else:
                break

        if len(result) < self.steps:
            if prices:
                fallback = float(prices[-1])
            else:
                try:
                    fallback = float(state.state)
                except (TypeError, ValueError):
                    fallback = 0.0
            result.extend([fallback] * (self.steps - len(result)))

        return result

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
                    [float(v) for v in forecast],
                    _coerce_time_base(state.attributes.get("forecast_time_base")),
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
                    [float(v) for v in forecast],
                    _coerce_time_base(state.attributes.get("forecast_time_base")),
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


def _calculate_defrost_factor(outdoor_temp: float, humidity: float = 80.0) -> float:
    """Calculate COP degradation due to defrost cycles for air-source heat pumps.

    Based on research for air-source heat pumps in humid climates (like Netherlands).
    Frosting occurs when outdoor temperature is between -10°C and 6°C with sufficient humidity.
    The worst frosting occurs around 0-3°C with high humidity (70-90%).

    Args:
        outdoor_temp: Outdoor temperature in °C
        humidity: Relative humidity in % (default 80% for Dutch maritime climate)

    Returns:
        Multiplier (0.60-1.0) to apply to base COP accounting for defrost losses

    Research references:
    - Frosting occurs at 100% RH below 3.1°C, at 70% RH below 5.3°C
    - COP degradation: typical 10-15%, worst case up to 40%
    - Most critical range: 0-7°C in humid climates
    """
    # No frosting above 6°C - heat pump operates at full efficiency
    if outdoor_temp >= 6.0:
        return 1.0

    # No frosting below -10°C (air too dry, insufficient moisture to freeze)
    if outdoor_temp <= -10.0:
        return 1.0

    # Calculate humidity-dependent frosting threshold
    # At 100% RH: frosting starts at 3.1°C
    # At 70% RH: frosting starts at 5.3°C
    # Linear interpolation for other humidity levels
    frosting_threshold = 3.1 + (humidity - 100) * (5.3 - 3.1) / (70 - 100)
    frosting_threshold = max(min(frosting_threshold, 6.0), -10.0)

    # No frosting if temperature is above the humidity-dependent threshold
    if outdoor_temp >= frosting_threshold:
        return 1.0

    # Frosting zone: calculate defrost penalty
    if outdoor_temp >= 0:
        # Worst frosting zone: 0-3°C
        # COP loss increases as we approach 0-2°C
        if outdoor_temp <= 3:
            # Maximum penalty at 0-2°C: 15-40% depending on humidity
            base_penalty = 0.25  # 25% base COP loss in worst conditions
            temp_factor = (
                1.0 - (outdoor_temp / 3.0) * 0.4
            )  # Reduces penalty as temp increases
        else:
            # Moderate frosting zone: 3-6°C
            # Linear reduction in penalty from 3°C to frosting threshold
            base_penalty = 0.15  # 15% COP loss
            temp_factor = (frosting_threshold - outdoor_temp) / (
                frosting_threshold - 3.0
            )
    else:
        # Below freezing: -10 to 0°C
        # Moderate frosting, less severe than near-zero temperatures
        base_penalty = 0.12  # 12% COP loss
        temp_factor = (outdoor_temp + 10) / 10.0

    # Adjust for humidity (Dutch climate typically 75-90% RH in winter)
    # Higher humidity = more frost formation = worse COP degradation
    humidity_factor = min(1.0, max(0.5, humidity / 80.0))  # Normalized to 80% baseline

    # Calculate final defrost penalty
    defrost_penalty = base_penalty * temp_factor * humidity_factor

    # Return COP multiplier (1.0 = no loss, 0.6 = 40% loss in worst case)
    cop_multiplier = 1.0 - defrost_penalty

    _LOGGER.debug(
        "Defrost factor: T=%.1f°C, RH=%.0f%% -> multiplier=%.3f (%.0f%% COP loss)",
        outdoor_temp,
        humidity,
        cop_multiplier,
        defrost_penalty * 100,
    )

    return max(0.60, cop_multiplier)  # Minimum 60% efficiency (40% max loss)


def _optimize_offsets(
    demand: list[float],
    prices: list[float],
    *,
    base_temp: float = 35.0,
    k_factor: float = DEFAULT_K_FACTOR,
    cop_compensation_factor: float = 1.0,
    buffer: float = 0.0,
    water_min: float = 28.0,
    water_max: float = 45.0,
    outdoor_temps: list[float] | None = None,
    humidity_forecast: list[float] | None = None,
    outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    time_base: int = 60,
    outdoor_min: float = -20.0,
    outdoor_max: float = 15.0,
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
        "Optimizing offsets demand=%s prices=%s base=%s k=%s comp=%s buffer=%s outdoor_temps=%s humidity=%s",
        demand,
        prices,
        base_temp,
        k_factor,
        cop_compensation_factor,
        buffer,
        outdoor_temps,
        humidity_forecast,
    )
    horizon = min(len(demand), len(prices))
    if horizon == 0:
        return [], []

    # Prepare outdoor temperature and humidity data for defrost modeling
    # Use forecasts if available, otherwise use a default assumption
    outdoor_temps_data = outdoor_temps if outdoor_temps else [5.0] * horizon
    humidity_data = humidity_forecast if humidity_forecast else [80.0] * horizon

    # Ensure we have enough data points
    if len(outdoor_temps_data) < horizon:
        # Pad with the last value if needed
        last_temp = outdoor_temps_data[-1] if outdoor_temps_data else 5.0
        outdoor_temps_data = list(outdoor_temps_data) + [last_temp] * (
            horizon - len(outdoor_temps_data)
        )

    if len(humidity_data) < horizon:
        # Pad with default humidity
        humidity_data = list(humidity_data) + [80.0] * (horizon - len(humidity_data))

    # Calculate defrost factors for each time step
    defrost_factors = [
        _calculate_defrost_factor(outdoor_temps_data[t], humidity_data[t])
        for t in range(horizon)
    ]

    # Calculate base temperature for each forecast step based on outdoor temperature
    base_temps = [
        _calculate_supply_temperature(
            outdoor_temps_data[t],
            water_min=water_min,
            water_max=water_max,
            outdoor_min=outdoor_min,
            outdoor_max=outdoor_max,
        )
        for t in range(horizon)
    ]

    def _calculate_cop(offset: int, time_step: int) -> float:
        """Calculate COP for given offset and time step, including outdoor temp and defrost effects."""
        supply_temp = base_temps[time_step] + offset
        outdoor_temp = outdoor_temps_data[time_step]

        # COP formula: base + outdoor_effect - supply_temp_effect
        cop_base = (
            DEFAULT_COP_AT_35
            + outdoor_temp_coefficient * outdoor_temp
            - k_factor * (supply_temp - 35)
        ) * cop_compensation_factor

        # Apply defrost factor
        cop_adjusted = cop_base * defrost_factors[time_step]

        return max(0.5, cop_adjusted)  # Ensure COP doesn't go below 0.5

    # Check which offsets are allowed - must respect water_min/max for all forecast steps
    # An offset is allowed only if it keeps supply temp within bounds for ALL time steps
    allowed_offsets = []
    for o in range(-4, 5):
        if all(water_min <= base_temps[t] + o <= water_max for t in range(horizon)):
            allowed_offsets.append(o)
    if not allowed_offsets:
        return [0 for _ in range(horizon)], [buffer for _ in range(horizon)]

    target_sum = -int(round(buffer))

    # Calculate step duration in hours for buffer energy calculation
    step_hours = time_base / 60.0

    # dynamic programming table storing (cost, prev_offset, prev_sum, buffer_energy)
    # State: (time_step, offset) -> {cumulative_sum: (cost, prev_offset, prev_sum, buffer_kwh)}
    dp: list[dict[int, dict[int, tuple[float, int | None, int | None, float]]]] = [
        {} for _ in range(horizon)
    ]

    for off in allowed_offsets:
        cop = _calculate_cop(off, 0)
        cost = demand[0] * prices[0] / cop if cop > 0 else demand[0] * prices[0] * 10
        # Calculate initial buffer energy
        heat_demand = max(float(demand[0]), 0.0)
        buffer_kwh = (
            buffer + off * heat_demand * DEFAULT_THERMAL_STORAGE_EFFICIENCY * step_hours
        )
        # Only allow states with non-negative buffer
        if buffer_kwh >= 0:
            dp[0][off] = {off: (cost, None, None, buffer_kwh)}

    for t in range(1, horizon):
        for off in allowed_offsets:
            cop = _calculate_cop(off, t)
            step_cost = (
                demand[t] * prices[t] / cop if cop > 0 else demand[t] * prices[t] * 10
            )
            for prev_off, sums in dp[t - 1].items():
                if abs(off - prev_off) <= 1:
                    for prev_sum, (prev_cost, _, _, prev_buffer_kwh) in sums.items():
                        new_sum = prev_sum + off
                        total = prev_cost + step_cost
                        # Calculate new buffer energy
                        heat_demand = max(float(demand[t]), 0.0)
                        buffer_kwh = (
                            prev_buffer_kwh
                            + off
                            * heat_demand
                            * DEFAULT_THERMAL_STORAGE_EFFICIENCY
                            * step_hours
                        )
                        # Only allow states with non-negative buffer
                        if buffer_kwh >= 0:
                            dp[t].setdefault(off, {})
                            cur = dp[t][off].get(new_sum)
                            if cur is None or total < cur[0]:
                                dp[t][off][new_sum] = (
                                    total,
                                    prev_off,
                                    prev_sum,
                                    buffer_kwh,
                                )

    if not dp[horizon - 1]:
        return [0 for _ in range(horizon)], [buffer for _ in range(horizon)]

    best_off: int | None = None
    best_sum: int | None = None
    best_cost = math.inf
    for off, sums in dp[horizon - 1].items():
        for sum_off, (cost, _, _, _) in sums.items():
            if sum_off == target_sum and cost < best_cost:
                best_cost = cost
                best_off = off
                best_sum = sum_off
    if best_off is None:
        for off, sums in dp[horizon - 1].items():
            for sum_off, (cost, _, _, _) in sums.items():
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
        _, prev_off, prev_sum, _ = dp[t][last_off][last_sum]
        assert prev_off is not None and prev_sum is not None
        result[t - 1] = prev_off
        last_off = prev_off
        last_sum = prev_sum

    # Track cumulative offset sum (in °C) for constraint purposes
    # Note: This is NOT energy - it tracks how far we've deviated from base temperature
    # The actual thermal energy buffer is calculated separately by _calculate_buffer_energy()
    buffer_evolution: list[float] = []
    cur = buffer
    for off in result:
        cur += off
        buffer_evolution.append(cur)

    _LOGGER.debug(
        "Optimized offsets result=%s buffer_evolution=%s", result, buffer_evolution
    )

    return result, buffer_evolution


def _calculate_buffer_energy(
    offsets: list[int],
    demand: list[float],
    *,
    time_base: int,
) -> list[float]:
    """Convert offset evolution to stored thermal energy in kWh.

    Physical model:
    - When offset > 0: supply temperature is raised, building is overheated,
      excess thermal energy is stored in building's thermal mass
    - When offset < 0: supply temperature is lowered, building uses stored
      thermal energy from its thermal mass
    - Energy stored per time step = offset × demand × storage_efficiency × time

    Units verification:
    - offset: °C (temperature adjustment)
    - demand: kW (thermal power demand)
    - storage_efficiency: dimensionless (fraction of demand stored per °C)
    - time: hours
    - Result: °C × kW × (dimensionless) × hours = kWh ✓

    Args:
        offsets: Temperature offsets in °C for each time step
        demand: Heat demand in kW for each time step
        time_base: Time base in minutes per step

    Returns:
        Cumulative thermal energy buffer in kWh at each time step
    """
    if time_base <= 0:
        step_hours = 1.0
    else:
        step_hours = time_base / 60.0

    energy_evolution: list[float] = []
    buffer_energy = 0.0

    for idx, offset in enumerate(offsets):
        if idx < len(demand):
            heat_demand = max(float(demand[idx]), 0.0)
        else:
            heat_demand = 0.0

        # Calculate energy stored/released in this time step
        # Positive offset stores energy, negative offset uses stored energy
        # Storage amount is proportional to demand (more demand = more thermal mass active)
        energy_delta = (
            offset * heat_demand * DEFAULT_THERMAL_STORAGE_EFFICIENCY * step_hours
        )
        buffer_energy += energy_delta
        energy_evolution.append(round(buffer_energy, 3))

    return energy_evolution


class HeatingCurveOffsetSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        net_heat_sensor: str | SensorEntity,
        price_sensor: str | SensorEntity,
        device: DeviceInfo,
        *,
        entry: ConfigEntry | None = None,
        k_factor: float = DEFAULT_K_FACTOR,
        cop_compensation_factor: float = 1.0,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        planning_window: int = DEFAULT_PLANNING_WINDOW,
        time_base: int = DEFAULT_TIME_BASE,
        consumption_sensors: list[str] | None = None,
        heatpump_sensor: str | None = None,
        production_sensors: list[str] | None = None,
        production_price_sensor: str | SensorEntity | None = None,
        outdoor_sensor: str | SensorEntity | None = None,
        pv_forecast_sensor: PVProductionForecastSensor | None = None,
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
        self._entry = entry
        self.net_heat_sensor = net_heat_sensor
        self.price_sensor = price_sensor
        self.production_price_sensor = production_price_sensor
        self.k_factor = k_factor
        self.cop_compensation_factor = cop_compensation_factor
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.planning_window = planning_window
        self.time_base = time_base
        self.steps = max(1, int(planning_window * 60 // time_base))
        self.consumption_sensors = consumption_sensors or []
        self.heatpump_sensor = heatpump_sensor
        self.production_sensors = production_sensors or []
        self.pv_forecast_sensor = pv_forecast_sensor
        self._extra_attrs: dict[str, list[int] | list[float] | float] = {}
        self._outdoor_sensor: str | SensorEntity | None = (
            outdoor_sensor or "sensor.outdoor_temperature"
        )
        self._outdoor_entity_id: str | None = None
        if isinstance(self._outdoor_sensor, str):
            self._outdoor_entity_id = self._outdoor_sensor
        self._time_base_issues: set[str] = set()

    @property
    def extra_state_attributes(self) -> dict[str, list[int] | list[float] | float]:
        return self._extra_attrs

    def _get_config_value(self, key: str, default: Any) -> Any:
        """Get config value with fallback to runtime data."""
        # Try entry options first
        if self._entry is not None:
            value = self._entry.options.get(key)
            if value is not None:
                return value
            # Try entry data
            value = self._entry.data.get(key)
            if value is not None:
                return value

        # Fall back to runtime data
        runtime = self.hass.data.get(DOMAIN, {}).get("runtime", {})
        value = runtime.get(key)
        if value is not None:
            return value

        # Use default
        return default

    def _resample_forecast(
        self,
        values: Iterable[Any],
        source_base: int | None,
        *,
        label: str,
        fill_value: float = 0.0,
    ) -> list[float]:
        base = source_base or self.time_base
        numeric_values: list[float] = []
        for item in values:
            try:
                numeric_values.append(float(item))
            except (TypeError, ValueError):
                continue

        if not numeric_values:
            return [float(fill_value)] * self.steps

        result: list[float] = []
        for step in range(self.steps):
            start_minute = step * self.time_base
            end_minute = (step + 1) * self.time_base

            # Find all indices that fall within this time range
            start_idx = int(start_minute / base)
            end_idx = int(end_minute / base)

            # Calculate average of all values in this range
            if end_idx <= len(numeric_values):
                values_in_range = numeric_values[start_idx:end_idx]
            else:
                values_in_range = numeric_values[start_idx:]

            if values_in_range:
                result.append(sum(values_in_range) / len(values_in_range))
            else:
                result.append(float(fill_value))

        if source_base and base != self.time_base:
            self._time_base_issues.add(
                f"{label}: forecast uses {base} min steps (expected {self.time_base})"
            )

        return result

    def _extract_prices(self, state) -> list[float]:
        prices = extract_price_forecast(state)
        source_base = _coerce_time_base(state.attributes.get("forecast_time_base"))
        if prices:
            fallback = float(prices[-1])
        else:
            try:
                fallback = float(state.state)
            except (TypeError, ValueError):
                fallback = 0.0
        return self._resample_forecast(
            prices,
            source_base,
            label="price",
            fill_value=fallback,
        )

    def _extract_demand(self, state) -> list[float]:
        source_base = _coerce_time_base(state.attributes.get("forecast_time_base"))
        forecast = state.attributes.get("forecast", [])
        return self._resample_forecast(
            forecast,
            source_base,
            label="net_heat",
            fill_value=0.0,
        )

    def _extract_production_forecast(self) -> list[float]:
        """Extract production forecast from production sensors or PV forecast sensor.

        Returns forecast in kW for each time step. Tries in this order:
        1. Production sensor with forecast attribute
        2. PV production forecast sensor (if configured)
        3. Current production value repeated for all steps
        """
        if not self.production_sensors and not self.pv_forecast_sensor:
            return [0.0] * self.steps

        # Try to get forecast from first production sensor
        if self.production_sensors:
            production_state = self.hass.states.get(self.production_sensors[0])
            if production_state:
                # Check for forecast attribute
                forecast_attr = production_state.attributes.get("forecast")
                if forecast_attr and isinstance(forecast_attr, list):
                    source_base = _coerce_time_base(
                        production_state.attributes.get("forecast_time_base")
                    )
                    return self._resample_forecast(
                        forecast_attr,
                        source_base,
                        label="production",
                        fill_value=0.0,
                    )

        # Fallback 1: Use PV forecast sensor if available
        if self.pv_forecast_sensor:
            try:
                forecast_attr = self.pv_forecast_sensor.extra_state_attributes.get(
                    "forecast"
                )
                if forecast_attr and isinstance(forecast_attr, list):
                    _LOGGER.info(
                        "Using PV production forecast sensor as production source "
                        "(configured production sensor lacks forecast attribute)"
                    )
                    source_base = _coerce_time_base(
                        self.pv_forecast_sensor.extra_state_attributes.get(
                            "forecast_time_base"
                        )
                    )
                    return self._resample_forecast(
                        forecast_attr,
                        source_base,
                        label="pv_production",
                        fill_value=0.0,
                    )
            except (AttributeError, TypeError) as err:
                _LOGGER.debug("Could not get forecast from PV sensor: %s", err)

        # Fallback 2: Use current production value for all steps
        if self.production_sensors:
            production_state = self.hass.states.get(self.production_sensors[0])
            if production_state:
                _LOGGER.warning(
                    "Production sensor '%s' does not have 'forecast' attribute "
                    "and no PV forecast sensor available. "
                    "Using constant value (%.3f kW) for all time steps. "
                    "For accurate optimization, configure a power sensor with forecast capability.",
                    self.production_sensors[0],
                    float(production_state.state) / 1000
                    if float(production_state.state) > 100
                    else float(production_state.state),
                )
                try:
                    current_production = float(production_state.state)
                    # Convert from W to kW if needed (assume values > 100 are in W)
                    if current_production > 100:
                        current_production = current_production / 1000.0
                    return [max(0.0, current_production)] * self.steps
                except (TypeError, ValueError):
                    pass

        return [0.0] * self.steps

    def _extract_baseline_consumption_forecast(self) -> list[float]:
        """Extract baseline consumption forecast (excluding heat pump).

        Returns forecast in kW for each time step. Falls back to historical
        average if no forecast is available.
        """
        if not self.consumption_sensors:
            return [0.0] * self.steps

        # Try to get forecast from first consumption sensor
        consumption_state = self.hass.states.get(self.consumption_sensors[0])
        if consumption_state:
            forecast_attr = consumption_state.attributes.get("forecast")
            if forecast_attr and isinstance(forecast_attr, list):
                source_base = _coerce_time_base(
                    consumption_state.attributes.get("forecast_time_base")
                )
                consumption_forecast = self._resample_forecast(
                    forecast_attr,
                    source_base,
                    label="consumption",
                    fill_value=0.0,
                )

                # Subtract heat pump consumption if available
                if self.heatpump_sensor:
                    heatpump_state = self.hass.states.get(self.heatpump_sensor)
                    if heatpump_state:
                        try:
                            current_hp_power = float(heatpump_state.state)
                            # Convert from W to kW if needed
                            if current_hp_power > 100:
                                current_hp_power = current_hp_power / 1000.0
                            # Subtract heat pump from baseline
                            return [
                                max(0.0, c - current_hp_power)
                                for c in consumption_forecast
                            ]
                        except (TypeError, ValueError):
                            pass

                return consumption_forecast

        # Fallback: use current consumption value
        # Log warning if sensor doesn't have forecast attribute
        if consumption_state:
            _LOGGER.warning(
                "Consumption sensor '%s' does not have 'forecast' attribute. "
                "Using constant value for all time steps. "
                "For accurate optimization, configure a power sensor with forecast capability. "
                "Note: Cumulative energy sensors (kWh) should not be used - they lack forecast data.",
                self.consumption_sensors[0],
            )
            try:
                current_consumption = float(consumption_state.state)
                # Convert from W to kW if needed
                if current_consumption > 100:
                    current_consumption = current_consumption / 1000.0

                # Subtract current heat pump power
                baseline = current_consumption
                if self.heatpump_sensor:
                    heatpump_state = self.hass.states.get(self.heatpump_sensor)
                    if heatpump_state:
                        try:
                            hp_power = float(heatpump_state.state)
                            if hp_power > 100:
                                hp_power = hp_power / 1000.0
                            baseline -= hp_power
                        except (TypeError, ValueError):
                            pass

                return [max(0.0, baseline)] * self.steps
            except (TypeError, ValueError):
                pass

        return [0.0] * self.steps

    def _select_dynamic_prices(
        self,
        consumption_prices: list[float],
        production_prices: list[float] | None,
        production_forecast: list[float],
        baseline_consumption: list[float],
    ) -> list[float]:
        """Select appropriate price per time step based on net energy balance.

        When production > consumption (net production), use production price
        because heat pump reduces sellback (opportunity cost).
        When consumption > production (net consumption), use consumption price
        because we're buying from grid.

        Args:
            consumption_prices: Electricity consumption prices per step (EUR/kWh)
            production_prices: Electricity production prices per step (EUR/kWh)
            production_forecast: Expected production per step (kW)
            baseline_consumption: Expected consumption excluding heat pump (kW)

        Returns:
            Selected prices per time step (EUR/kWh)
        """
        if not production_prices or not self.production_sensors:
            # No production price sensor configured, always use consumption prices
            return consumption_prices

        selected_prices: list[float] = []

        for i in range(self.steps):
            prod = production_forecast[i] if i < len(production_forecast) else 0.0
            cons = baseline_consumption[i] if i < len(baseline_consumption) else 0.0

            # Calculate net balance (positive = producing, negative = consuming)
            net_balance = prod - cons

            if net_balance > 0:
                # Net production: heat pump reduces sellback
                # Use production price (opportunity cost of not selling)
                price = (
                    production_prices[i]
                    if i < len(production_prices)
                    else consumption_prices[i]
                )
            else:
                # Net consumption: heat pump increases grid consumption
                # Use consumption price (actual cost of buying)
                price = consumption_prices[i] if i < len(consumption_prices) else 0.0

            selected_prices.append(price)

        return selected_prices

    def _apply_solar_buffer(
        self, demand: list[float]
    ) -> tuple[list[float], list[float], float, float, float]:
        """Use negative demand as a solar buffer inside the planning window."""

        if self.time_base <= 0:
            step_hours = 1.0
        else:
            step_hours = self.time_base / 60.0

        buffer_kwh = 0.0
        total_gain_kwh = 0.0
        adjusted: list[float] = []
        evolution: list[float] = []

        for value in demand:
            try:
                heat = float(value)
            except (TypeError, ValueError):
                heat = 0.0
            energy = heat * step_hours
            if energy < 0:
                gain = -energy
                total_gain_kwh += gain
                buffer_kwh += gain
                adjusted.append(0.0)
            else:
                usable = min(buffer_kwh, energy)
                energy -= usable
                buffer_kwh -= usable
                adjusted_value = energy / step_hours if step_hours > 0 else 0.0
                adjusted.append(adjusted_value)
            evolution.append(round(buffer_kwh, 3))

        used_gain = total_gain_kwh - buffer_kwh
        return adjusted, evolution, total_gain_kwh, buffer_kwh, used_gain

    async def _compute_history_baseline(self) -> float:
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )
            from homeassistant.util import dt as dt_util
        except Exception:
            return 0.0

        start = dt_util.utcnow() - timedelta(hours=24)
        try:
            instance = get_instance(self.hass)
        except Exception:
            return 0.0

        def _mean(entity_id: str) -> float:
            stats = statistics_during_period(
                self.hass,
                start,
                None,
                {entity_id},
                "hour",
                None,
                {"mean"},
            )
            rows = stats.get(entity_id)
            if not rows:
                return 0.0
            means = [row.mean for row in rows if getattr(row, "mean", None) is not None]
            if not means:
                return 0.0
            return sum(means) / len(means) / 1000.0

        baseline = 0.0
        for ent in self.consumption_sensors:
            baseline += await instance.async_add_executor_job(_mean, ent)
        if self.heatpump_sensor:
            baseline += await instance.async_add_executor_job(
                _mean, self.heatpump_sensor
            )
        for ent in self.production_sensors:
            baseline -= await instance.async_add_executor_job(_mean, ent)
        return baseline

    async def async_update(self):
        ent = (
            self.net_heat_sensor.entity_id
            if hasattr(self.net_heat_sensor, "entity_id")
            else self.net_heat_sensor
        )
        price_state = self.hass.states.get(self.price_sensor)
        net_state = self.hass.states.get(ent) if ent else None
        self._time_base_issues.clear()

        if net_state is None:
            self._set_unavailable(f"geen netto warmtevraag sensor gevonden ({ent})")
            return
        if net_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"netto warmtevraag sensor {ent} heeft status '{net_state.state}'"
            )
            return
        if price_state is None:
            self._set_unavailable(f"prijssensor {self.price_sensor} werd niet gevonden")
            return
        if price_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"prijssensor {self.price_sensor} heeft status '{price_state.state}'"
            )
            return

        raw_demand = self._extract_demand(net_state)
        baseline = await self._compute_history_baseline()
        if baseline:
            baseline_adjusted = [d + baseline for d in raw_demand]
        else:
            baseline_adjusted = list(raw_demand)
        (
            demand,
            solar_buffer_evolution,
            solar_gain_total,
            solar_buffer_remaining,
            solar_gain_used,
        ) = self._apply_solar_buffer(baseline_adjusted)

        # Extract consumption prices
        consumption_prices = self._extract_prices(price_state)

        # Extract production prices if available
        production_prices: list[float] | None = None
        if self.production_price_sensor:
            production_price_state = self.hass.states.get(self.production_price_sensor)
            if production_price_state and production_price_state.state not in (
                "unknown",
                "unavailable",
            ):
                production_prices = self._extract_prices(production_price_state)
                _LOGGER.debug("Production prices extracted: %s", production_prices)

        # Extract production and baseline consumption forecasts for dynamic pricing
        production_forecast = self._extract_production_forecast()
        baseline_consumption = self._extract_baseline_consumption_forecast()

        # Select appropriate prices based on net energy balance
        prices = self._select_dynamic_prices(
            consumption_prices,
            production_prices,
            production_forecast,
            baseline_consumption,
        )

        total_energy = sum(demand)
        _LOGGER.debug(
            "Offset sensor demand=%s consumption_prices=%s production_prices=%s "
            "selected_prices=%s production=%s baseline_consumption=%s baseline=%s solar_buffer=%s",
            demand,
            consumption_prices,
            production_prices,
            prices,
            production_forecast,
            baseline_consumption,
            baseline,
            solar_buffer_evolution,
        )

        # Get heating curve parameters from config entry
        water_min = float(
            self._get_config_value(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN)
        )
        water_max = float(
            self._get_config_value(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX)
        )
        outdoor_min = float(self._get_config_value(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0))
        outdoor_max = float(self._get_config_value(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0))

        entity_id = self._outdoor_entity_id
        if entity_id is None and isinstance(self._outdoor_sensor, SensorEntity):
            entity_id = self._outdoor_sensor.entity_id

        if entity_id is None:
            self._set_unavailable("geen buitensensor gevonden")
            return

        o_state = self.hass.states.get(entity_id)
        if o_state is None:
            self._set_unavailable(f"{entity_id} werd niet gevonden")
            return
        if o_state.state in ("unknown", "unavailable"):
            self._set_unavailable(f"{entity_id} heeft status '{o_state.state}'")
            return
        try:
            outdoor_temp = float(o_state.state)
        except ValueError:
            self._set_unavailable(f"{entity_id} levert een ongeldige waarde")
            return

        base_temp = _calculate_supply_temperature(
            outdoor_temp,
            water_min=water_min,
            water_max=water_max,
            outdoor_min=outdoor_min,
            outdoor_max=outdoor_max,
        )

        # Extract outdoor temperature and humidity forecasts for defrost modeling
        outdoor_temp_forecast: list[float] = []
        humidity_forecast: list[float] = []

        if o_state.attributes:
            # Get forecast from outdoor sensor attributes
            forecast_attr = o_state.attributes.get("forecast", [])
            if forecast_attr and isinstance(forecast_attr, list):
                outdoor_temp_forecast = [float(v) for v in forecast_attr[: self.steps]]

            # Get humidity forecast if available
            humidity_attr = o_state.attributes.get("humidity_forecast", [])
            if humidity_attr and isinstance(humidity_attr, list):
                humidity_forecast = [float(v) for v in humidity_attr[: self.steps]]

        _LOGGER.debug(
            "Outdoor forecasts for optimization: temp=%s, humidity=%s",
            outdoor_temp_forecast,
            humidity_forecast,
        )

        allowed_offsets = [
            o for o in range(-4, 5) if water_min <= base_temp + o <= water_max
        ]
        optimization_reason: str | None = None
        if total_energy <= 0:
            optimization_reason = "Geen optimalisatie: de totale warmtevraag in het venster is niet positief."
        elif not any(d > 0 for d in demand):
            optimization_reason = (
                "Geen optimalisatie: de voorspelde netto warmtevraag is niet positief."
            )
        elif len(allowed_offsets) <= 1:
            optimization_reason = (
                "Geen optimalisatie: de ingestelde stooklijn laat geen afwijking toe."
            )

        if optimization_reason is None:
            offsets, buffer_offset_evolution = await self.hass.async_add_executor_job(
                partial(
                    _optimize_offsets,
                    base_temp=base_temp,
                    k_factor=self.k_factor,
                    cop_compensation_factor=self.cop_compensation_factor,
                    buffer=0.0,
                    water_min=water_min,
                    water_max=water_max,
                    outdoor_temps=outdoor_temp_forecast
                    if outdoor_temp_forecast
                    else None,
                    humidity_forecast=humidity_forecast if humidity_forecast else None,
                    outdoor_temp_coefficient=self.outdoor_temp_coefficient,
                    time_base=self.time_base,
                    outdoor_min=outdoor_min,
                    outdoor_max=outdoor_max,
                ),
                demand,
                prices,
            )
            _LOGGER.debug(
                "Calculated offsets=%s buffer_evolution=%s",
                offsets,
                buffer_offset_evolution,
            )
        else:
            offsets = [0 for _ in range(self.steps)]
            buffer_offset_evolution = [0.0 for _ in range(self.steps)]

        buffer_energy_evolution = _calculate_buffer_energy(
            offsets,
            demand,
            time_base=self.time_base,
        )

        if offsets:
            self._attr_native_value = offsets[0]
        else:
            self._attr_native_value = 0

        # Calculate per-step base temperatures using outdoor forecast
        # If we have outdoor forecast, use it; otherwise fall back to current outdoor temp
        if outdoor_temp_forecast and len(outdoor_temp_forecast) >= self.steps:
            base_temps_for_display = [
                _calculate_supply_temperature(
                    outdoor_temp_forecast[i],
                    water_min=water_min,
                    water_max=water_max,
                    outdoor_min=outdoor_min,
                    outdoor_max=outdoor_max,
                )
                for i in range(self.steps)
            ]
        else:
            # Fallback to using current base_temp for all steps
            base_temps_for_display = [base_temp] * self.steps

        supply_temps = [
            round(
                max(min(base_temps_for_display[i] + offsets[i], water_max), water_min),
                1,
            )
            for i in range(len(offsets))
        ]
        # Calculate net balance for display
        net_balance_forecast = [
            round(production_forecast[i] - baseline_consumption[i], 3)
            if i < len(production_forecast) and i < len(baseline_consumption)
            else 0.0
            for i in range(self.steps)
        ]

        self._extra_attrs = {
            "future_offsets": offsets,
            "prices": prices,
            "consumption_prices": consumption_prices,
            "production_prices": production_prices if production_prices else [],
            "production_forecast_kw": [round(v, 3) for v in production_forecast],
            "baseline_consumption_kw": [round(v, 3) for v in baseline_consumption],
            "net_balance_kw": net_balance_forecast,
            "price_source": [
                "production" if net_balance_forecast[i] > 0 else "consumption"
                for i in range(min(len(net_balance_forecast), self.steps))
            ],
            "buffer_evolution": buffer_energy_evolution,
            "buffer_evolution_offsets": buffer_offset_evolution,
            "total_energy": round(total_energy, 3),
            "future_supply_temperatures": supply_temps,
            "base_supply_temperature": round(base_temp, 1),
            "optimization_status": optimization_reason or "OK",
            "time_base_minutes": self.time_base,
            "time_base_issues": sorted(self._time_base_issues),
            "raw_net_heat_forecast": [round(v, 3) for v in raw_demand],
            "baseline_adjusted_net_heat_forecast": [
                round(v, 3) for v in baseline_adjusted
            ],
            "net_heat_forecast_after_solar": [round(v, 3) for v in demand],
            "solar_buffer_evolution": solar_buffer_evolution,
            "solar_gain_available_kwh": round(solar_gain_total, 3),
            "solar_gain_used_kwh": round(solar_gain_used, 3),
            "solar_gain_remaining_kwh": round(solar_buffer_remaining, 3),
        }
        self._mark_available()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if not isinstance(self.net_heat_sensor, str):
            self.net_heat_sensor = self.net_heat_sensor.entity_id
        if not isinstance(self.price_sensor, str):
            self.price_sensor = self.price_sensor.entity_id
        if isinstance(self._outdoor_sensor, SensorEntity):
            self._outdoor_entity_id = self._outdoor_sensor.entity_id
        elif isinstance(self._outdoor_sensor, str):
            self._outdoor_entity_id = self._outdoor_sensor
        for ent in (self.net_heat_sensor, self.price_sensor):
            if ent:
                self.async_on_remove(
                    async_track_state_change_event(self.hass, ent, self._handle_change)
                )
        if self._outdoor_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._outdoor_entity_id, self._handle_change
                )
            )

    async def _handle_change(self, event):
        await self.async_update()
        self.async_write_ha_state()


class HeatBufferSensor(BaseUtilitySensor):
    """Expose the heat buffer evolution from the heating curve optimization."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        offset_entity: str | SensorEntity,
        device: DeviceInfo,
    ) -> None:
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="kWh",
            device_class=SensorDeviceClass.ENERGY,
            icon="mdi:database",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_").replace(".", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.offset_entity = offset_entity
        self._extra_attrs: dict[str, list[float] | dict[str, float]] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float] | dict[str, float]]:
        return self._extra_attrs

    async def async_update(self) -> None:
        ent = (
            self.offset_entity.entity_id
            if hasattr(self.offset_entity, "entity_id")
            else self.offset_entity
        )
        if not ent:
            self._set_unavailable("geen offset-sensor ingesteld")
            return
        state = self.hass.states.get(ent)
        if state is None:
            self._set_unavailable(f"offset-sensor {ent} werd niet gevonden")
            return
        if state.state in ("unknown", "unavailable"):
            self._set_unavailable(f"offset-sensor {ent} heeft status '{state.state}'")
            return
        if "buffer_evolution" not in state.attributes:
            self._set_unavailable(
                f"offset-sensor {ent} levert geen 'buffer_evolution' attribuut"
            )
            return

        evolution = [float(v) for v in state.attributes.get("buffer_evolution", [])]
        if not evolution:
            self._set_unavailable(
                f"offset-sensor {ent} gaf een lege bufferprognose terug"
            )
            return

        self._attr_native_value = evolution[0]
        self._extra_attrs = {
            "buffer_evolution": evolution,
            "buffer_by_step": {str(i): v for i, v in enumerate(evolution)},
        }
        self._mark_available()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if not isinstance(self.offset_entity, str):
            self.offset_entity = self.offset_entity.entity_id
        if self.offset_entity:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self.offset_entity, self._handle_change
                )
            )

    async def _handle_change(self, event):
        await self.async_update()
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heating Curve Optimizer",
    )
    entities: list[BaseUtilitySensor] = []

    outdoor_sensor_entity = OutdoorTemperatureSensor(
        hass=hass,
        name="Outdoor Temperature",
        unique_id=f"{entry.entry_id}_outdoor_temperature",
        device=device_info,
    )
    entities.append(outdoor_sensor_entity)

    entities.append(
        CalculatedSupplyTemperatureSensor(
            hass=hass,
            name="Calculated Supply Temperature",
            unique_id=f"{entry.entry_id}_calculated_supply_temperature",
            outdoor_sensor=outdoor_sensor_entity,
            entry=entry,
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

    # Deduplicate sources while preserving order (fixes issue with duplicate configs)
    consumption_sources = list(dict.fromkeys(consumption_sources))
    production_sources = list(dict.fromkeys(production_sources))

    area_m2 = entry.options.get(CONF_AREA_M2, entry.data.get(CONF_AREA_M2))
    energy_label = entry.options.get(
        CONF_ENERGY_LABEL, entry.data.get(CONF_ENERGY_LABEL)
    )
    indoor_sensor = entry.options.get(
        CONF_INDOOR_TEMPERATURE_SENSOR, entry.data.get(CONF_INDOOR_TEMPERATURE_SENSOR)
    )
    supply_temp_sensor = entry.options.get(
        CONF_SUPPLY_TEMPERATURE_SENSOR, entry.data.get(CONF_SUPPLY_TEMPERATURE_SENSOR)
    )
    outdoor_temp_sensor: str | SensorEntity = outdoor_sensor_entity
    power_sensor = entry.options.get(
        CONF_POWER_CONSUMPTION, entry.data.get(CONF_POWER_CONSUMPTION)
    )
    k_factor = float(
        entry.options.get(
            CONF_K_FACTOR, entry.data.get(CONF_K_FACTOR, DEFAULT_K_FACTOR)
        )
    )
    base_cop = float(
        entry.options.get(
            CONF_BASE_COP, entry.data.get(CONF_BASE_COP, DEFAULT_COP_AT_35)
        )
    )
    outdoor_temp_coefficient = float(
        entry.options.get(
            CONF_OUTDOOR_TEMP_COEFFICIENT,
            entry.data.get(
                CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
            ),
        )
    )
    cop_compensation_factor = float(
        entry.options.get(
            CONF_COP_COMPENSATION_FACTOR,
            entry.data.get(
                CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
            ),
        )
    )
    glass_east = float(
        entry.options.get(CONF_GLASS_EAST_M2, entry.data.get(CONF_GLASS_EAST_M2, 0))
    )
    glass_west = float(
        entry.options.get(CONF_GLASS_WEST_M2, entry.data.get(CONF_GLASS_WEST_M2, 0))
    )
    glass_south = float(
        entry.options.get(CONF_GLASS_SOUTH_M2, entry.data.get(CONF_GLASS_SOUTH_M2, 0))
    )
    glass_u = float(
        entry.options.get(CONF_GLASS_U_VALUE, entry.data.get(CONF_GLASS_U_VALUE, 1.2))
    )
    planning_window = int(
        entry.options.get(
            CONF_PLANNING_WINDOW,
            entry.data.get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW),
        )
    )
    time_base = int(
        entry.options.get(
            CONF_TIME_BASE, entry.data.get(CONF_TIME_BASE, DEFAULT_TIME_BASE)
        )
    )

    price_sensor = entry.options.get(
        CONF_PRICE_SENSOR, entry.data.get(CONF_PRICE_SENSOR)
    )
    consumption_price_sensor = entry.options.get(
        CONF_CONSUMPTION_PRICE_SENSOR,
        entry.data.get(CONF_CONSUMPTION_PRICE_SENSOR, price_sensor),
    )
    production_price_sensor = entry.options.get(
        CONF_PRODUCTION_PRICE_SENSOR,
        entry.data.get(CONF_PRODUCTION_PRICE_SENSOR, price_sensor),
    )

    # Note: We don't create wrapper entities for price sensors anymore.
    # The integration uses the price sensors from other integrations directly.
    # This reduces clutter in the UI and avoids unnecessary entity duplication.

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

    # PV production forecast sensor
    pv_east_wp = float(
        entry.options.get(CONF_PV_EAST_WP, entry.data.get(CONF_PV_EAST_WP, 0))
    )
    pv_west_wp = float(
        entry.options.get(CONF_PV_WEST_WP, entry.data.get(CONF_PV_WEST_WP, 0))
    )
    pv_south_wp = float(
        entry.options.get(CONF_PV_SOUTH_WP, entry.data.get(CONF_PV_SOUTH_WP, 0))
    )
    pv_tilt = float(
        entry.options.get(CONF_PV_TILT, entry.data.get(CONF_PV_TILT, DEFAULT_PV_TILT))
    )

    pv_forecast_sensor = None
    if pv_east_wp or pv_west_wp or pv_south_wp:
        pv_forecast_sensor = PVProductionForecastSensor(
            hass=hass,
            name="PV Production Forecast",
            unique_id=f"{entry.entry_id}_pv_production_forecast",
            east_wp=pv_east_wp,
            west_wp=pv_west_wp,
            south_wp=pv_south_wp,
            tilt=pv_tilt,
            icon="mdi:solar-power",
            device=device_info,
        )
        entities.append(pv_forecast_sensor)

    net_heat_sensor = None
    if heat_loss_sensor:
        net_heat_sensor = NetHeatLossSensor(
            hass=hass,
            name="Hourly Net Heat Loss",
            unique_id=f"{entry.entry_id}_hourly_net_heat_loss",
            icon="mdi:fire",
            device=device_info,
            heat_loss_sensor=heat_loss_sensor,
            window_gain_sensor=window_gain_sensor,
            config_entry_id=entry.entry_id,
        )
        entities.append(net_heat_sensor)
        hass.data.setdefault(DOMAIN, {}).setdefault("runtime", {}).setdefault(
            entry.entry_id, {}
        )["net_heat_sensor_object"] = net_heat_sensor

    cop_sensor_entity = None
    thermal_power_sensor_entity = None
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
            cop_compensation_factor=cop_compensation_factor,
            device=device_info,
        )
        entities.append(cop_sensor_entity)
        if power_sensor:
            thermal_power_sensor_entity = HeatPumpThermalPowerSensor(
                hass=hass,
                name="Heat Pump Thermal Power",
                unique_id=f"{entry.entry_id}_thermal_power",
                power_sensor=power_sensor,
                supply_sensor=supply_temp_sensor,
                outdoor_sensor=outdoor_temp_sensor,
                device=device_info,
                k_factor=k_factor,
                base_cop=base_cop,
            )
            entities.append(thermal_power_sensor_entity)

    diagnostics_price_sensor = consumption_price_sensor or production_price_sensor

    if (
        heat_loss_sensor
        or window_gain_sensor
        or diagnostics_price_sensor
        or cop_sensor_entity
    ):
        entities.append(
            DiagnosticsSensor(
                hass=hass,
                name="Optimizer Diagnostics",
                unique_id=f"{entry.entry_id}_diagnostics",
                heat_loss_sensor=heat_loss_sensor,
                window_gain_sensor=window_gain_sensor,
                price_sensor=diagnostics_price_sensor,
                cop_sensor=cop_sensor_entity,
                device=device_info,
                planning_window=planning_window,
                time_base=time_base,
            )
        )

    heating_curve_offset_sensor = None
    if net_heat_sensor and consumption_price_sensor:
        heating_curve_offset_sensor = HeatingCurveOffsetSensor(
            hass=hass,
            name="Heating Curve Offset",
            unique_id=f"{entry.entry_id}_heating_curve_offset",
            net_heat_sensor=net_heat_sensor,
            price_sensor=consumption_price_sensor,
            device=device_info,
            entry=entry,
            k_factor=k_factor,
            cop_compensation_factor=cop_compensation_factor,
            outdoor_temp_coefficient=outdoor_temp_coefficient,
            consumption_sensors=consumption_sources,
            heatpump_sensor=power_sensor,
            production_sensors=production_sources,
            production_price_sensor=production_price_sensor,
            outdoor_sensor=outdoor_sensor_entity,
            planning_window=planning_window,
            time_base=time_base,
            pv_forecast_sensor=pv_forecast_sensor,
        )
        entities.append(heating_curve_offset_sensor)

    if cop_sensor_entity and heating_curve_offset_sensor:
        entities.append(
            CopEfficiencyDeltaSensor(
                hass=hass,
                name="COP Delta",
                unique_id=f"{entry.entry_id}_cop_delta",
                cop_sensor=cop_sensor_entity,
                offset_entity=heating_curve_offset_sensor,
                outdoor_sensor=outdoor_sensor_entity,
                device=device_info,
                k_factor=k_factor,
                base_cop=base_cop,
                outdoor_temp_coefficient=outdoor_temp_coefficient,
                cop_compensation_factor=cop_compensation_factor,
            )
        )
        if thermal_power_sensor_entity is not None:
            entities.append(
                HeatGenerationDeltaSensor(
                    hass=hass,
                    name="Heat Generation Delta",
                    unique_id=f"{entry.entry_id}_heat_generation_delta",
                    thermal_power_sensor=thermal_power_sensor_entity,
                    cop_sensor=cop_sensor_entity,
                    offset_entity=heating_curve_offset_sensor,
                    outdoor_sensor=outdoor_sensor_entity,
                    device=device_info,
                    k_factor=k_factor,
                    base_cop=base_cop,
                    outdoor_temp_coefficient=outdoor_temp_coefficient,
                    cop_compensation_factor=cop_compensation_factor,
                )
            )

    if heating_curve_offset_sensor is not None:
        entities.append(
            HeatBufferSensor(
                hass=hass,
                name="Heat Buffer",
                unique_id=f"{entry.entry_id}_heat_buffer",
                offset_entity=heating_curve_offset_sensor,
                device=device_info,
            )
        )
        entities.append(
            OptimizedSupplyTemperatureSensor(
                hass=hass,
                name="Optimized Supply Temperature",
                unique_id=f"{entry.entry_id}_optimized_supply_temperature",
                outdoor_sensor=outdoor_sensor_entity,
                entry=entry,
                device=device_info,
                offset_entity=heating_curve_offset_sensor,
                k_factor=k_factor,
                planning_window=planning_window,
                time_base=time_base,
            )
        )

    if entities:
        async_add_entities(entities, True)

    hass.data[DOMAIN]["entities"] = {ent.entity_id: ent for ent in entities}
