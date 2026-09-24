"""Heat calculation coordinator for the Heating Curve Optimizer integration."""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA_M2,
    CONF_CEILING_HEIGHT,
    CONF_ENERGY_LABEL,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_INDOOR_TEMP_HYSTERESIS,
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_PV_EFFICIENCY_FACTOR,
    CONF_PV_ORIENTATION,
    CONF_PV_PEAK_POWER_KWP,
    CONF_PV_TILT,
    CONF_TARGET_INDOOR_TEMP,
    CONF_VENTILATION_TYPE,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
    DEFAULT_PV_EFFICIENCY_FACTOR,
    DEFAULT_PV_ORIENTATION_DEG,
    DEFAULT_PV_TILT,
    DEFAULT_TARGET_INDOOR_TEMP,
    DEFAULT_VENTILATION_TYPE,
    DOMAIN,
    INDOOR_TEMPERATURE,
    calculate_htc_from_energy_label,
)
from .coordinator_weather import WeatherDataCoordinator, _update_failed

_LOGGER = logging.getLogger(__name__)


class HeatCalculationCoordinator(DataUpdateCoordinator):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Coordinator for heat loss, solar gain, and PV production calculations."""

    def __init__(
        self,
        hass: HomeAssistant,
        weather_coordinator: WeatherDataCoordinator,
        config: dict[str, Any],
        entry_id: str,
        config_entry: ConfigEntry | None = None,
    ):
        """Initialize the heat calculation coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Heat Calculations",
            update_interval=timedelta(minutes=5),
            config_entry=config_entry,
        )
        self.weather_coordinator = weather_coordinator
        self.config = config
        self._entry_id = entry_id
        self._indoor_temp_sensor = config.get(CONF_INDOOR_TEMPERATURE_SENSOR)
        self._unsub = None

    @property
    def has_real_indoor_sensor(self) -> bool:
        """Whether a real indoor-temperature sensor is configured.

        Without one, `data["indoor_temperature"]` is the fixed
        `INDOOR_TEMPERATURE` fallback, not a real measurement - calibration
        (calibration.py) must never treat that fallback as ground truth.
        """
        return bool(self._indoor_temp_sensor)

    async def async_setup(self) -> None:
        """Set up event tracking for indoor temperature changes."""
        if self._indoor_temp_sensor:
            self._unsub = async_track_state_change_event(
                self.hass,
                [self._indoor_temp_sensor],
                self._handle_indoor_temp_change,
            )
            _LOGGER.debug(
                "Tracking indoor temperature sensor: %s", self._indoor_temp_sensor
            )

    async def _handle_indoor_temp_change(self, event: Event) -> None:
        """Handle indoor temperature changes with debouncing."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if not old_state or not new_state:
            return

        try:
            old_temp = float(old_state.state)
            new_temp = float(new_state.state)
            # Only update if temperature changed by more than 0.5°C
            if abs(new_temp - old_temp) >= 0.5:
                _LOGGER.debug(
                    "Indoor temperature changed significantly: %.1f -> %.1f",
                    old_temp,
                    new_temp,
                )
                await self.async_request_refresh()
        except (ValueError, TypeError):
            pass

    async def async_shutdown(self) -> None:
        """Clean up event tracking."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Calculate heat loss, solar gain, and PV production."""
        # Get weather data from coordinator
        weather_data = self.weather_coordinator.data
        if not weather_data:
            # A persistent, user-actionable problem (unlike a single failed
            # refresh, which DataUpdateCoordinator already surfaces via
            # entity unavailability) - surface it in Settings > Repairs too,
            # mirroring battery_controller's ForecastCoordinator.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"weather_data_unavailable_{self._entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="weather_data_unavailable",
            )
            raise _update_failed("no_weather_data")
        ir.async_delete_issue(
            self.hass, DOMAIN, f"weather_data_unavailable_{self._entry_id}"
        )

        # Get configuration
        area_m2 = self.config.get(CONF_AREA_M2)
        energy_label = self.config.get(CONF_ENERGY_LABEL)

        if not area_m2 or not energy_label:
            raise _update_failed("missing_building_config")

        # Get indoor temperature
        indoor_temp = INDOOR_TEMPERATURE
        if self._indoor_temp_sensor:
            indoor_state = self.hass.states.get(self._indoor_temp_sensor)
            if indoor_state and indoor_state.state not in ("unknown", "unavailable"):
                try:
                    indoor_temp = float(indoor_state.state)
                except (ValueError, TypeError):
                    pass

        # Get target temperature and hysteresis from entry.options (written live by
        # the number entities in number.py), falling back to zone config then defaults.
        # entry.options is the authoritative store - no more hass.data["runtime"] reads.
        entry_options: dict[str, Any] = (
            self.config_entry.options if self.config_entry is not None else {}
        )

        def _live(key: str, config_default: float) -> float:
            val = entry_options.get(key)
            if val is not None:
                return float(val)
            return float(self.config.get(key, config_default))

        target_temp = _live(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP)

        # Get separate lower and upper hysteresis values
        # Lower hysteresis: how far below target before heat pump turns ON
        # Upper hysteresis: how far above target before heat pump turns OFF
        legacy_hysteresis = self.config.get(
            CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER
        )
        hysteresis_lower = _live(
            CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
            self.config.get(CONF_INDOOR_TEMP_HYSTERESIS_LOWER, legacy_hysteresis),
        )
        hysteresis_upper = _live(
            CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
            self.config.get(CONF_INDOOR_TEMP_HYSTERESIS_UPPER, legacy_hysteresis),
        )

        # Calculate heat demand factor based on indoor temp vs target
        # Lower bound: target - lower_hysteresis (heat pump turns ON below this)
        # Upper bound: target + upper_hysteresis (heat pump turns OFF above this)
        lower_bound = target_temp - hysteresis_lower
        upper_bound = target_temp + hysteresis_upper
        total_band = hysteresis_lower + hysteresis_upper

        if indoor_temp <= lower_bound:
            # Room is cold, need full heating + extra to catch up
            temp_deficit = lower_bound - indoor_temp
            heat_demand_factor = 1.0 + (temp_deficit * 0.5)  # 50% extra per °C below
        elif indoor_temp >= upper_bound:
            # Room is warm enough, no heating needed (heat pump OFF)
            heat_demand_factor = 0.0
        else:
            # In the hysteresis band, linear reduction from 1.0 to 0.0
            heat_demand_factor = (upper_bound - indoor_temp) / total_band

        # Calculate HTC (Heat Transfer Coefficient)
        ventilation_type = self.config.get(
            CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE
        )
        ceiling_height = float(
            self.config.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)
        )

        htc = calculate_htc_from_energy_label(
            energy_label,
            area_m2,
            ventilation_type=ventilation_type,
            ceiling_height=ceiling_height,
        )

        # Calculate current heat loss (base calculation)
        outdoor_temp = weather_data["current_temperature"]
        base_heat_loss = htc * (indoor_temp - outdoor_temp) / 1000  # Convert to kW

        # Apply heat demand factor based on target temperature
        heat_loss = base_heat_loss * heat_demand_factor

        # Calculate heat loss forecast (use target_temp for forecast, not current indoor_temp)
        # This assumes the room will reach target temperature
        heat_loss_forecast = [
            htc * (target_temp - t) / 1000 for t in weather_data["temperature_forecast"]
        ]

        # Calculate solar gain
        solar_gain, solar_forecast = await self.hass.async_add_executor_job(
            self._calculate_solar_gain,
            weather_data["radiation_forecast"],
        )

        # Calculate PV production
        pv_forecast = await self.hass.async_add_executor_job(
            self._calculate_pv_production,
            weather_data["radiation_forecast"],
        )

        # Calculate net heat loss (heat loss - solar gain)
        net_heat_loss = heat_loss - solar_gain
        net_forecast = [h - s for h, s in zip(heat_loss_forecast, solar_forecast)]

        # Determine if heat pump should be ON based on heat demand factor
        # Heat pump is ON when there's any positive demand
        heat_pump_on = heat_demand_factor > 0.0

        result = {
            "heat_loss": round(heat_loss, 3),
            "heat_loss_base": round(base_heat_loss, 3),
            "heat_loss_forecast": [round(v, 3) for v in heat_loss_forecast],
            "solar_gain": round(solar_gain, 3),
            "solar_gain_forecast": [round(v, 3) for v in solar_forecast],
            "pv_production_forecast": [round(v, 3) for v in pv_forecast],
            "net_heat_loss": round(net_heat_loss, 3),
            "net_heat_loss_forecast": [round(v, 3) for v in net_forecast],
            "outdoor_temperature": outdoor_temp,
            "indoor_temperature": indoor_temp,
            "target_temperature": target_temp,
            "heat_demand_factor": round(heat_demand_factor, 3),
            "heat_pump_on": heat_pump_on,
            "hysteresis_lower": hysteresis_lower,
            "hysteresis_upper": hysteresis_upper,
            "lower_bound": round(lower_bound, 2),
            "upper_bound": round(upper_bound, 2),
            "timestamp": dt_util.utcnow(),
        }

        _LOGGER.debug(
            "Heat calculations: loss=%.2f kW (base=%.2f, factor=%.2f), "
            "solar=%.2f kW, net=%.2f kW, indoor=%.1f°C, target=%.1f°C",
            heat_loss,
            base_heat_loss,
            heat_demand_factor,
            solar_gain,
            net_heat_loss,
            indoor_temp,
            target_temp,
        )

        return result

    def _calculate_solar_gain(
        self, radiation_forecast: list[float]
    ) -> tuple[float, list[float]]:
        """Calculate solar gain through windows (blocking call)."""
        glass_east = float(self.config.get(CONF_GLASS_EAST_M2, 0))
        glass_south = float(self.config.get(CONF_GLASS_SOUTH_M2, 0))
        glass_west = float(self.config.get(CONF_GLASS_WEST_M2, 0))
        glass_u = float(self.config.get(CONF_GLASS_U_VALUE, 1.2))

        total_glass = glass_east + glass_south + glass_west

        if total_glass == 0 or not radiation_forecast:
            return 0.0, [0.0] * len(radiation_forecast)

        # SHGC (Solar Heat Gain Coefficient) approximation
        # Lower U-value glass typically has lower SHGC
        shgc = max(0.3, 0.7 - (glass_u - 0.8) * 0.2)

        # Orientation factors (how much radiation reaches each direction)
        # These are rough approximations for Netherlands latitude
        orientation_factors = {
            "east": 0.6,  # Morning sun
            "south": 1.0,  # Maximum sun exposure
            "west": 0.6,  # Afternoon sun
        }

        solar_forecast = []
        for radiation in radiation_forecast:
            # Calculate solar gain for each orientation
            gain = (
                glass_east * radiation * orientation_factors["east"] * shgc
                + glass_south * radiation * orientation_factors["south"] * shgc
                + glass_west * radiation * orientation_factors["west"] * shgc
            ) / 1000  # Convert W to kW

            solar_forecast.append(max(0.0, gain))

        current_solar = solar_forecast[0] if solar_forecast else 0.0

        return current_solar, solar_forecast

    def _calculate_pv_production(self, radiation_forecast: list[float]) -> list[float]:
        """Calculate PV production forecast (blocking call).

        Sums each configured PV-array subentry's own peak_power_kwp/
        orientation/tilt/efficiency_factor (const.py's PV_SUBENTRY_TYPE) -
        a smooth generalization of the old fixed east/south/west buckets to
        continuous orientation degrees, not a full solar-position model
        (battery_controller's forecast_models.py does that; deliberately
        out of scope here - "configure the same way", not "the same
        forecast algorithm").
        """
        pv_arrays = self.config.get("pv_arrays") or []
        if not pv_arrays or not radiation_forecast:
            return [0.0] * len(radiation_forecast)

        pv_forecast = []
        for radiation in radiation_forecast:
            production = 0.0
            for array in pv_arrays:
                peak_power_kwp = float(array.get(CONF_PV_PEAK_POWER_KWP, 0))
                if peak_power_kwp <= 0:
                    continue
                orientation = float(
                    array.get(CONF_PV_ORIENTATION, DEFAULT_PV_ORIENTATION_DEG)
                )
                tilt = float(array.get(CONF_PV_TILT, DEFAULT_PV_TILT))
                efficiency_factor = float(
                    array.get(CONF_PV_EFFICIENCY_FACTOR, DEFAULT_PV_EFFICIENCY_FACTOR)
                )

                # 0.65 + 0.35*cos(orientation - south): south (180°) gives
                # 1.0, east/west (90°/270°) give 0.65 - matching the old
                # fixed-bucket model's own values exactly at those three
                # angles, smoothly interpolated (and extrapolated to
                # north, 0.30) in between rather than a discontinuous
                # 3-bucket lookup.
                orientation_factor = 0.65 + 0.35 * math.cos(
                    math.radians(orientation - 180.0)
                )
                # Tilt factor (how much radiation is affected by panel
                # angle). Optimal tilt for Netherlands is ~35°
                # (DEFAULT_PV_TILT) - compared against that shared
                # constant rather than a second hardcoded 35 so the two
                # can't silently drift apart.
                tilt_factor = (
                    1.0
                    if tilt == DEFAULT_PV_TILT
                    else max(0.7, 1.0 - abs(tilt - DEFAULT_PV_TILT) * 0.01)
                )

                # Formula: Power (kW) = kWp * (radiation / 1000) * factors
                # radiation is in W/m², 1000 W/m² is STC (Standard Test
                # Conditions).
                production += (
                    peak_power_kwp
                    * radiation
                    * orientation_factor
                    * tilt_factor
                    * efficiency_factor
                    / 1000
                )

            pv_forecast.append(max(0.0, production))

        return pv_forecast
