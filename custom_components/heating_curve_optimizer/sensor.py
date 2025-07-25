from __future__ import annotations

from typing import cast

import logging
import math

import aiohttp
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from typing import Any

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
    CONF_PRICE_SENSOR,
    CONF_PRICE_SETTINGS,
    CONF_SOLAR_FORECAST,
    CONF_CONFIGS,
    INDOOR_TEMPERATURE,
    SOLAR_EFFICIENCY,
    U_VALUE_MAP,
)
from .entity import BaseUtilitySensor

_LOGGER = logging.getLogger(__name__)

UTILITY_ENTITIES: list[BaseUtilitySensor] = []
PARALLEL_UPDATES = 1


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
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = aiohttp.ClientSession()

    async def _fetch_weather(self) -> tuple[float, list[float]]:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m&current_weather=true&timezone=UTC"
        )
        async with self.session.get(url) as resp:
            data = await resp.json()
        current = float(data.get("current_weather", {}).get("temperature", 0))
        temps = [float(t) for t in data.get("hourly", {}).get("temperature_2m", [])]
        return current, temps

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _compute_value(self) -> None:
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


class SolarGainSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        solar_sensor: list[str],
        area_m2: float,
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
        self.solar_sensors = solar_sensor
        self._extra_attrs: dict[str, list[float]] = {}
        self.area_m2 = area_m2

    def _extract_forecast(self, state) -> list[float]:
        """Return forecast values for the next 24h for a Solcast/Forecast.Solar sensor."""
        from datetime import timedelta
        from homeassistant.util import dt as dt_util

        for key in (
            "detailed forecast",
            "detailed_forecast",
            "detailedForecast",
            "forecast",
            "all",
        ):
            data = state.attributes.get(key)
            if data:
                break
        else:
            data = []

        now = dt_util.utcnow()
        end = now + timedelta(hours=24)
        values: list[float] = []
        if isinstance(data, (list, tuple)):
            for item in data:
                ts = None
                if isinstance(item, dict):
                    val = (
                        item.get("pv_estimate")
                        or item.get("value")
                        or item.get("energy")
                        or item.get("wh")
                        or item.get("watts")
                    )
                    ts = (
                        item.get("period_end")
                        or item.get("period")
                        or item.get("time")
                        or item.get("dt")
                    )
                else:
                    val = item
                try:
                    if val is None:
                        continue
                    val_float = float(val)
                except (TypeError, ValueError):
                    continue

                if ts:
                    dt_obj = dt_util.parse_datetime(str(ts))
                    if dt_obj is None:
                        continue
                    dt_obj = dt_util.as_utc(dt_obj)
                    if dt_obj < now or dt_obj >= end:
                        continue

                if len(values) >= 24:
                    break
                values.append(val_float)

        return values

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    async def _compute_value(self) -> None:
        total_solar = 0.0
        attr_data: dict[str, list] = {}
        aggregated: list[float] = []

        for sensor in self.solar_sensors:
            state = self.hass.states.get(sensor)
            if state is None or state.state in ("unknown", "unavailable"):
                _LOGGER.warning("Solar forecast sensor %s is unavailable", sensor)
                continue

            forecast = self._extract_forecast(state)
            if not forecast:
                _LOGGER.warning(
                    "Solar forecast sensor %s has no usable forecast", sensor
                )
                continue

            attr_data[sensor] = forecast
            total_solar += forecast[0]

            for i, v in enumerate(forecast[:24]):
                if len(aggregated) <= i:
                    aggregated.append(float(v))
                else:
                    aggregated[i] += float(v)

        if not attr_data:
            self._attr_available = False
            return

        agg_list: list[float | None] = [round(v, 3) for v in aggregated]
        if len(agg_list) < 24:
            agg_list.extend([None] * (24 - len(agg_list)))
        attr_data["aggregated"] = cast(list[float], agg_list)

        self._extra_attrs = attr_data
        self._attr_available = True
        q_solar = total_solar * self.area_m2 * SOLAR_EFFICIENCY / 1000.0
        self._attr_native_value = round(q_solar, 3)

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for sensor in self.solar_sensors:
            self.async_on_remove(
                async_track_state_change_event(self.hass, sensor, self._handle_change)
            )

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()

    async def _handle_change(self, event):
        await self._compute_value()
        self.async_write_ha_state()


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
        solar_sensor: list[str],
        area_m2: float,
        energy_label: str,
        indoor_sensor: str | None,
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
        self.solar_sensors = solar_sensor
        self.area_m2 = area_m2
        self.energy_label = energy_label
        self.indoor_sensor = indoor_sensor
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = aiohttp.ClientSession()

    async def _compute_value(self) -> None:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&current_weather=true&hourly=temperature_2m&timezone=UTC"
        )
        async with self.session.get(url) as resp:
            data = await resp.json()
        t_outdoor = float(data.get("current_weather", {}).get("temperature", 0))

        solar_total = 0.0
        for sensor in self.solar_sensors:
            state = self.hass.states.get(sensor)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            try:
                solar_total += float(state.state)
            except ValueError:
                continue

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
        q_solar = solar_total * self.area_m2 * SOLAR_EFFICIENCY / 1000.0
        q_net = max(q_loss - q_solar, 0.0)
        self._attr_native_value = round(q_net, 3)

    async def async_update(self):
        await self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for sensor in self.solar_sensors:
            self.async_on_remove(
                async_track_state_change_event(self.hass, sensor, self._handle_change)
            )

    async def _handle_change(self, event):
        await self._compute_value()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        await self.session.close()


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
        from homeassistant.components.recorder import history

        func = cast(Any, history.state_changes_during_period)
        return cast(
            dict[str, list],
            await self.hass.async_add_executor_job(
                func,
                self.hass,
                start,
                end,
                sensors,
                False,
                True,
            ),
        )

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
    entities: list[BaseUtilitySensor] = []

    configs = entry.data.get(CONF_CONFIGS, [])
    consumption_sources: list[str] = []
    production_sources: list[str] = []
    for cfg in configs:
        if cfg.get(CONF_SOURCE_TYPE) == SOURCE_TYPE_CONSUMPTION:
            consumption_sources.extend(cfg.get(CONF_SOURCES, []))
        elif cfg.get(CONF_SOURCE_TYPE) == SOURCE_TYPE_PRODUCTION:
            production_sources.extend(cfg.get(CONF_SOURCES, []))

    area_m2 = entry.data.get(CONF_AREA_M2)
    energy_label = entry.data.get(CONF_ENERGY_LABEL)
    solar_sensor = entry.data.get(CONF_SOLAR_FORECAST)
    if isinstance(solar_sensor, str):
        solar_sensor = [solar_sensor]
    indoor_sensor = entry.data.get(CONF_INDOOR_TEMPERATURE_SENSOR)
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
                unique_id=f"{DOMAIN}_current_consumption_price",
                price_sensor=price_sensor,
                source_type=SOURCE_TYPE_CONSUMPTION,
                price_settings=price_settings,
                icon="mdi:transmission-tower-import",
                device=device_info,
            )
        )
        entities.append(
            CurrentElectricityPriceSensor(
                hass=hass,
                name="Current Production Price",
                unique_id=f"{DOMAIN}_current_production_price",
                price_sensor=price_sensor,
                source_type=SOURCE_TYPE_PRODUCTION,
                price_settings=price_settings,
                icon="mdi:transmission-tower-export",
                device=device_info,
            )
        )

        UTILITY_ENTITIES.extend(entities)

    if area_m2 and energy_label:
        entities.append(
            HeatLossSensor(
                hass=hass,
                name="Hourly Heat Loss",
                unique_id=f"{DOMAIN}_hourly_heat_loss",
                area_m2=float(area_m2),
                energy_label=energy_label,
                indoor_sensor=indoor_sensor,
                icon="mdi:home-thermometer",
                device=device_info,
            )
        )

    if solar_sensor and area_m2:
        entities.append(
            SolarGainSensor(
                hass=hass,
                name="Hourly Solar Gain",
                unique_id=f"{DOMAIN}_hourly_solar_gain",
                solar_sensor=solar_sensor,
                area_m2=float(area_m2),
                icon="mdi:weather-sunny",
                device=device_info,
            )
        )

    if area_m2 and (glass_east or glass_west or glass_south):
        entities.append(
            WindowSolarGainSensor(
                hass=hass,
                name="Window Solar Gain",
                unique_id=f"{DOMAIN}_window_solar_gain",
                east_m2=glass_east,
                west_m2=glass_west,
                south_m2=glass_south,
                u_value=glass_u,
                icon="mdi:window-closed-variant",
                device=device_info,
            )
        )

    if consumption_sources and production_sources:
        entities.append(
            EnergyConsumptionForecastSensor(
                hass=hass,
                name="Expected Energy Consumption",
                unique_id=f"{DOMAIN}_expected_energy_consumption",
                consumption_sensors=consumption_sources,
                production_sensors=production_sources,
                icon="mdi:flash-clock",
                device=device_info,
            )
        )

    if solar_sensor and area_m2 and energy_label:
        entities.append(
            NetHeatDemandSensor(
                hass=hass,
                name="Hourly Net Heat Demand",
                unique_id=f"{DOMAIN}_hourly_net_heat_demand",
                solar_sensor=solar_sensor,
                area_m2=float(area_m2),
                energy_label=energy_label,
                indoor_sensor=indoor_sensor,
                icon="mdi:fire",
                device=device_info,
            )
        )

    if entities:
        UTILITY_ENTITIES.extend(ent for ent in entities if ent not in UTILITY_ENTITIES)

    async_add_entities(entities, True)

    hass.data[DOMAIN]["entities"] = {ent.entity_id: ent for ent in entities}
