"""Data update coordinators for the Heating Curve Optimizer integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers import issue_registry as ir, storage
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA_M2,
    CONF_ENERGY_LABEL,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_INDOOR_TEMP_HYSTERESIS,
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_K_FACTOR,
    CONF_OFFSET_DELTA_T,
    CONF_BASE_COP,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_PLANNING_WINDOW,
    CONF_TARGET_INDOOR_TEMP,
    CONF_TIME_BASE,
    CONF_MAX_BUFFER_DEBT,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_PV_EAST_WP,
    CONF_PV_SOUTH_WP,
    CONF_PV_WEST_WP,
    CONF_PV_TILT,
    CONF_POWER_CONSUMPTION,
    CONF_VENTILATION_TYPE,
    CONF_CEILING_HEIGHT,
    CONF_CONTROL_MODE,
    CONTROL_MODES,
    DEFAULT_CONTROL_MODE,
    MODE_FOLLOW_CURVE,
    MODE_OPTIMIZE_V2,
    CONF_GRID_IMPORT_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_EMITTER_TYPE,
    DEFAULT_EMITTER_TYPE,
    DEFAULT_REALTIME_INTERVAL_S,
    DEFAULT_COP_AT_35,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
    DEFAULT_K_FACTOR,
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_TARGET_INDOOR_TEMP,
    DEFAULT_TIME_BASE,
    DEFAULT_MAX_BUFFER_DEBT,
    DEFAULT_VENTILATION_TYPE,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_PV_TILT,
    DOMAIN,
    INDOOR_TEMPERATURE,
    calculate_htc_from_energy_label,
)
from .building_model import BuildingConfig, EmitterConfig
from .calibration import ThermalCalibrationState
from .calibration import STORAGE_VERSION as CALIBRATION_STORAGE_VERSION
from .heatpump_model import HeatPumpConfig
from .helpers import extract_price_forecast_with_interval
from .optimizer import calculate_buffer_energy, optimize_offsets
from .realtime_controller import RealtimeController, create_realtime_controller
from .thermal_optimizer import optimize_thermal_schedule

_LOGGER = logging.getLogger(__name__)

# Plain-English fallback for each UpdateFailed translation_key, mirroring
# strings.json's "exceptions" messages - used only on an HA release old
# enough that UpdateFailed still extends plain Exception rather than
# HomeAssistantError (confirmed the case for this repo's own test
# environment, HA 2024.3.3: UpdateFailed(translation_domain=...) raises
# TypeError there, "takes no keyword arguments"). On such a release the
# translation_domain/translation_key/translation_placeholders kwargs
# cannot be passed at all, so _update_failed() below falls back to a
# formatted message instead of crashing setup with a TypeError.
_UPDATE_FAILED_MESSAGES: dict[str, str] = {
    "api_error": "Open-Meteo API returned status {status}.",
    "connection_error": "Error fetching weather data from open-meteo.com: {error}.",
    "no_forecast_data": "No forecast data in the open-Meteo API response.",
    "no_weather_data": "Weather coordinator has no data yet.",
    "missing_building_config": "Missing area or energy label configuration.",
    "no_heat_data": "Heat calculation coordinator has no data yet.",
    "no_weather_data_for_optimization": "No weather data available for optimization.",
    "no_price_sensor": "No electricity price sensor is configured.",
    "price_sensor_unavailable": "Price sensor {sensor} is unavailable.",
    "price_data_extraction_failed": "Cannot extract price data from sensor {sensor}.",
}


def _update_failed(
    translation_key: str, translation_placeholders: dict[str, str] | None = None
) -> UpdateFailed:
    """Build an UpdateFailed with translation support where the installed
    HA's UpdateFailed accepts it, falling back to a formatted plain-string
    message on an HA release old enough that it doesn't (see the module-
    level comment above _UPDATE_FAILED_MESSAGES)."""
    try:
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders=translation_placeholders,
        )
    except TypeError:
        message = _UPDATE_FAILED_MESSAGES[translation_key].format(
            **(translation_placeholders or {})
        )
        return UpdateFailed(message)


def _calculate_supply_temp_from_curve(
    outdoor_temp: float,
    min_supply: float,
    max_supply: float,
    min_outdoor: float,
    max_outdoor: float,
) -> float:
    """Calculate supply temperature from heating curve parameters."""
    if outdoor_temp <= min_outdoor:
        return max_supply
    if outdoor_temp >= max_outdoor:
        return min_supply
    ratio = (outdoor_temp - min_outdoor) / (max_outdoor - min_outdoor)
    return max_supply + (min_supply - max_supply) * ratio


def _calculate_cop(
    supply_temp: float,
    outdoor_temp: float,
    base_cop: float,
    k_factor: float,
    outdoor_temp_coefficient: float,
    cop_compensation: float,
) -> float:
    """Calculate COP from supply and outdoor temperatures."""
    cop = (
        base_cop
        + outdoor_temp_coefficient * outdoor_temp
        - k_factor * (supply_temp - 35)
    ) * cop_compensation
    return max(0.5, cop)


class WeatherDataCoordinator(DataUpdateCoordinator):
    """Coordinator for weather and radiation data from open-meteo.com."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the weather data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Weather Data",
            update_interval=timedelta(minutes=30),
        )
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = async_get_clientsession(hass)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch weather and radiation data from open-meteo.com."""
        _LOGGER.debug(
            "Fetching weather data for %.4f, %.4f", self.latitude, self.longitude
        )

        # Combine temperature, humidity, and radiation in one API call
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m,relative_humidity_2m,shortwave_radiation"
            "&current_weather=true&timezone=UTC&forecast_days=2"
        )

        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    raise _update_failed(
                        "api_error",
                        translation_placeholders={"status": str(resp.status)},
                    )
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise _update_failed(
                "connection_error",
                translation_placeholders={"error": str(err)},
            ) from err

        # Extract current weather
        current_weather = data.get("current_weather", {})
        current_temp = float(current_weather.get("temperature", 0))

        # Extract hourly forecasts
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        humidity = hourly.get("relative_humidity_2m", [])
        radiation = hourly.get("shortwave_radiation", [])

        if not times or not temps:
            raise _update_failed("no_forecast_data")

        # Find current hour index
        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        start_idx = 0
        for i, ts in enumerate(times):
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if t >= now:
                start_idx = i
                break

        # Extract next 48 hours (2 days)
        temp_forecast = [float(v) for v in temps[start_idx : start_idx + 48]]
        humidity_forecast = (
            [float(v) for v in humidity[start_idx : start_idx + 48]] if humidity else []
        )
        radiation_forecast = (
            [float(v) for v in radiation[start_idx : start_idx + 48]]
            if radiation
            else []
        )

        result = {
            "current_temperature": round(current_temp, 2),
            "temperature_forecast": [round(v, 2) for v in temp_forecast],
            "humidity_forecast": [round(v, 1) for v in humidity_forecast],
            "radiation_forecast": [round(v, 1) for v in radiation_forecast],
            "timestamp": dt_util.utcnow(),
        }

        _LOGGER.debug(
            "Weather data updated: current=%.1f°C, forecast=%d hours",
            current_temp,
            len(temp_forecast),
        )

        return result


class HeatCalculationCoordinator(DataUpdateCoordinator):
    """Coordinator for heat loss, solar gain, and PV production calculations."""

    def __init__(
        self,
        hass: HomeAssistant,
        weather_coordinator: WeatherDataCoordinator,
        config: dict[str, Any],
        entry_id: str,
    ):
        """Initialize the heat calculation coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Heat Calculations",
            update_interval=timedelta(minutes=5),
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

        # Get target temperature and hysteresis from runtime data (number entities) or
        # config. Runtime data is keyed per entry_id (see number.py) so multiple config
        # entries don't share one target temperature.
        runtime = (
            self.hass.data.get(DOMAIN, {}).get("runtime", {}).get(self._entry_id, {})
        )
        target_temp = runtime.get(
            CONF_TARGET_INDOOR_TEMP,
            self.config.get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP),
        )

        # Get separate lower and upper hysteresis values
        # Lower hysteresis: how far below target before heat pump turns ON
        # Upper hysteresis: how far above target before heat pump turns OFF
        hysteresis_lower = runtime.get(
            CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
            self.config.get(
                CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
                # Fallback to legacy symmetric hysteresis
                self.config.get(
                    CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER
                ),
            ),
        )
        hysteresis_upper = runtime.get(
            CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
            self.config.get(
                CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
                # Fallback to legacy symmetric hysteresis
                self.config.get(
                    CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER
                ),
            ),
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
        """Calculate PV production forecast (blocking call)."""
        pv_east = float(self.config.get(CONF_PV_EAST_WP, 0))
        pv_south = float(self.config.get(CONF_PV_SOUTH_WP, 0))
        pv_west = float(self.config.get(CONF_PV_WEST_WP, 0))
        pv_tilt = float(self.config.get(CONF_PV_TILT, DEFAULT_PV_TILT))

        total_pv = pv_east + pv_south + pv_west

        if total_pv == 0 or not radiation_forecast:
            return [0.0] * len(radiation_forecast)

        # System efficiency (inverter + wiring + temperature losses)
        system_efficiency = 0.85

        # Tilt factor (how much radiation is affected by panel angle)
        # Optimal tilt for Netherlands is ~35°
        tilt_factor = 1.0 if pv_tilt == 35 else max(0.7, 1.0 - abs(pv_tilt - 35) * 0.01)

        # Orientation factors for PV panels
        orientation_factors = {
            "east": 0.65,
            "south": 1.0,
            "west": 0.65,
        }

        pv_forecast = []
        for radiation in radiation_forecast:
            # Calculate production for each orientation
            # Formula: Power (W) = Wp * (radiation / 1000) * efficiency
            # radiation is in W/m², 1000 W/m² is STC (Standard Test Conditions)
            production = (
                (
                    pv_east * radiation * orientation_factors["east"]
                    + pv_south * radiation * orientation_factors["south"]
                    + pv_west * radiation * orientation_factors["west"]
                )
                * tilt_factor
                * system_efficiency
                / 1000
                / 1000
            )  # First /1000 for STC, second for W to kW

            pv_forecast.append(max(0.0, production))

        return pv_forecast


class OptimizationCoordinator(DataUpdateCoordinator):
    """Coordinator for heating curve optimization using dynamic programming."""

    def __init__(
        self,
        hass: HomeAssistant,
        heat_coordinator: HeatCalculationCoordinator,
        config: dict[str, Any],
        entry_id: str = "",
    ):
        """Initialize the optimization coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Heating Optimization",
            update_interval=timedelta(minutes=15),
        )
        self.heat_coordinator = heat_coordinator
        self.config = config
        self._entry_id = entry_id
        self._price_sensor = config.get(CONF_CONSUMPTION_PRICE_SENSOR)
        self._unsub = None
        self._last_price = None
        self._current_buffer: float = 0.0  # Track actual buffer state
        self._current_offset: int = 0  # Track current offset for change constraint
        # Which optimizer drives optimized_offset (phase 3, REDESIGN.md).
        # Read from options first (select.py persists there, matching
        # battery_controller's BatteryControlModeSelect), falling back to
        # data, then the legacy default.
        self._control_mode: str = config.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        # Thermal calibration (phase 4, REDESIGN.md). Only set up when
        # entry_id is known (async_setup loads it from Store) - tests that
        # construct a coordinator without one, or without calling
        # async_setup, simply get no calibration, same as _unsub above.
        self._calibration: ThermalCalibrationState | None = None
        self._last_calibration_snapshot: dict[str, Any] | None = None
        # Real-time PV-surplus controller (phase 5b, REDESIGN.md). Only set
        # up in async_setup() when at least one grid sensor is configured -
        # otherwise the timer loop below never starts, so there is zero
        # overhead for the majority of installations that have not opted in.
        self._realtime_controller: RealtimeController | None = None
        self._unsub_realtime = None

    @property
    def control_mode(self) -> str:
        """Return the active control mode (legacy/follow_curve/optimize_v2)."""
        return self._control_mode

    @control_mode.setter
    def control_mode(self, value: str) -> None:
        """Set the active control mode; takes effect on the next update."""
        if value not in CONTROL_MODES:
            _LOGGER.warning("Ignoring unknown control mode: %s", value)
            return
        self._control_mode = value

    @property
    def thermal_calibration(self) -> ThermalCalibrationState | None:
        """Return the thermal calibration state, if async_setup has run."""
        return self._calibration

    async def async_reset_thermal_calibration(self) -> None:
        """Reset thermal calibration to the label-based prior (service handler)."""
        if self._calibration is None:
            _LOGGER.warning(
                "Cannot reset thermal calibration: not set up (entry_id missing "
                "or async_setup not called)"
            )
            return
        await self._calibration.async_reset()
        self._last_calibration_snapshot = None
        await self.async_request_refresh()

    async def async_setup(self) -> None:
        """Set up event tracking for price changes and load thermal calibration."""
        if self._price_sensor:
            self._unsub = async_track_state_change_event(
                self.hass,
                [self._price_sensor],
                self._handle_price_change,
            )
            _LOGGER.debug("Tracking price sensor: %s", self._price_sensor)

        if self._entry_id:
            self._calibration = ThermalCalibrationState(
                store=storage.Store(
                    self.hass,
                    CALIBRATION_STORAGE_VERSION,
                    f"{DOMAIN}_{self._entry_id}_thermal_calibration",
                )
            )
            await self._calibration.async_load()

        if self.config.get(CONF_GRID_IMPORT_SENSOR) or self.config.get(
            CONF_GRID_EXPORT_SENSOR
        ):
            self._realtime_controller = create_realtime_controller(self.config)
            self._unsub_realtime = async_track_time_interval(
                self.hass,
                self._handle_realtime_update,
                timedelta(seconds=DEFAULT_REALTIME_INTERVAL_S),
            )
            _LOGGER.debug(
                "Real-time PV-surplus controller active (import=%s, export=%s)",
                self.config.get(CONF_GRID_IMPORT_SENSOR),
                self.config.get(CONF_GRID_EXPORT_SENSOR),
            )

    async def _handle_price_change(self, event: Event) -> None:
        """Handle significant price changes."""
        new_state = event.data.get("new_state")
        if not new_state:
            return

        try:
            new_price = float(new_state.state)
            # Only trigger on significant price change (>10%)
            if self._last_price is not None:
                change_pct = abs(new_price - self._last_price) / self._last_price
                if change_pct >= 0.10:
                    _LOGGER.debug(
                        "Significant price change detected: %.2f%%, triggering optimization",
                        change_pct * 100,
                    )
                    await self.async_request_refresh()
            self._last_price = new_price
        except (ValueError, TypeError):
            pass

    async def async_shutdown(self) -> None:
        """Clean up event tracking."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_realtime:
            self._unsub_realtime()
            self._unsub_realtime = None

    def _read_grid_power_sensor_w(self, sensor_id: str) -> float | None:
        """Read one power sensor and convert its value to W.

        Sensors without a unit attribute are assumed to report W. An
        unrecognized power unit is skipped rather than guessed at -
        mirrors _read_power_consumption_kw's reasoning in calibration.py
        and battery_controller's _read_power_sensor_w.
        """
        state = self.hass.states.get(sensor_id)
        if not state or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None
        unit = state.attributes.get("unit_of_measurement", "W")
        if unit in ("W", ""):
            return value
        if unit == "kW":
            return value * 1000.0
        _LOGGER.debug(
            "Cannot use %s for the real-time controller: unrecognized power unit %r",
            sensor_id,
            unit,
        )
        return None

    def _get_realtime_grid_w(self) -> float | None:
        """Read current grid power: positive = import, negative = export.

        Returns None if neither sensor is configured, or neither has a
        usable reading - the caller must then skip this cycle rather than
        act on a fictitious 0 W.
        """
        import_sensor = self.config.get(CONF_GRID_IMPORT_SENSOR)
        export_sensor = self.config.get(CONF_GRID_EXPORT_SENSOR)
        if not import_sensor and not export_sensor:
            return None

        import_w = (
            self._read_grid_power_sensor_w(import_sensor) if import_sensor else None
        )
        export_w = (
            self._read_grid_power_sensor_w(export_sensor) if export_sensor else None
        )
        if import_w is None and export_w is None:
            return None

        return (import_w or 0.0) - (export_w or 0.0)

    async def _handle_realtime_update(self, now: datetime) -> None:
        """Periodic real-time update for the PV-surplus controller.

        Runs every DEFAULT_REALTIME_INTERVAL_S seconds. Only active while
        control_mode is optimize_v2 (the shadow price it needs only exists
        there) and a full DP cycle has already published a thermal_v2
        result this session - otherwise there is no planned offset or
        shadow price to adjust around yet.
        """
        if self._realtime_controller is None:
            return
        if self._control_mode != MODE_OPTIMIZE_V2:
            return
        if self.data is None:
            return

        thermal_v2 = self.data.get("thermal_v2", {})
        if not thermal_v2.get("available"):
            return

        current_grid_w = self._get_realtime_grid_w()
        if current_grid_w is None:
            _LOGGER.debug("Grid power sensor(s) unavailable; real-time update skipped")
            return

        time_base = int(self.config.get(CONF_TIME_BASE, DEFAULT_TIME_BASE))
        offset_delta_t = int(
            self.config.get(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T)
        )
        max_adjustment = max(1, time_base // max(1, offset_delta_t))

        planned_offsets = thermal_v2.get("offsets", [])
        planned_offset = planned_offsets[0] if planned_offsets else 0
        shadow_price = thermal_v2.get("shadow_price_eur_per_kwh", 0.0)

        action = self._realtime_controller.get_control_action(
            current_grid_w=current_grid_w,
            shadow_price_eur_per_kwh=shadow_price,
            planned_offset=planned_offset,
            max_adjustment=max_adjustment,
        )

        self.async_set_updated_data({**self.data, "realtime": action})

    async def _async_update_data(self) -> dict[str, Any]:
        """Run heating curve optimization."""
        # Get heat demand forecast
        heat_data = self.heat_coordinator.data
        if not heat_data:
            raise _update_failed("no_heat_data")

        demand_forecast = list(heat_data["net_heat_loss_forecast"])  # Make a copy

        # Get heat pump status
        heat_demand_factor = heat_data.get("heat_demand_factor", 1.0)
        heat_pump_on = heat_data.get("heat_pump_on", True)
        current_net_heat_loss = heat_data.get("net_heat_loss", 0.0)

        # Get time base for buffer calculations
        time_base = int(self.config.get(CONF_TIME_BASE, DEFAULT_TIME_BASE))
        step_hours = time_base / 60.0

        # Handle passive buffer changes when heat pump is OFF
        # The building exchanges heat with environment regardless of optimizer
        if not heat_pump_on:
            # Passive heat exchange: buffer changes by -net_heat_loss * time
            # net_heat_loss > 0: losing heat → buffer decreases
            # net_heat_loss < 0: solar gain → buffer increases
            passive_buffer_change = -current_net_heat_loss * step_hours
            self._current_buffer += passive_buffer_change

            _LOGGER.debug(
                "Heat pump OFF: passive buffer change %.3f kWh "
                "(net_loss=%.2f kW, buffer now=%.2f kWh)",
                passive_buffer_change,
                current_net_heat_loss,
                self._current_buffer,
            )

            # Set current demand to 0 for optimizer (heat pump not running)
            if demand_forecast:
                demand_forecast[0] = 0.0

        elif heat_demand_factor < 1.0 and demand_forecast:
            # Heat pump ON but with reduced demand - apply factor
            demand_forecast[0] = demand_forecast[0] * heat_demand_factor

        # Get outdoor temperature forecast from weather coordinator
        weather_data = self.heat_coordinator.weather_coordinator.data
        if not weather_data:
            raise _update_failed("no_weather_data_for_optimization")

        temp_forecast = weather_data["temperature_forecast"]

        # Get price forecast
        if not self._price_sensor:
            raise _update_failed("no_price_sensor")

        price_state = self.hass.states.get(self._price_sensor)
        if not price_state or price_state.state in ("unknown", "unavailable"):
            # Persistent, user-actionable (a misconfigured or broken price
            # sensor means the optimizer cannot run at all) - surface it in
            # Settings > Repairs too, mirroring battery_controller's
            # OptimizationCoordinator.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"price_sensor_unavailable_{self._entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="price_sensor_unavailable",
                translation_placeholders={"sensor": self._price_sensor},
            )
            raise _update_failed(
                "price_sensor_unavailable",
                translation_placeholders={"sensor": self._price_sensor},
            )
        ir.async_delete_issue(
            self.hass, DOMAIN, f"price_sensor_unavailable_{self._entry_id}"
        )

        price_forecast, price_interval = extract_price_forecast_with_interval(
            price_state
        )

        if not price_forecast:
            # Fallback to current price
            try:
                current_price = float(price_state.state)
                price_forecast = [current_price]
            except (ValueError, TypeError) as err:
                raise _update_failed(
                    "price_data_extraction_failed",
                    translation_placeholders={"sensor": self._price_sensor},
                ) from err

        # Phase 5 (REDESIGN.md §2.1.G): production price, for pricing PV
        # surplus used to cover heating at the feed-in rate rather than the
        # consumption rate. Optional - CONF_PRODUCTION_PRICE_SENSOR was
        # already a config key nothing read; a missing/unavailable sensor
        # here just means thermal_optimizer falls back to its own fixed
        # DEFAULT_FEED_IN_PRICE (never treats PV-covered heat as free -
        # same rule as a missing feed-in price generally, see
        # thermal_optimizer.py's module docstring).
        feed_in_price_forecast: list[float] | None = None
        production_price_sensor = self.config.get(CONF_PRODUCTION_PRICE_SENSOR)
        if production_price_sensor:
            production_price_state = self.hass.states.get(production_price_sensor)
            if production_price_state and production_price_state.state not in (
                "unknown",
                "unavailable",
            ):
                feed_in_price_forecast, _ = extract_price_forecast_with_interval(
                    production_price_state
                )
                if not feed_in_price_forecast:
                    try:
                        feed_in_price_forecast = [float(production_price_state.state)]
                    except (ValueError, TypeError):
                        feed_in_price_forecast = None

        # Get optimization parameters
        planning_window = int(
            self.config.get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW)
        )
        time_base = int(self.config.get(CONF_TIME_BASE, DEFAULT_TIME_BASE))
        offset_delta_t = int(
            self.config.get(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T)
        )
        max_buffer_debt = float(
            self.config.get(CONF_MAX_BUFFER_DEBT, DEFAULT_MAX_BUFFER_DEBT)
        )
        k_factor = float(self.config.get(CONF_K_FACTOR, DEFAULT_K_FACTOR))
        base_cop = float(self.config.get(CONF_BASE_COP, DEFAULT_COP_AT_35))
        outdoor_temp_coefficient = float(
            self.config.get(
                CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
            )
        )
        cop_compensation = float(
            self.config.get(
                CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
            )
        )

        # Get temperature limits
        min_supply = float(self.config.get(CONF_HEAT_CURVE_MIN, 20.0))
        max_supply = float(self.config.get(CONF_HEAT_CURVE_MAX, 45.0))
        min_outdoor = float(self.config.get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0))
        max_outdoor = float(self.config.get(CONF_HEAT_CURVE_MAX_OUTDOOR, 20.0))

        # Run optimization in executor (CPU-intensive)
        _LOGGER.debug(
            "Running optimization with %d demand points, buffer=%.2f, offset=%d",
            len(demand_forecast),
            self._current_buffer,
            self._current_offset,
        )
        result = await self.hass.async_add_executor_job(
            self._run_optimization,
            demand_forecast,
            price_forecast,
            temp_forecast,
            planning_window,
            time_base,
            offset_delta_t,
            max_buffer_debt,
            price_interval,
            k_factor,
            base_cop,
            outdoor_temp_coefficient,
            cop_compensation,
            min_supply,
            max_supply,
            min_outdoor,
            max_outdoor,
            self._current_buffer,
            self._current_offset,
        )

        # --- Shadow mode: redesigned thermal optimizer (phase 2) --------
        # Runs alongside the legacy optimizer above, using the exact same
        # forecasts, and is exposed only as a diagnostic sensor
        # (sensor_thermal_shadow.py) - it never drives anything yet. See
        # docs/redesign/REDESIGN.md phase 2/3 and docs/algorithm/
        # redesign-thermal-model.md for what it does and why.
        try:
            thermal_v2_result = await self.hass.async_add_executor_job(
                self._run_thermal_v2_optimization,
                demand_forecast,
                price_forecast,
                temp_forecast,
                heat_data.get("solar_gain_forecast", []),
                heat_data.get("indoor_temperature", 20.0),
                time_base,
                offset_delta_t,
                min_supply,
                max_supply,
                min_outdoor,
                max_outdoor,
                self._current_offset,
                heat_data.get("pv_production_forecast", []),
                feed_in_price_forecast,
            )
        except Exception as err:  # belt-and-braces: shadow mode must never
            # take the legacy result down with it, even on a dispatch-level
            # failure the inner try/except couldn't catch.
            _LOGGER.warning("Thermal v2 shadow optimization dispatch failed: %s", err)
            thermal_v2_result = {"available": False, "error": str(err)}
        thermal_v2_result["legacy_total_cost_eur"] = result.get("total_cost")
        result["thermal_v2"] = thermal_v2_result

        # --- Apply the active control mode (phase 3, REDESIGN.md) ---------
        # Decides which engine's result actually reaches optimized_offset /
        # optimized_offsets / future_supply_temperatures - i.e. what every
        # downstream sensor, and any automation built on them, actually
        # sees. Placed *before* the state-tracking block below, so
        # self._current_offset/self._current_buffer always reflect what was
        # really applied, whichever mode chose it - both engines read those
        # as their own "previous offset" next cycle (see
        # _run_thermal_v2_optimization's current_offset argument), so this
        # is what keeps ramp-rate limiting physically correct across a mode
        # switch instead of each engine drifting against its own private
        # what-if history.
        if self._control_mode == MODE_FOLLOW_CURVE:
            horizon = len(result.get("optimized_offsets") or [0])
            zero_offsets = [0] * max(horizon, 1)
            result["optimized_offset"] = 0
            result["optimized_offsets"] = zero_offsets
            result["future_supply_temperatures"] = result.get(
                "baseline_supply_temperatures", []
            )
            result["buffer_evolution"] = calculate_buffer_energy(
                zero_offsets,
                demand_forecast,
                time_base=time_base,
                buffer=self._current_buffer,
            )
            result["initial_buffer"] = round(self._current_buffer, 3)
            result["total_cost"] = result.get("baseline_cost", 0.0)
            result["cost_savings"] = 0.0

        elif self._control_mode == MODE_OPTIMIZE_V2:
            if thermal_v2_result.get("available"):
                v2_offsets = thermal_v2_result.get("offsets", [])
                v2_comfort_min = thermal_v2_result.get("building_comfort_min")
                v2_mass = thermal_v2_result.get("building_thermal_mass_kwh_per_k")
                v2_indoor_temps = thermal_v2_result.get("indoor_temps", [])
                # Re-express the v2 indoor-temperature trajectory as
                # "stored kWh above the comfort floor" so the existing
                # heat_buffer sensor keeps the same physical meaning
                # instead of going stale - see REDESIGN.md §2.1.B on why
                # that floor-relative energy is the right equivalent of
                # the legacy buffer, now backed by a real state variable.
                if v2_comfort_min is not None and v2_mass is not None:
                    v2_buffer = [
                        round((t - v2_comfort_min) * v2_mass, 3)
                        for t in v2_indoor_temps
                    ]
                else:
                    v2_buffer = []

                result["optimized_offset"] = v2_offsets[0] if v2_offsets else 0
                result["optimized_offsets"] = v2_offsets
                result["future_supply_temperatures"] = thermal_v2_result.get(
                    "supply_temps", []
                )
                result["buffer_evolution"] = v2_buffer
                result["initial_buffer"] = v2_buffer[0] if v2_buffer else 0.0
                result["total_cost"] = thermal_v2_result.get("total_cost_eur", 0.0)
                result["cost_savings"] = round(
                    result.get("baseline_cost", 0.0)
                    - thermal_v2_result.get("total_cost_eur", 0.0),
                    3,
                )
            else:
                _LOGGER.warning(
                    "control_mode=optimize_v2 but the redesigned optimizer is "
                    "unavailable (%s) - falling back to the legacy result this "
                    "cycle so heating control is never left without a decision.",
                    thermal_v2_result.get("error"),
                )
        # MODE_LEGACY (default): result is left exactly as _run_optimization
        # produced it - zero behaviour change for every installation that
        # has not explicitly switched control_mode.

        # Phase 4 (REDESIGN.md): feed one real observation into thermal
        # calibration, using whatever `result` now says was actually
        # applied (post mode-override, so the sample reflects reality
        # regardless of which engine is currently driving).
        await self._maybe_record_calibration_sample(
            indoor_temp=heat_data.get("indoor_temperature", INDOOR_TEMPERATURE),
            outdoor_temp=weather_data.get(
                "current_temperature", temp_forecast[0] if temp_forecast else 5.0
            ),
            solar_gain_kw=heat_data.get("solar_gain", 0.0),
            supply_temp=(
                result["future_supply_temperatures"][0]
                if result.get("future_supply_temperatures")
                else max(min_supply, min(max_supply, 35.0))
            ),
            heat_pump_on=heat_pump_on,
        )

        # Update tracked state for next optimization run. Note: buffer_evolution's
        # units/semantics depend on the active control mode (legacy debt/surplus
        # vs. the v2 comfort-floor-relative kWh above) - switching control_mode
        # therefore seeds the *other* mode's next run with a value computed
        # under different physics for one cycle. Bounded by max_buffer_debt on
        # the legacy side and self-corrects within a planning window; a real
        # unit conversion between the two is a refinement for later, not a
        # correctness issue while control_mode is left alone (the common case).
        new_offset = int(result.get("optimized_offset", 0))
        buffer_evolution = result.get("buffer_evolution", [])
        new_buffer = buffer_evolution[0] if buffer_evolution else self._current_buffer

        _LOGGER.debug(
            "Optimization complete: offset=%d°C (was %d), buffer=%.2f kWh (was %.2f), cost=%.3f",
            new_offset,
            self._current_offset,
            new_buffer,
            self._current_buffer,
            result.get("total_cost", 0),
        )

        self._current_offset = new_offset
        self._current_buffer = new_buffer

        if self._realtime_controller is not None:
            # A full DP cycle just established a new planned-offset baseline;
            # the real-time layer adjusts *around* that, so stale adjustment
            # memory from the previous baseline is discarded rather than
            # carried forward and silently compounding.
            self._realtime_controller.reset()

        return result

    def _run_optimization(
        self,
        demand_forecast: list[float],
        price_forecast: list[float],
        temp_forecast: list[float],
        planning_window: int,
        time_base: int,
        offset_delta_t: int,
        max_buffer_debt: float,
        price_interval: int,
        k_factor: float,
        base_cop: float,
        outdoor_temp_coefficient: float,
        cop_compensation: float,
        min_supply: float,
        max_supply: float,
        min_outdoor: float,
        max_outdoor: float,
        current_buffer: float,
        current_offset: int,
    ) -> dict[str, Any]:
        """Run DP optimization (blocking call in executor)."""
        try:
            # Limit forecasts to planning window
            max_steps = planning_window
            demand_limited = demand_forecast[:max_steps]
            price_limited = price_forecast[:max_steps]
            temp_limited = temp_forecast[:max_steps]

            # Call the optimizer with correct parameter names
            offsets, buffer_evolution = optimize_offsets(
                demand=demand_limited,
                prices=price_limited,
                base_temp=base_cop,
                k_factor=k_factor,
                cop_compensation_factor=cop_compensation,
                buffer=current_buffer,  # Use actual buffer state
                water_min=min_supply,
                water_max=max_supply,
                outdoor_temps=temp_limited,
                humidity_forecast=None,  # Not available yet
                outdoor_temp_coefficient=outdoor_temp_coefficient,
                time_base=time_base,
                outdoor_min=min_outdoor,
                outdoor_max=max_outdoor,
                max_buffer_debt=max_buffer_debt,  # Configurable heat debt limit
                current_offset=current_offset,  # Constrain first step change
                offset_delta_t=offset_delta_t,  # Minutes per 1°C offset change
            )

            # Calculate future supply temperatures and COP for both baseline and optimized
            future_supply_temps = []
            baseline_supply_temps = []
            baseline_cop_list = []
            optimized_cop_list = []
            step_hours = time_base / 60.0

            for i, offset in enumerate(offsets):
                if i < len(temp_limited):
                    outdoor_temp = temp_limited[i]
                    base_temp = _calculate_supply_temp_from_curve(
                        outdoor_temp, min_supply, max_supply, min_outdoor, max_outdoor
                    )
                    supply_temp = max(min(base_temp + offset, max_supply), min_supply)

                    baseline_supply_temps.append(round(base_temp, 1))
                    future_supply_temps.append(round(supply_temp, 1))
                    baseline_cop_list.append(
                        round(
                            _calculate_cop(
                                base_temp,
                                outdoor_temp,
                                base_cop,
                                k_factor,
                                outdoor_temp_coefficient,
                                cop_compensation,
                            ),
                            3,
                        )
                    )
                    optimized_cop_list.append(
                        round(
                            _calculate_cop(
                                supply_temp,
                                outdoor_temp,
                                base_cop,
                                k_factor,
                                outdoor_temp_coefficient,
                                cop_compensation,
                            ),
                            3,
                        )
                    )
                else:
                    # No temperature data, use min_supply as fallback
                    baseline_supply_temps.append(round(min_supply, 1))
                    future_supply_temps.append(round(min_supply, 1))
                    baseline_cop_list.append(3.0)
                    optimized_cop_list.append(3.0)

            # Calculate real costs: electricity cost = (heat_demand / COP) * time * price
            baseline_cost = 0.0
            optimized_cost = 0.0

            for i in range(len(offsets)):
                if i < len(demand_limited) and i < len(price_limited):
                    demand = max(0.0, demand_limited[i])
                    price = price_limited[i]
                    b_cop = baseline_cop_list[i]
                    o_cop = optimized_cop_list[i]

                    if b_cop > 0:
                        baseline_cost += (demand / b_cop) * step_hours * price
                    if o_cop > 0:
                        optimized_cost += (demand / o_cop) * step_hours * price

            cost_savings = baseline_cost - optimized_cost

            return {
                "optimized_offset": round(offsets[0], 1) if offsets else 0.0,
                "optimized_offsets": [round(v, 1) for v in offsets],
                "buffer_evolution": [round(v, 3) for v in buffer_evolution],
                "initial_buffer": round(current_buffer, 3),
                "previous_offset": current_offset,
                "future_supply_temperatures": future_supply_temps,
                "baseline_supply_temperatures": baseline_supply_temps,
                "baseline_cop": baseline_cop_list,
                "optimized_cop": optimized_cop_list,
                "baseline_cost": round(baseline_cost, 3),
                "total_cost": round(optimized_cost, 3),
                "cost_savings": round(cost_savings, 3),
                "prices": [round(p, 5) for p in price_limited],
                "demand_forecast": [round(d, 3) for d in demand_limited],
                "outdoor_forecast": [round(t, 1) for t in temp_limited],
                "timestamp": dt_util.utcnow(),
            }

        except Exception as err:
            _LOGGER.error("Optimization failed: %s", err, exc_info=True)
            # Return safe fallback
            return {
                "optimized_offset": 0.0,
                "optimized_offsets": [0.0],
                "buffer_evolution": [0.0],
                "total_cost": 0.0,
                "timestamp": dt_util.utcnow(),
                "error": str(err),
            }

    def _read_power_consumption_kw(self) -> float | None:
        """Read the configured heat-pump electricity meter, in kW.

        Returns None if not configured, unavailable, or its unit can't be
        determined - calibration.py's independence from the UA/thermal-mass
        prior depends on this being a real, correctly-scaled measurement,
        so a guessed unit (e.g. "big number must be Watts") is worse than
        no sample at all.
        """
        sensor_id = self.config.get(CONF_POWER_CONSUMPTION)
        if not sensor_id:
            return None
        state = self.hass.states.get(sensor_id)
        if not state or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None
        unit = state.attributes.get("unit_of_measurement", "")
        if unit == "kW":
            return value
        if unit == "W":
            return value / 1000.0
        _LOGGER.debug(
            "Cannot use %s for thermal calibration: unrecognized power unit %r",
            sensor_id,
            unit,
        )
        return None

    async def _maybe_record_calibration_sample(
        self,
        *,
        indoor_temp: float,
        outdoor_temp: float,
        solar_gain_kw: float,
        supply_temp: float,
        heat_pump_on: bool,
    ) -> None:
        """Feed one real observation into thermal calibration, if eligible.

        Sequencing: this cycle records what it believes was delivered
        (electricity meter x COP - independent of UA/thermal_mass, see
        calibration.py's module docstring); the *next* cycle compares the
        real indoor-temperature change since now against that belief. So
        the snapshot taken this cycle only completes next cycle's sample,
        never this one's - `self._last_calibration_snapshot` carries it
        forward.
        """
        if (
            self._calibration is None
            or not self.heat_coordinator.has_real_indoor_sensor
        ):
            return

        now = dt_util.utcnow()
        power_kw = self._read_power_consumption_kw()
        thermal_power_kw = None
        if power_kw is not None and heat_pump_on:
            heatpump = HeatPumpConfig.from_config(self.config)
            thermal_power_kw = power_kw * heatpump.cop_at(
                supply_temp=supply_temp, outdoor_temp=outdoor_temp
            )

        previous = self._last_calibration_snapshot
        if previous is not None and previous.get("heat_and_solar_kw") is not None:
            elapsed_hours = (now - previous["timestamp"]).total_seconds() / 3600.0
            # Skip startup gaps (near-zero elapsed) and long outages (HA
            # restart, network loss) - both make the observed rate
            # meaningless rather than merely noisy.
            if 0.05 <= elapsed_hours <= 3.0:
                building = BuildingConfig.from_config(self.config)
                if building.area_m2 > 0:
                    delta_t = previous["indoor_temp"] - previous["outdoor_temp"]
                    rate_c_per_h = (
                        indoor_temp - previous["indoor_temp"]
                    ) / elapsed_hours
                    self._calibration.record_sample(
                        delta_t=delta_t,
                        heat_and_solar_kw=previous["heat_and_solar_kw"],
                        rate_c_per_h=rate_c_per_h,
                        prior_ua_w_per_k=building.ua_w_per_k,
                        prior_thermal_mass_kwh_per_k=building.thermal_mass_kwh_per_k,
                    )
                    await self._calibration.async_save()

        heat_and_solar_kw = (
            None
            if thermal_power_kw is None
            else thermal_power_kw + max(0.0, solar_gain_kw)
        )
        self._last_calibration_snapshot = {
            "timestamp": now,
            "indoor_temp": indoor_temp,
            "outdoor_temp": outdoor_temp,
            "heat_and_solar_kw": heat_and_solar_kw,
        }

    def _run_thermal_v2_optimization(
        self,
        demand_forecast: list[float],
        price_forecast: list[float],
        temp_forecast: list[float],
        solar_gain_forecast: list[float],
        indoor_temperature: float,
        time_base: int,
        offset_delta_t: int,
        min_supply: float,
        max_supply: float,
        min_outdoor: float,
        max_outdoor: float,
        current_offset: int,
        pv_production_forecast: list[float] | None = None,
        feed_in_price_forecast: list[float] | None = None,
    ) -> dict[str, Any]:
        """Run the redesigned thermal DP optimizer (blocking call, executor).

        Shadow mode (docs/redesign/REDESIGN.md phase 2): runs alongside
        `_run_optimization` above, against the exact same forecasts, but its
        result only ever reaches a diagnostic sensor - never
        `self._current_offset`/`self._current_buffer`. Any failure here
        must never break the legacy optimization result this coordinator is
        still driving, so every error is caught and reported as
        `available: False` instead of propagating.

        `pv_production_forecast` (phase 5, REDESIGN.md §2.1.G) is passed
        straight through as `pv_surplus_kw`: this integration has no
        household consumption forecast to net production against, so the
        full modelled PV production is treated as available for heating -
        an overestimate of true surplus on days with concurrent household
        load, documented here rather than silently assumed. Refining this
        needs a consumption forecast, which does not exist yet.
        """
        try:
            building = BuildingConfig.from_config(self.config)
            if building.area_m2 <= 0:
                raise ValueError("area_m2 not configured")

            # Phase 4 (REDESIGN.md): once calibration has learned enough
            # from real operation, it overrides the label-based prior.
            calibration = self._calibration
            if calibration is not None and calibration.applied:
                building.ua_w_per_k = calibration.learned_ua_w_per_k
                building.thermal_mass_kwh_per_k = (
                    calibration.learned_thermal_mass_kwh_per_k
                )
                if building.ua_w_per_k > 0:
                    building.time_constant_hours = building.thermal_mass_kwh_per_k / (
                        building.ua_w_per_k / 1000.0
                    )

            emitter = EmitterConfig.sized_to_building(
                building,
                design_outdoor_temp=min_outdoor,
                design_supply_temp=max_supply,
                emitter_type=str(
                    self.config.get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE)
                ),
            )
            # No dedicated heat-pump-capacity config key exists yet (phase
            # 5 territory). Sized with headroom above what the emitter can
            # use at the design point, the way an installer would sanity
            # check pump vs. radiator sizing, rather than block shadow
            # mode on a new required field.
            heatpump = HeatPumpConfig.from_config(
                self.config, max_thermal_power_kw=emitter.nominal_power_kw * 1.3
            )

            horizon = min(len(demand_forecast), len(price_forecast), len(temp_forecast))
            thermal_result = optimize_thermal_schedule(
                building=building,
                heatpump=heatpump,
                emitter=emitter,
                outdoor_temps=temp_forecast[:horizon],
                prices=price_forecast[:horizon],
                initial_indoor_temp=indoor_temperature,
                solar_gain_kw=(
                    solar_gain_forecast[:horizon] if solar_gain_forecast else None
                ),
                pv_surplus_kw=(
                    pv_production_forecast[:horizon] if pv_production_forecast else None
                ),
                feed_in_prices=(
                    feed_in_price_forecast[:horizon] if feed_in_price_forecast else None
                ),
                time_base=time_base,
                offset_delta_t=offset_delta_t,
                water_min=min_supply,
                water_max=max_supply,
                outdoor_min=min_outdoor,
                outdoor_max=max_outdoor,
                current_offset=current_offset,
            )
            return {
                "available": True,
                "offset": thermal_result.offsets[0] if thermal_result.offsets else 0,
                "offsets": thermal_result.offsets,
                "supply_temps": thermal_result.supply_temps,
                "indoor_temps": thermal_result.indoor_temps,
                "thermal_power_kw": thermal_result.thermal_power_kw,
                "electrical_power_kw": thermal_result.electrical_power_kw,
                "cost_eur": thermal_result.cost_eur,
                "total_cost_eur": thermal_result.total_cost_eur,
                "shadow_price_eur_per_kwh": thermal_result.shadow_price_eur_per_kwh,
                "building_ua_w_per_k": round(building.ua_w_per_k, 1),
                "building_thermal_mass_kwh_per_k": round(
                    building.thermal_mass_kwh_per_k, 2
                ),
                "building_comfort_min": building.comfort_min,
                "building_time_constant_hours": round(building.time_constant_hours, 1),
                "emitter_nominal_power_kw": round(emitter.nominal_power_kw, 2),
                "heatpump_max_thermal_power_kw": round(
                    heatpump.max_thermal_power_kw, 2
                ),
                "calibration_applied": (
                    calibration.applied if calibration is not None else False
                ),
                "calibration_sample_count": (
                    calibration.sample_count if calibration is not None else 0
                ),
                "timestamp": dt_util.utcnow(),
            }
        except Exception as err:
            _LOGGER.warning(
                "Thermal v2 shadow optimization failed (legacy optimizer unaffected): %s",
                err,
            )
            return {
                "available": False,
                "error": str(err),
                "timestamp": dt_util.utcnow(),
            }
