from __future__ import annotations


from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    CONF_PRICE_SENSOR,
    CONF_PRICE_SETTINGS,
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
    CONF_AREA_M2,
    CONF_ENERGY_LABEL,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_SOLAR_FORECAST,
    U_VALUE_MAP,
    INDOOR_TEMPERATURE,
    SOLAR_EFFICIENCY,
)
from .entity import BaseUtilitySensor

import logging

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
        outdoor_sensor: str,
        area_m2: float,
        energy_label: str,
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
        self.outdoor_sensor = outdoor_sensor
        self.area_m2 = area_m2
        self.energy_label = energy_label

    def _compute_value(self) -> None:
        state = self.hass.states.get(self.outdoor_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            self._attr_available = False
            _LOGGER.warning(
                "Outdoor temperature sensor %s is unavailable", self.outdoor_sensor
            )
            return
        try:
            t_outdoor = float(state.state)
        except ValueError:
            self._attr_available = False
            _LOGGER.warning(
                "Outdoor temperature sensor %s has invalid state", self.outdoor_sensor
            )
            return
        self._attr_available = True
        u_value = U_VALUE_MAP.get(self.energy_label.upper(), 1.0)
        q_loss = self.area_m2 * u_value * (INDOOR_TEMPERATURE - t_outdoor) / 1000.0
        self._attr_native_value = round(q_loss, 3)

    async def async_update(self):
        self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.outdoor_sensor, self._handle_change
            )
        )

    async def _handle_change(self, event):
        self._compute_value()
        self.async_write_ha_state()


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
        """Extract forecast list from a Solcast or Forecast.Solar sensor."""
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

        values: list[float] = []
        if isinstance(data, (list, tuple)):
            for item in data:
                if isinstance(item, dict):
                    val = (
                        item.get("pv_estimate")
                        or item.get("value")
                        or item.get("energy")
                        or item.get("wh")
                    )
                else:
                    val = item
                try:
                    values.append(float(val))
                except (TypeError, ValueError):
                    continue
        return values

    @property
    def extra_state_attributes(self) -> dict[str, list[float]]:
        return self._extra_attrs

    def _compute_value(self) -> None:
        total_solar = 0.0
        attr_data: dict[str, list[float]] = {}
        cumulative: list[float] = []

        for sensor in self.solar_sensors:
            state = self.hass.states.get(sensor)
            if state is None or state.state in ("unknown", "unavailable"):
                _LOGGER.warning("Solar forecast sensor %s is unavailable", sensor)
                continue
            try:
                val = float(state.state)
            except ValueError:
                _LOGGER.warning("Solar forecast sensor %s has invalid state", sensor)
                continue
            total_solar += val

            forecast = self._extract_forecast(state)
            if forecast:
                attr_data[sensor] = forecast
                for i, v in enumerate(forecast):
                    if len(cumulative) <= i:
                        cumulative.append(float(v))
                    else:
                        cumulative[i] += float(v)

        if not attr_data:
            self._attr_available = False
            return

        run = 0.0
        cum_list: list[float] = []
        for v in cumulative:
            run += v
            cum_list.append(run)
        attr_data["cumulative"] = cum_list

        self._extra_attrs = attr_data
        self._attr_available = True
        q_solar = total_solar * self.area_m2 * SOLAR_EFFICIENCY / 1000.0
        self._attr_native_value = round(q_solar, 3)

    async def async_update(self):
        self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for sensor in self.solar_sensors:
            self.async_on_remove(
                async_track_state_change_event(self.hass, sensor, self._handle_change)
            )

    async def _handle_change(self, event):
        self._compute_value()
        self.async_write_ha_state()


class NetHeatDemandSensor(BaseUtilitySensor):
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        outdoor_sensor: str,
        solar_sensor: list[str],
        area_m2: float,
        energy_label: str,
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
        self.outdoor_sensor = outdoor_sensor
        self.solar_sensors = solar_sensor
        self.area_m2 = area_m2
        self.energy_label = energy_label

    def _compute_value(self) -> None:
        outdoor_state = self.hass.states.get(self.outdoor_sensor)
        if outdoor_state is None or outdoor_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            return
        try:
            t_outdoor = float(outdoor_state.state)
        except ValueError:
            self._attr_available = False
            return

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
        q_loss = self.area_m2 * u_value * (INDOOR_TEMPERATURE - t_outdoor) / 1000.0
        q_solar = solar_total * self.area_m2 * SOLAR_EFFICIENCY / 1000.0
        q_net = max(q_loss - q_solar, 0.0)
        self._attr_native_value = round(q_net, 3)

    async def async_update(self):
        self._compute_value()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.outdoor_sensor, self._handle_change
            )
        )
        for sensor in self.solar_sensors:
            self.async_on_remove(
                async_track_state_change_event(self.hass, sensor, self._handle_change)
            )

    async def _handle_change(self, event):
        self._compute_value()
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    price_settings = entry.options.get(
        CONF_PRICE_SETTINGS, entry.data.get(CONF_PRICE_SETTINGS, {})
    )
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heatpump Curve Optimizer",
    )
    entities: list[BaseUtilitySensor] = []

    area_m2 = entry.data.get(CONF_AREA_M2)
    energy_label = entry.data.get(CONF_ENERGY_LABEL)
    outdoor_sensor = entry.data.get(CONF_OUTDOOR_TEMPERATURE)
    solar_sensor = entry.data.get(CONF_SOLAR_FORECAST)
    if isinstance(solar_sensor, str):
        solar_sensor = [solar_sensor]

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

    if outdoor_sensor and area_m2 and energy_label:
        entities.append(
            HeatLossSensor(
                hass=hass,
                name="Hourly Heat Loss",
                unique_id=f"{DOMAIN}_hourly_heat_loss",
                outdoor_sensor=outdoor_sensor,
                area_m2=float(area_m2),
                energy_label=energy_label,
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

    if outdoor_sensor and solar_sensor and area_m2 and energy_label:
        entities.append(
            NetHeatDemandSensor(
                hass=hass,
                name="Hourly Net Heat Demand",
                unique_id=f"{DOMAIN}_hourly_net_heat_demand",
                outdoor_sensor=outdoor_sensor,
                solar_sensor=solar_sensor,
                area_m2=float(area_m2),
                energy_label=energy_label,
                icon="mdi:fire",
                device=device_info,
            )
        )

    if entities:
        UTILITY_ENTITIES.extend(ent for ent in entities if ent not in UTILITY_ENTITIES)

    async_add_entities(entities, True)

    hass.data[DOMAIN]["entities"] = {ent.entity_id: ent for ent in entities}
