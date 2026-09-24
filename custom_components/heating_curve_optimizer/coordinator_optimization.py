"""Optimization coordinator for the Heating Curve Optimizer integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import storage
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .building_model import BuildingConfig, EmitterConfig
from .calibration import (
    MIN_INDOOR_TEMP_DELTA_C,
    RESULT_ELAPSED_GAP,
    RESULT_MISSING_BUILDING_CONFIG,
    RESULT_NO_INDOOR_SENSOR,
    RESULT_NO_POWER_READING,
    ThermalCalibrationState,
)
from .calibration import (
    STORAGE_VERSION as CALIBRATION_STORAGE_VERSION,
)
from .const import (
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_EMITTER_TYPE,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_OFFSET_DELTA_T,
    CONF_PLANNING_WINDOW,
    CONF_POWER_CONSUMPTION,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    DEFAULT_EMITTER_TYPE,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_HEAT_CURVE_MAX_OUTDOOR,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_HEAT_CURVE_MIN_OUTDOOR,
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_REALTIME_INTERVAL_S,
    DOMAIN,
)
from .coordinator_heat import INDOOR_SOURCE_SENSOR, HeatCalculationCoordinator
from .coordinator_weather import _update_failed
from .heatpump_model import HeatPumpConfig
from .helpers import (
    extract_price_forecast_with_timestamps,
    get_sensor_value,
    max_offset_change,
    read_power_kw,
    resample_to_steps,
)
from .realtime_controller import RealtimeController, create_realtime_controller
from .thermal_optimizer import (
    DEFAULT_OFFSET_MAX,
    DEFAULT_OFFSET_MIN,
    ThermalOptimizationResult,
    optimize_thermal_schedule,
)

_LOGGER = logging.getLogger(__name__)

# Below this, the heat pump's electricity meter reading is treated as idle
# (standby draw), not an active heating cycle.
IDLE_POWER_THRESHOLD_KW = 0.1

# A price move of at least this fraction of the previous price, or at least
# PRICE_CHANGE_MIN_ABS €/kWh (whichever is larger in magnitude), triggers a
# re-optimization outside the regular schedule. The absolute floor keeps the
# check meaningful around zero and for negative prices.
PRICE_CHANGE_REL = 0.10
PRICE_CHANGE_MIN_ABS = 0.01

# Calibration: a sample spans from one snapshot until the indoor temperature
# has moved by MIN_INDOOR_TEMP_DELTA_C, within these elapsed-time bounds.
CALIBRATION_MIN_HOURS = 0.25
CALIBRATION_MAX_HOURS = 6.0

# Heat pump capacity headroom over the emitter's design-point output, used
# when no heat pump capacity is configured.
DEFAULT_HEATPUMP_HEADROOM = 1.3


def price_change_is_significant(old: float | None, new: float) -> bool:
    """Whether a price update warrants an immediate re-optimization."""
    if old is None:
        return False
    return abs(new - old) >= max(PRICE_CHANGE_MIN_ABS, PRICE_CHANGE_REL * abs(old))


class OptimizationCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Runs the thermal DP optimizer for one heating zone."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        heat_coordinator: HeatCalculationCoordinator,
        config: dict[str, Any],
        zone_id: str,
    ) -> None:
        """Initialize the optimization coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"Heating Optimization ({zone_id})",
            update_interval=timedelta(minutes=15),
        )
        self.heat_coordinator = heat_coordinator
        self.config = config
        self.zone_id = zone_id
        self._price_sensor: str | None = config.get(CONF_CONSUMPTION_PRICE_SENSOR)
        self._unsub: Any = None
        self._unsub_realtime: Any = None
        self._last_price: float | None = None
        self._current_offset = 0
        self._calibration: ThermalCalibrationState | None = None
        self._calibration_snapshot: dict[str, Any] | None = None
        self._realtime_controller: RealtimeController | None = None

    @property
    def thermal_calibration(self) -> ThermalCalibrationState | None:
        """Return the thermal calibration state, if set up."""
        return self._calibration

    async def async_reset_thermal_calibration(self) -> None:
        """Reset thermal calibration to the label-based prior."""
        if self._calibration is None:
            return
        await self._calibration.async_reset()
        self._calibration_snapshot = None
        await self.async_request_refresh()

    async def async_setup(self) -> None:
        """Track the price sensor, load calibration, start the real-time loop."""
        if self._price_sensor:
            self._unsub = async_track_state_change_event(
                self.hass, [self._price_sensor], self._handle_price_change
            )

        self._calibration = ThermalCalibrationState(
            store=storage.Store(
                self.hass,
                CALIBRATION_STORAGE_VERSION,
                f"{DOMAIN}_{self.zone_id}_thermal_calibration",
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

    async def async_shutdown(self) -> None:
        """Clean up event tracking."""
        for unsub in (self._unsub, self._unsub_realtime):
            if unsub:
                unsub()
        self._unsub = None
        self._unsub_realtime = None
        await super().async_shutdown()

    async def _handle_price_change(self, event: Event[EventStateChangedData]) -> None:
        """Re-optimize on a significant price change."""
        new_state = event.data.get("new_state")
        if not new_state:
            return
        try:
            new_price = float(new_state.state)
        except (ValueError, TypeError):
            return
        if price_change_is_significant(self._last_price, new_price):
            await self.async_request_refresh()
        self._last_price = new_price

    # ------------------------------------------------------------------
    # Real-time PV-surplus layer
    # ------------------------------------------------------------------

    def _get_realtime_grid_w(self) -> float | None:
        """Current grid power in W: positive = import, negative = export."""
        import_sensor = self.config.get(CONF_GRID_IMPORT_SENSOR)
        export_sensor = self.config.get(CONF_GRID_EXPORT_SENSOR)
        import_kw = read_power_kw(self.hass, import_sensor, default_unit="W")
        export_kw = read_power_kw(self.hass, export_sensor, default_unit="W")
        if import_kw is None and export_kw is None:
            return None
        return ((import_kw or 0.0) - (export_kw or 0.0)) * 1000.0

    async def _handle_realtime_update(self, now: datetime) -> None:
        """Adjust the planned offset around live grid power."""
        if self._realtime_controller is None or not self.data:
            return
        current_grid_w = self._get_realtime_grid_w()
        if current_grid_w is None:
            return
        offsets = self.data.get("offsets") or [0]
        action = self._realtime_controller.get_control_action(
            current_grid_w=current_grid_w,
            shadow_price_eur_per_kwh=self.data.get("shadow_price_eur_per_kwh", 0.0),
            planned_offset=offsets[0],
            max_adjustment=max_offset_change(
                self.data.get("step_minutes", 60), self._offset_delta_t()
            ),
            offset_min=DEFAULT_OFFSET_MIN,
            offset_max=DEFAULT_OFFSET_MAX,
        )
        self.async_set_updated_data({**self.data, "realtime": action})

    # ------------------------------------------------------------------
    # Optimization cycle
    # ------------------------------------------------------------------

    def _offset_delta_t(self) -> int:
        return int(self.config.get(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T))

    def _curve(self) -> tuple[float, float, float, float]:
        """(min_supply, max_supply, min_outdoor, max_outdoor) of the heating curve."""
        return (
            float(self.config.get(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN)),
            float(self.config.get(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX)),
            float(
                self.config.get(
                    CONF_HEAT_CURVE_MIN_OUTDOOR, DEFAULT_HEAT_CURVE_MIN_OUTDOOR
                )
            ),
            float(
                self.config.get(
                    CONF_HEAT_CURVE_MAX_OUTDOOR, DEFAULT_HEAT_CURVE_MAX_OUTDOOR
                )
            ),
        )

    def _read_prices(
        self,
    ) -> tuple[list[float], list[datetime], int]:
        """Consumption price forecast with UTC period start times."""
        if not self._price_sensor:
            raise _update_failed("no_price_sensor")
        issue_id = f"price_sensor_unavailable_{self.zone_id}"
        state = self.hass.states.get(self._price_sensor)
        if not state or state.state in ("unknown", "unavailable"):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="price_sensor_unavailable",
                translation_placeholders={"sensor": self._price_sensor},
            )
            raise _update_failed(
                "price_sensor_unavailable",
                translation_placeholders={"sensor": self._price_sensor},
            )
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)

        prices, starts, interval = extract_price_forecast_with_timestamps(state)
        if not prices:
            raise _update_failed(
                "price_data_extraction_failed",
                translation_placeholders={"sensor": self._price_sensor},
            )
        return prices, starts, interval

    def _read_feed_in_prices(self) -> tuple[list[float], list[datetime], int] | None:
        sensor = self.config.get(CONF_PRODUCTION_PRICE_SENSOR)
        if not sensor:
            return None
        state = self.hass.states.get(sensor)
        if not state or state.state in ("unknown", "unavailable"):
            return None
        prices, starts, interval = extract_price_forecast_with_timestamps(state)
        return (prices, starts, interval) if prices else None

    def _build_steps(
        self, starts: list[datetime], interval: int, now: datetime
    ) -> tuple[list[datetime], list[float]]:
        """DP step grid: one step per price period, the first starting now.

        The horizon covers the planning window, rounded up to whole periods.

        When the price sensor only reports the current price (a single
        period), the grid is extended with further periods of the same
        length so the horizon still spans the planning window.
        """
        horizon_end = now + timedelta(
            hours=int(self.config.get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW))
        )
        period = timedelta(minutes=interval)
        periods = list(starts)
        while periods[-1] + period < horizon_end:
            periods.append(periods[-1] + period)

        step_starts: list[datetime] = []
        durations: list[float] = []
        for start in periods:
            end = start + period
            step_start = max(start, now)
            if end <= step_start or step_start >= horizon_end:
                continue
            step_starts.append(step_start)
            durations.append((end - step_start).total_seconds() / 3600)
        return step_starts, durations

    async def _async_update_data(self) -> dict[str, Any]:
        """Run one optimization cycle."""
        heat_data = self.heat_coordinator.data
        if not heat_data:
            raise _update_failed("no_heat_data")
        weather_data = self.heat_coordinator.weather_coordinator.data
        if not weather_data:
            raise _update_failed("no_weather_data_for_optimization")

        prices, price_starts, interval = self._read_prices()
        now = dt_util.utcnow()
        step_starts, durations = self._build_steps(price_starts, interval, now)
        if not step_starts:
            raise _update_failed(
                "price_data_extraction_failed",
                translation_placeholders={"sensor": str(self._price_sensor)},
            )

        def align(values: list[float], start: datetime, minutes: float) -> list[float]:
            return resample_to_steps(values, start, minutes, step_starts, durations)

        # Prices: extend the last known price over any synthesized periods.
        price_series = align(prices, price_starts[0], interval)
        price_series += [prices[-1]] * (len(step_starts) - len(price_series))

        feed_in: list[float] | None = None
        feed_in_raw = self._read_feed_in_prices()
        if feed_in_raw is not None:
            fi_prices, fi_starts, fi_interval = feed_in_raw
            feed_in = align(fi_prices, fi_starts[0], fi_interval)

        forecast_start: datetime = weather_data["forecast_start_utc"]
        outdoor = align(weather_data["temperature_forecast"], forecast_start, 60)
        humidity = align(
            weather_data.get("humidity_forecast") or [], forecast_start, 60
        )
        solar = align(heat_data.get("solar_gain_forecast") or [], forecast_start, 60)
        pv = align(heat_data.get("pv_production_forecast") or [], forecast_start, 60)
        if not outdoor:
            raise _update_failed("no_weather_data_for_optimization")

        config = self.heat_coordinator.effective_config()
        indoor_temp = float(heat_data["indoor_temperature"])
        result = await self.hass.async_add_executor_job(
            self._optimize,
            config,
            indoor_temp,
            outdoor,
            price_series,
            solar,
            pv,
            feed_in,
            humidity,
            durations,
            interval,
        )
        opt: ThermalOptimizationResult = result["result"]
        building: BuildingConfig = result["building"]

        self._current_offset = opt.offsets[0] if opt.offsets else 0
        if self._realtime_controller is not None:
            self._realtime_controller.reset()

        power_kw = read_power_kw(self.hass, self.config.get(CONF_POWER_CONSUMPTION))
        await self._maybe_record_calibration_sample(
            building=building,
            indoor_temp=indoor_temp,
            indoor_is_measured=heat_data.get("indoor_temperature_source")
            == INDOOR_SOURCE_SENSOR,
            outdoor_temp=float(weather_data["current_temperature"]),
            solar_gain_kw=float(heat_data.get("solar_gain", 0.0)),
            supply_temp=self._measured_supply_temp()
            or (opt.supply_temps[0] if opt.supply_temps else None),
            power_kw=power_kw,
            now=now,
        )

        mass = building.thermal_mass_kwh_per_k

        def stored_kwh(temp: float) -> float:
            return round((temp - building.comfort_min) * mass, 3)

        calibration = self._calibration
        return {
            "offset": self._current_offset,
            "offsets": opt.offsets,
            "supply_temps": opt.supply_temps,
            "indoor_temps": opt.indoor_temps,
            "thermal_power_kw": opt.thermal_power_kw,
            "electrical_power_kw": opt.electrical_power_kw,
            "cop": opt.cop,
            "cost_eur": opt.cost_eur,
            "total_cost_eur": opt.total_cost_eur,
            "baseline_supply_temps": opt.baseline_supply_temps,
            "baseline_indoor_temps": opt.baseline_indoor_temps,
            "baseline_cop": opt.baseline_cop,
            "baseline_cost_eur": opt.baseline_cost_eur,
            "baseline_total_cost_eur": opt.baseline_total_cost_eur,
            "cost_savings_eur": opt.cost_savings_eur,
            "shadow_price_eur_per_kwh": opt.shadow_price_eur_per_kwh,
            "prices": [round(p, 5) for p in price_series],
            "outdoor_forecast": [round(t, 1) for t in outdoor],
            "step_start_times": [s.isoformat() for s in step_starts],
            "step_durations_hours": [round(d, 4) for d in durations],
            "step_minutes": interval,
            "initial_indoor_temp": indoor_temp,
            "comfort_min": building.comfort_min,
            "comfort_max": building.comfort_max,
            "buffer_kwh": stored_kwh(indoor_temp),
            "buffer_forecast_kwh": [stored_kwh(t) for t in opt.indoor_temps],
            "building_ua_w_per_k": round(building.ua_w_per_k, 1),
            "building_thermal_mass_kwh_per_k": round(mass, 2),
            "building_time_constant_hours": round(building.time_constant_hours, 1),
            "internal_gain_kw": round(building.internal_gain_kw, 3),
            "emitter_nominal_power_kw": result["emitter_nominal_power_kw"],
            "heatpump_max_thermal_power_kw": result["heatpump_max_thermal_power_kw"],
            "calibration_applied": calibration.applied if calibration else False,
            "calibration_sample_count": calibration.sample_count if calibration else 0,
            "calibration_last_result": calibration.last_result if calibration else None,
            "learned_ua_w_per_k": calibration.learned_ua_w_per_k
            if calibration
            else None,
            "learned_thermal_mass_kwh_per_k": (
                calibration.learned_thermal_mass_kwh_per_k if calibration else None
            ),
            "heat_pump_power_kw": power_kw,
            "heat_pump_actively_running": (
                power_kw > IDLE_POWER_THRESHOLD_KW if power_kw is not None else None
            ),
            "timestamp": now,
        }

    def _measured_supply_temp(self) -> float | None:
        """Measured supply temperature, if a sensor is configured and valid."""
        return get_sensor_value(
            self.hass, self.config.get(CONF_SUPPLY_TEMPERATURE_SENSOR), None
        )

    def building_config(self, config: dict[str, Any]) -> BuildingConfig:
        """Label-based building model, overridden by applied calibration."""
        building = BuildingConfig.from_config(config)
        calibration = self._calibration
        if (
            calibration is not None
            and calibration.applied
            and calibration.learned_ua_w_per_k is not None
            and calibration.learned_thermal_mass_kwh_per_k is not None
        ):
            building.ua_w_per_k = calibration.learned_ua_w_per_k
            building.thermal_mass_kwh_per_k = calibration.learned_thermal_mass_kwh_per_k
            building.time_constant_hours = building.thermal_mass_kwh_per_k / (
                building.ua_w_per_k / 1000.0
            )
        return building

    def _optimize(
        self,
        config: dict[str, Any],
        indoor_temp: float,
        outdoor: list[float],
        prices: list[float],
        solar: list[float],
        pv: list[float],
        feed_in: list[float] | None,
        humidity: list[float],
        durations: list[float],
        step_minutes: int,
    ) -> dict[str, Any]:
        """Build the models and run the DP (blocking, executor)."""
        building = self.building_config(config)
        if building.area_m2 <= 0:
            raise _update_failed("missing_building_config")
        min_supply, max_supply, min_outdoor, max_outdoor = self._curve()
        emitter = EmitterConfig.sized_to_building(
            building,
            design_outdoor_temp=min_outdoor,
            design_supply_temp=max_supply,
            emitter_type=str(config.get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE)),
        )
        heatpump = HeatPumpConfig.from_config(
            config,
            max_thermal_power_kw=emitter.nominal_power_kw * DEFAULT_HEATPUMP_HEADROOM,
        )
        horizon = min(len(outdoor), len(prices))
        result = optimize_thermal_schedule(
            building=building,
            heatpump=heatpump,
            emitter=emitter,
            outdoor_temps=outdoor[:horizon],
            prices=prices[:horizon],
            initial_indoor_temp=indoor_temp,
            solar_gain_kw=solar[:horizon] or None,
            pv_surplus_kw=pv[:horizon] or None,
            feed_in_prices=feed_in[:horizon] if feed_in else None,
            humidity_forecast=humidity[:horizon] or None,
            step_durations_hours=durations[:horizon],
            time_base=step_minutes,
            offset_delta_t=self._offset_delta_t(),
            water_min=min_supply,
            water_max=max_supply,
            outdoor_min=min_outdoor,
            outdoor_max=max_outdoor,
            current_offset=self._current_offset,
        )
        return {
            "result": result,
            "building": building,
            "emitter_nominal_power_kw": round(emitter.nominal_power_kw, 2),
            "heatpump_max_thermal_power_kw": round(heatpump.max_thermal_power_kw, 2),
        }

    # ------------------------------------------------------------------
    # Thermal calibration
    # ------------------------------------------------------------------

    async def _maybe_record_calibration_sample(
        self,
        *,
        building: BuildingConfig,
        indoor_temp: float,
        indoor_is_measured: bool,
        outdoor_temp: float,
        solar_gain_kw: float,
        supply_temp: float | None,
        power_kw: float | None,
        now: datetime,
    ) -> None:
        """Accumulate one observation window and feed it to the calibration fit.

        A window starts at a snapshot and keeps integrating delivered heat
        (electricity meter x COP - independent of the UA/thermal-mass
        estimate being corrected) and the indoor-outdoor temperature
        difference until the indoor temperature has moved far enough to be
        well above sensor quantization. It is then recorded as one sample
        with the window's mean heat input and mean temperature difference.
        """
        calibration = self._calibration
        if calibration is None:
            return
        if not indoor_is_measured:
            calibration.last_result = RESULT_NO_INDOOR_SENSOR
            self._calibration_snapshot = None
            return
        if building.area_m2 <= 0:
            calibration.last_result = RESULT_MISSING_BUILDING_CONFIG
            return
        if power_kw is None or supply_temp is None:
            calibration.last_result = RESULT_NO_POWER_READING
            self._calibration_snapshot = None
            return

        heatpump = HeatPumpConfig.from_config(self.config)
        heat_kw = power_kw * heatpump.cop_at(
            supply_temp=supply_temp, outdoor_temp=outdoor_temp
        )
        gains_kw = heat_kw + max(0.0, solar_gain_kw) + building.internal_gain_kw
        delta_t = indoor_temp - outdoor_temp

        snapshot = self._calibration_snapshot
        if snapshot is None:
            self._calibration_snapshot = {
                "start": now,
                "last": now,
                "start_indoor": indoor_temp,
                "gains_kwh": 0.0,
                "delta_t_kh": 0.0,
                "last_gains_kw": gains_kw,
                "last_delta_t": delta_t,
            }
            return

        step_h = (now - snapshot["last"]).total_seconds() / 3600.0
        if step_h <= 0 or step_h > 1.0:
            # A gap (restart, outage) breaks the energy integral.
            calibration.last_result = RESULT_ELAPSED_GAP
            self._calibration_snapshot = None
            return
        snapshot["gains_kwh"] += snapshot["last_gains_kw"] * step_h
        snapshot["delta_t_kh"] += snapshot["last_delta_t"] * step_h
        snapshot["last"] = now
        snapshot["last_gains_kw"] = gains_kw
        snapshot["last_delta_t"] = delta_t

        elapsed_h = (now - snapshot["start"]).total_seconds() / 3600.0
        moved = indoor_temp - snapshot["start_indoor"]
        if abs(moved) < MIN_INDOOR_TEMP_DELTA_C or elapsed_h < CALIBRATION_MIN_HOURS:
            if elapsed_h > CALIBRATION_MAX_HOURS:
                calibration.last_result = RESULT_ELAPSED_GAP
                self._calibration_snapshot = None
            return

        prior = BuildingConfig.from_config(self.heat_coordinator.effective_config())
        calibration.record_sample(
            delta_t=snapshot["delta_t_kh"] / elapsed_h,
            heat_and_solar_kw=snapshot["gains_kwh"] / elapsed_h,
            rate_c_per_h=moved / elapsed_h,
            prior_ua_w_per_k=prior.ua_w_per_k,
            prior_thermal_mass_kwh_per_k=prior.thermal_mass_kwh_per_k,
        )
        await calibration.async_save()
        self._calibration_snapshot = None
