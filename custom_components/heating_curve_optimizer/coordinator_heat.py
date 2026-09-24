"""Heat calculation coordinator for the Heating Curve Optimizer integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .building_model import BuildingConfig
from .const import (
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_PV_EFFICIENCY_FACTOR,
    CONF_PV_ORIENTATION,
    CONF_PV_PEAK_POWER_KWP,
    CONF_PV_TILT,
    CONF_TARGET_INDOOR_TEMP,
    DEFAULT_GLASS_U_VALUE,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
    DEFAULT_PV_EFFICIENCY_FACTOR,
    DEFAULT_PV_ORIENTATION_DEG,
    DEFAULT_PV_TILT,
    DEFAULT_TARGET_INDOOR_TEMP,
    DOMAIN,
    ENTITY_MANAGED_OPTIONS,
)
from .coordinator_weather import WeatherDataCoordinator, _update_failed
from .helpers import calculate_pv_forecast, calculate_window_solar_gain

_LOGGER = logging.getLogger(__name__)

INDOOR_SOURCE_SENSOR = "sensor"
INDOOR_SOURCE_TARGET = "target_fallback"


class HeatCalculationCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Heat loss, solar gain, internal gains and PV production for one zone.

    ``live_options`` is True for the primary zone only: its target
    temperature and hysteresis are adjusted at runtime through the number/
    climate entities, which persist them in ``entry.options``. Additional
    zones take those values from their own subentry data.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        weather_coordinator: WeatherDataCoordinator,
        config: dict[str, Any],
        zone_id: str,
        *,
        live_options: bool = False,
    ) -> None:
        """Initialize the heat calculation coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"Heat Calculations ({zone_id})",
            update_interval=timedelta(minutes=5),
        )
        self.weather_coordinator = weather_coordinator
        self.config = config
        self.zone_id = zone_id
        self._live_options = live_options
        self._indoor_temp_sensor: str | None = config.get(
            CONF_INDOOR_TEMPERATURE_SENSOR
        )
        self._unsub: Any = None

    @property
    def has_real_indoor_sensor(self) -> bool:
        """Whether a real indoor-temperature sensor is configured."""
        return bool(self._indoor_temp_sensor)

    def effective_config(self) -> dict[str, Any]:
        """Zone config with the live comfort settings merged in."""
        config = dict(self.config)
        if self._live_options and self.config_entry is not None:
            for key in ENTITY_MANAGED_OPTIONS:
                value = self.config_entry.options.get(key)
                if value is not None:
                    config[key] = value
        return config

    async def async_setup(self) -> None:
        """Track the indoor temperature sensor."""
        if self._indoor_temp_sensor:
            self._unsub = async_track_state_change_event(
                self.hass,
                [self._indoor_temp_sensor],
                self._handle_indoor_temp_change,
            )

    async def _handle_indoor_temp_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Refresh on a significant indoor temperature change."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not old_state or not new_state:
            return
        try:
            if abs(float(new_state.state) - float(old_state.state)) >= 0.5:
                await self.async_request_refresh()
        except (ValueError, TypeError):
            return

    async def async_shutdown(self) -> None:
        """Clean up event tracking."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        await super().async_shutdown()

    def _read_indoor_temperature(self, target_temp: float) -> tuple[float, str]:
        """Measured indoor temperature, or the target temperature as fallback.

        The fallback is the target rather than a fixed constant: assuming
        the house sits exactly at its setpoint keeps the optimizer neutral,
        where a fixed value above the comfort band would make it believe
        the house is permanently too warm.
        """
        issue_id = f"indoor_sensor_unavailable_{self.zone_id}"
        if not self._indoor_temp_sensor:
            return target_temp, INDOOR_SOURCE_TARGET
        state = self.hass.states.get(self._indoor_temp_sensor)
        if state is not None and state.state not in ("unknown", "unavailable"):
            try:
                value = float(state.state)
            except (ValueError, TypeError):
                pass
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                return value, INDOOR_SOURCE_SENSOR
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="indoor_sensor_unavailable",
            translation_placeholders={"sensor": self._indoor_temp_sensor},
        )
        return target_temp, INDOOR_SOURCE_TARGET

    async def _async_update_data(self) -> dict[str, Any]:
        """Calculate heat loss, gains and PV production."""
        weather_data = self.weather_coordinator.data
        if not weather_data:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "weather_data_unavailable",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="weather_data_unavailable",
            )
            raise _update_failed("no_weather_data")
        ir.async_delete_issue(self.hass, DOMAIN, "weather_data_unavailable")

        config = self.effective_config()
        building = BuildingConfig.from_config(config)
        if building.area_m2 <= 0:
            raise _update_failed("missing_building_config")

        target_temp = float(
            config.get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP)
        )
        hysteresis_lower = float(
            config.get(
                CONF_INDOOR_TEMP_HYSTERESIS_LOWER, DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER
            )
        )
        hysteresis_upper = float(
            config.get(
                CONF_INDOOR_TEMP_HYSTERESIS_UPPER, DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER
            )
        )
        indoor_temp, indoor_source = self._read_indoor_temperature(target_temp)

        lower_bound = target_temp - hysteresis_lower
        upper_bound = target_temp + hysteresis_upper
        if indoor_temp <= lower_bound:
            heat_demand_factor = 1.0 + (lower_bound - indoor_temp) * 0.5
        elif indoor_temp >= upper_bound:
            heat_demand_factor = 0.0
        else:
            heat_demand_factor = (upper_bound - indoor_temp) / (
                hysteresis_lower + hysteresis_upper
            )

        outdoor_temp = float(weather_data["current_temperature"])
        temp_forecast: list[float] = weather_data["temperature_forecast"]
        heat_loss = building.heat_loss_kw(indoor_temp, outdoor_temp)
        heat_loss_forecast = [
            building.heat_loss_kw(target_temp, t) for t in temp_forecast
        ]

        timestamps = [
            weather_data["forecast_start_utc"] + timedelta(hours=i)
            for i in range(len(weather_data["radiation_forecast"]))
        ]
        solar_forecast = await self.hass.async_add_executor_job(
            self._calculate_solar_gain, weather_data, timestamps
        )
        pv_forecast = await self.hass.async_add_executor_job(
            self._calculate_pv_production, weather_data, timestamps
        )
        solar_gain = solar_forecast[0] if solar_forecast else 0.0
        internal_gain = building.internal_gain_kw

        net_heat_loss = heat_loss - solar_gain - internal_gain
        net_forecast = [
            loss
            - (solar_forecast[i] if i < len(solar_forecast) else 0.0)
            - internal_gain
            for i, loss in enumerate(heat_loss_forecast)
        ]

        return {
            "heat_loss": round(heat_loss, 3),
            "heat_loss_forecast": [round(v, 3) for v in heat_loss_forecast],
            "solar_gain": round(solar_gain, 3),
            "solar_gain_forecast": [round(v, 3) for v in solar_forecast],
            "internal_gain": round(internal_gain, 3),
            "pv_production_forecast": [round(v, 3) for v in pv_forecast],
            "net_heat_loss": round(net_heat_loss, 3),
            "net_heat_loss_forecast": [round(v, 3) for v in net_forecast],
            "outdoor_temperature": outdoor_temp,
            "indoor_temperature": indoor_temp,
            "indoor_temperature_source": indoor_source,
            "target_temperature": target_temp,
            "hysteresis_lower": hysteresis_lower,
            "hysteresis_upper": hysteresis_upper,
            "lower_bound": round(lower_bound, 2),
            "upper_bound": round(upper_bound, 2),
            "heat_demand_factor": round(heat_demand_factor, 3),
            "heat_pump_on": heat_demand_factor > 0.0,
            "htc_w_per_k": round(building.ua_w_per_k, 1),
            "timestamp": dt_util.utcnow(),
        }

    def _calculate_solar_gain(
        self, weather_data: dict[str, Any], timestamps: list[Any]
    ) -> list[float]:
        """Solar gain through the zone's windows (blocking call)."""
        return calculate_window_solar_gain(
            weather_data["radiation_forecast"],
            {
                "east": float(self.config.get(CONF_GLASS_EAST_M2, 0.0)),
                "south": float(self.config.get(CONF_GLASS_SOUTH_M2, 0.0)),
                "west": float(self.config.get(CONF_GLASS_WEST_M2, 0.0)),
            },
            float(self.config.get(CONF_GLASS_U_VALUE, DEFAULT_GLASS_U_VALUE)),
            dni_forecast=weather_data.get("dni_forecast") or None,
            diffuse_forecast=weather_data.get("diffuse_forecast") or None,
            timestamps_utc=timestamps,
            latitude=self.weather_coordinator.latitude,
            longitude=self.weather_coordinator.longitude,
        )

    def _calculate_pv_production(
        self, weather_data: dict[str, Any], timestamps: list[Any]
    ) -> list[float]:
        """Combined PV production forecast of all configured arrays (blocking)."""
        radiation: list[float] = weather_data["radiation_forecast"]
        combined = [0.0] * len(radiation)
        for array in self.config.get("pv_arrays") or []:
            peak_power_kwp = float(array.get(CONF_PV_PEAK_POWER_KWP, 0))
            if peak_power_kwp <= 0:
                continue
            array_forecast = calculate_pv_forecast(
                radiation,
                peak_power_kwp=peak_power_kwp,
                orientation_deg=float(
                    array.get(CONF_PV_ORIENTATION, DEFAULT_PV_ORIENTATION_DEG)
                ),
                tilt_deg=float(array.get(CONF_PV_TILT, DEFAULT_PV_TILT)),
                efficiency_factor=float(
                    array.get(CONF_PV_EFFICIENCY_FACTOR, DEFAULT_PV_EFFICIENCY_FACTOR)
                ),
                dni_forecast=weather_data.get("dni_forecast") or None,
                diffuse_forecast=weather_data.get("diffuse_forecast") or None,
                timestamps_utc=timestamps,
                latitude=self.weather_coordinator.latitude,
                longitude=self.weather_coordinator.longitude,
            )
            for i, value in enumerate(array_forecast):
                combined[i] += value
        return combined
