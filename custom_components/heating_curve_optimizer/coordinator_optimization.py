"""Optimization coordinator for the Heating Curve Optimizer integration."""

from __future__ import annotations

import logging
from collections import deque
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
    MIN_SAMPLES_TO_APPLY,
    MODE_APPLY,
    MODE_OBSERVE,
    MODE_OFF,
    RESULT_ELAPSED_GAP,
    RESULT_EXCLUDED_DHW,
    RESULT_EXCLUDED_JUMP,
    RESULT_EXCLUDED_WINDOW,
    RESULT_MISSING_BUILDING_CONFIG,
    RESULT_MULTI_ZONE,
    RESULT_NO_INDOOR_SENSOR,
    RESULT_NO_POWER_READING,
    STATUS_APPLIED,
    STATUS_COLLECTING,
    STATUS_OFF,
    STATUS_PROVISIONAL,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    CopSample,
    EmitterSample,
    Prior,
    Sample,
    ThermalCalibrationState,
    effective_energy_label,
    residual_diagnosis,
)
from .calibration import (
    STORAGE_VERSION as CALIBRATION_STORAGE_VERSION,
)
from .const import (
    CONF_CALIBRATION_MODE,
    CONF_CEILING_HEIGHT,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_DHW_ACTIVE_SENSOR,
    CONF_EMITTER_TYPE,
    CONF_ENERGY_LABEL,
    CONF_GAS_BOILER_EFFICIENCY,
    CONF_GAS_CALORIFIC_VALUE,
    CONF_GAS_METER_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_HEAT_PUMP_THERMAL_POWER_SENSOR,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_OFFSET_DELTA_T,
    CONF_PLANNING_WINDOW,
    CONF_POWER_CONSUMPTION,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_VENTILATION_TYPE,
    CONF_WINDOW_SENSORS,
    DEFAULT_CALIBRATION_MODE,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_EMITTER_TYPE,
    DEFAULT_GAS_BOILER_EFFICIENCY,
    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_HEAT_CURVE_MAX_OUTDOOR,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_HEAT_CURVE_MIN_OUTDOOR,
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_REALTIME_INTERVAL_S,
    DEFAULT_VENTILATION_TYPE,
    DOMAIN,
)
from .coordinator_heat import INDOOR_SOURCE_SENSOR, HeatCalculationCoordinator
from .coordinator_weather import _update_failed
from .heatpump_model import HeatPumpConfig
from .helpers import (
    calculate_defrost_factor,
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

# A jump of more than this between two runs (sensor glitch, sensor moved,
# window opened without a contact) invalidates the observation window.
CALIBRATION_MAX_JUMP_C = 1.0

# Model accuracy: the plan's indoor temperature this far ahead is compared
# with the measurement once that moment has passed.
ACCURACY_HORIZON = timedelta(hours=1)
ACCURACY_MATCH_TOLERANCE = timedelta(minutes=30)
ACCURACY_WINDOW = timedelta(hours=24)

# Below this, the heat pump is not in a steady heating state for COP and
# emitter samples.
MIN_RUNNING_POWER_KW = 0.3

# Minimum observation window when gas-meter heat is part of the sample.
GAS_MIN_WINDOW_HOURS = 2.0

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
        *,
        subentry_id: str | None = None,
        calibration_allowed: bool = True,
    ) -> None:
        """Initialize the optimization coordinator.

        ``calibration_allowed`` is False when several zones share one heat
        pump: its power cannot be attributed to a single zone.
        """
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
        self._calibration_allowed = calibration_allowed
        self._calibration_taint: str | None = None
        self.subentry_id = subentry_id
        self._unsub_calibration: Any = None
        self._pending_predictions: list[tuple[datetime, float, float]] = []
        self._accuracy: deque[tuple[datetime, float, float]] = deque(maxlen=500)
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
        prior_emitter = self._prior_emitter(self.heat_coordinator.effective_config())
        self._calibration.refit(
            self.calibration_prior(),
            cop_prior=self.cop_prior(),
            emitter_prior=(
                prior_emitter.nominal_power_kw,
                prior_emitter.exponent,
                prior_emitter.nominal_delta_t,
            ),
        )

        watched = self._exclusion_sensors()
        if watched:
            self._unsub_calibration = async_track_state_change_event(
                self.hass, list(watched), self._handle_exclusion_sensor
            )

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
        for unsub in (self._unsub, self._unsub_realtime, self._unsub_calibration):
            if unsub:
                unsub()
        self._unsub = None
        self._unsub_realtime = None
        self._unsub_calibration = None
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

    def _exclusion_sensors(self) -> dict[str, str]:
        """Binary sensors whose "on" state invalidates a calibration window."""
        sensors: dict[str, str] = {}
        if dhw := self.config.get(CONF_DHW_ACTIVE_SENSOR):
            sensors[dhw] = RESULT_EXCLUDED_DHW
        for window in self.config.get(CONF_WINDOW_SENSORS) or []:
            sensors[window] = RESULT_EXCLUDED_WINDOW
        return sensors

    async def _handle_exclusion_sensor(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Remember that tap water or an open window disturbed this window."""
        new_state = event.data.get("new_state")
        if new_state is not None and new_state.state == "on":
            self._calibration_taint = self._exclusion_sensors().get(
                event.data["entity_id"], RESULT_EXCLUDED_WINDOW
            )

    def _active_exclusion(self) -> str | None:
        """An exclusion reason that is active right now, if any."""
        for entity_id, reason in self._exclusion_sensors().items():
            state = self.hass.states.get(entity_id)
            if state is not None and state.state == "on":
                return reason
        return None

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
        indoor_is_measured = (
            heat_data.get("indoor_temperature_source") == INDOOR_SOURCE_SENSOR
        )
        self._track_accuracy(
            now=now,
            indoor_temp=indoor_temp,
            indoor_is_measured=indoor_is_measured,
            opt=opt,
            durations=durations,
            outdoor=outdoor,
            solar=solar,
        )
        self._record_operating_point(
            indoor_temp=indoor_temp,
            indoor_is_measured=indoor_is_measured,
            outdoor_temp=float(weather_data["current_temperature"]),
            humidity=float((weather_data.get("humidity_forecast") or [80.0])[0]),
            power_kw=power_kw,
            planned_supply=opt.supply_temps[0] if opt.supply_temps else None,
        )
        await self._maybe_record_calibration_sample(
            indoor_temp=indoor_temp,
            indoor_is_measured=indoor_is_measured,
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

        self._update_calibration_issue()
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
            "solar_factor": round(building.solar_factor, 2),
            "calibration": self.calibration_summary(config),
            "model_accuracy": self.accuracy_summary(now),
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
        """Label-based building model, replaced by the calibration when applied."""
        building = BuildingConfig.from_config(config)
        calibration = self._calibration
        if (
            self.calibration_mode() == MODE_APPLY
            and self._calibration_allowed
            and calibration is not None
            and calibration.ready
            and calibration.fit is not None
        ):
            fit = calibration.fit
            building.ua_w_per_k = fit.ua_w_per_k
            building.thermal_mass_kwh_per_k = fit.thermal_mass_kwh_per_k
            building.internal_gain_kw = fit.internal_gain_kw
            building.solar_factor = fit.solar_factor
            building.time_constant_hours = fit.time_constant_hours
        return building

    def _calibration_applied(self) -> bool:
        return (
            self.calibration_mode() == MODE_APPLY
            and self._calibration_allowed
            and self._calibration is not None
        )

    def _prior_emitter(self, config: dict[str, Any]) -> EmitterConfig:
        """Emitter sized to the label-based building (the calibration prior)."""
        _min_supply, max_supply, min_outdoor, _max_outdoor = self._curve()
        return EmitterConfig.sized_to_building(
            BuildingConfig.from_config(config),
            design_outdoor_temp=min_outdoor,
            design_supply_temp=max_supply,
            emitter_type=str(config.get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE)),
        )

    def emitter_config(
        self, config: dict[str, Any], building: BuildingConfig
    ) -> EmitterConfig:
        """Emitter curve: learned when applied and usable, else sized to the building."""
        calibration = self._calibration
        if (
            self._calibration_applied()
            and calibration is not None
            and calibration.emitter_fit is not None
            and calibration.emitter_fit.usable
        ):
            fit = calibration.emitter_fit
            scale = self._modelled_heat_scale()
            return EmitterConfig(
                exponent=fit.exponent,
                nominal_delta_t=fit.nominal_delta_t,
                nominal_power_kw=fit.nominal_power_kw * scale,
            )
        _min_supply, max_supply, min_outdoor, _max_outdoor = self._curve()
        return EmitterConfig.sized_to_building(
            building,
            design_outdoor_temp=min_outdoor,
            design_supply_temp=max_supply,
            emitter_type=str(config.get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE)),
        )

    def _modelled_heat_scale(self) -> float:
        """Factor from modelled (P x COP) heat to real heat, when known.

        1 when heat is measured by a thermal sensor. Otherwise the COP scale
        the building fit learned from gas-meter periods (1 without them).
        """
        calibration = self._calibration
        if self.config.get(CONF_HEAT_PUMP_THERMAL_POWER_SENSOR):
            return 1.0
        if (
            self._calibration_applied()
            and calibration is not None
            and calibration.ready
            and calibration.fit is not None
        ):
            return calibration.fit.cop_scale
        return 1.0

    def cop_prior(self) -> tuple[float, float, float]:
        """Configured COP curve with the compensation factor folded in."""
        hp = HeatPumpConfig.from_config(self.config)
        comp = hp.cop_compensation_factor
        return (
            hp.base_cop_at_35 * comp,
            hp.outdoor_temp_coefficient * comp,
            hp.k_factor * comp,
        )

    def heatpump_config(
        self, config: dict[str, Any], emitter: EmitterConfig
    ) -> HeatPumpConfig:
        """COP curve: measured fit, else configured x learned scale."""
        heatpump = HeatPumpConfig.from_config(
            config,
            max_thermal_power_kw=emitter.nominal_power_kw * DEFAULT_HEATPUMP_HEADROOM,
        )
        calibration = self._calibration
        if not self._calibration_applied() or calibration is None:
            return heatpump
        if calibration.cop_fit is not None and calibration.cop_fit.usable:
            heatpump.base_cop_at_35 = calibration.cop_fit.base_cop
            heatpump.outdoor_temp_coefficient = calibration.cop_fit.outdoor_coefficient
            heatpump.k_factor = calibration.cop_fit.k_factor
            heatpump.cop_compensation_factor = 1.0
        else:
            heatpump.cop_compensation_factor *= self._modelled_heat_scale()
        return heatpump

    def _gas_heat_counter_kwh(self) -> float | None:
        """Cumulative heat delivered by the gas boiler, from its gas meter."""
        sensor = self.config.get(CONF_GAS_METER_SENSOR)
        state = self.hass.states.get(sensor) if sensor else None
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        efficiency = float(
            self.config.get(CONF_GAS_BOILER_EFFICIENCY, DEFAULT_GAS_BOILER_EFFICIENCY)
        )
        unit = str(state.attributes.get("unit_of_measurement", "m³"))
        if unit == "kWh":
            return value * efficiency
        calorific = float(
            self.config.get(
                CONF_GAS_CALORIFIC_VALUE, DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3
            )
        )
        return value * calorific * efficiency

    def _record_operating_point(
        self,
        *,
        indoor_temp: float,
        indoor_is_measured: bool,
        outdoor_temp: float,
        humidity: float,
        power_kw: float | None,
        planned_supply: float | None,
    ) -> None:
        """Phase 2/3 samples from the current steady operating point."""
        calibration = self._calibration
        if (
            calibration is None
            or self.calibration_mode() == MODE_OFF
            or not self._calibration_allowed
            or self._active_exclusion() is not None
        ):
            return
        measured_heat = read_power_kw(
            self.hass, self.config.get(CONF_HEAT_PUMP_THERMAL_POWER_SENSOR)
        )
        supply = self._measured_supply_temp()
        if (
            measured_heat is not None
            and power_kw is not None
            and power_kw >= MIN_RUNNING_POWER_KW
            and measured_heat > 0
        ):
            operating_supply = supply if supply is not None else planned_supply
            if operating_supply is not None:
                calibration.record_cop_sample(
                    CopSample(
                        outdoor_temp=outdoor_temp,
                        supply_temp=operating_supply,
                        defrost_factor=calculate_defrost_factor(outdoor_temp, humidity),
                        cop=measured_heat / power_kw,
                    ),
                    self.cop_prior(),
                )
        if supply is None or not indoor_is_measured:
            return
        heat = measured_heat
        if heat is None and power_kw is not None:
            heat = power_kw * HeatPumpConfig.from_config(self.config).cop_at(
                supply_temp=supply, outdoor_temp=outdoor_temp, humidity=humidity
            )
        if heat is None or heat < MIN_RUNNING_POWER_KW:
            return
        prior = self._prior_emitter(self.heat_coordinator.effective_config())
        calibration.record_emitter_sample(
            EmitterSample(delta_t=supply - indoor_temp, heat_kw=heat),
            prior_nominal_kw=prior.nominal_power_kw,
            prior_exponent=prior.exponent,
            nominal_delta_t=prior.nominal_delta_t,
        )

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
        emitter = self.emitter_config(config, building)
        heatpump = self.heatpump_config(config, emitter)
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

    def calibration_mode(self) -> str:
        """The zone's calibration mode (off / observe / apply)."""
        return str(self.config.get(CONF_CALIBRATION_MODE, DEFAULT_CALIBRATION_MODE))

    def calibration_prior(self) -> Prior:
        """Label-based model values the fit is regularised towards."""
        building = BuildingConfig.from_config(self.heat_coordinator.effective_config())
        return Prior(
            ua_w_per_k=building.ua_w_per_k,
            thermal_mass_kwh_per_k=building.thermal_mass_kwh_per_k,
            internal_gain_kw=building.internal_gain_kw,
            area_m2=building.area_m2,
        )

    def _calibration_inputs_available(self) -> bool:
        return bool(self.config.get(CONF_INDOOR_TEMPERATURE_SENSOR)) and bool(
            self.config.get(CONF_POWER_CONSUMPTION)
            or self.config.get(CONF_HEAT_PUMP_THERMAL_POWER_SENSOR)
        )

    def calibration_status(self) -> str:
        """User-facing calibration status."""
        calibration = self._calibration
        if self.calibration_mode() == MODE_OFF:
            return STATUS_OFF
        if (
            calibration is None
            or not self._calibration_allowed
            or not self._calibration_inputs_available()
        ):
            return STATUS_UNAVAILABLE
        if calibration.sample_count < MIN_SAMPLES_TO_APPLY:
            return STATUS_COLLECTING
        if not calibration.ready:
            return STATUS_PROVISIONAL
        return STATUS_APPLIED if self.calibration_mode() == MODE_APPLY else STATUS_READY

    def calibration_summary(self, config: dict[str, Any]) -> dict[str, Any]:
        """Calibration results in terms a user can relate to."""
        calibration = self._calibration
        prior = self.calibration_prior()
        fit = calibration.fit if calibration else None
        summary: dict[str, Any] = {
            "status": self.calibration_status(),
            "mode": self.calibration_mode(),
            "sample_count": calibration.sample_count if calibration else 0,
            "samples_needed": MIN_SAMPLES_TO_APPLY,
            "progress_pct": min(
                100,
                round(
                    100
                    * (calibration.sample_count if calibration else 0)
                    / MIN_SAMPLES_TO_APPLY
                ),
            ),
            "last_result": calibration.last_result if calibration else None,
            "excluded_windows": dict(calibration.exclusions) if calibration else {},
            "heat_source": (
                "measured"
                if self.config.get(CONF_HEAT_PUMP_THERMAL_POWER_SENSOR)
                else "cop_model"
            ),
            "configured_energy_label": config.get(CONF_ENERGY_LABEL),
            "label_ua_w_per_k": round(prior.ua_w_per_k, 1),
            "label_thermal_mass_kwh_per_k": round(prior.thermal_mass_kwh_per_k, 2),
        }
        if not self._calibration_allowed:
            summary["last_result"] = RESULT_MULTI_ZONE
        summary |= self._cop_and_emitter_summary(config)
        if fit is None:
            return summary
        outdoor = 5.0
        target = float(config.get("target_indoor_temp", 20.0))
        cooling_rate = (
            fit.ua_w_per_k / 1000.0 * (target - outdoor) - fit.internal_gain_kw
        ) / fit.thermal_mass_kwh_per_k
        summary |= {
            "r_squared": round(fit.r_squared, 3),
            "heating_share": round(fit.heating_share, 2),
            "plausible": fit.plausible,
            "learned_ua_w_per_k": round(fit.ua_w_per_k, 1),
            "learned_thermal_mass_kwh_per_k": round(fit.thermal_mass_kwh_per_k, 2),
            "learned_solar_factor": round(fit.solar_factor, 2),
            "learned_internal_gain_w": round(fit.internal_gain_kw * 1000.0),
            "heat_loss_vs_label_pct": round(
                100.0 * (fit.ua_w_per_k / prior.ua_w_per_k - 1.0)
            ),
            "effective_energy_label": effective_energy_label(
                fit.ua_w_per_k,
                prior.area_m2,
                str(config.get(CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE)),
                float(config.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)),
            ),
            "time_constant_hours": round(fit.time_constant_hours, 1),
            "cooling_rate_at_5c_k_per_h": round(cooling_rate, 2),
            "gas_windows": sum(1 for s in calibration.samples if s.gas_kw > 0.1)
            if calibration
            else 0,
            "learned_cop_scale": round(fit.cop_scale, 3),
        }
        return summary

    def _cop_and_emitter_summary(self, config: dict[str, Any]) -> dict[str, Any]:
        """Phase 2/3 results next to the configured assumptions."""
        calibration = self._calibration
        result: dict[str, Any] = {}
        base = self.cop_prior()[0]
        result["configured_cop_at_a0_w35"] = round(base, 2)
        cop_fit = calibration.cop_fit if calibration else None
        if cop_fit is not None:
            result |= {
                "cop_samples": cop_fit.sample_count,
                "cop_usable": cop_fit.usable,
                "learned_cop_at_a0_w35": round(cop_fit.base_cop, 2),
                "learned_k_factor": round(cop_fit.k_factor, 3),
                "learned_outdoor_coefficient": round(cop_fit.outdoor_coefficient, 3),
                "cop_fit_mean_abs_error": round(cop_fit.mean_abs_error, 2),
                "cop_vs_configured_pct": round(100 * (cop_fit.base_cop / base - 1)),
            }
        prior_emitter = self._prior_emitter(config)
        result["assumed_emitter_power_kw"] = round(prior_emitter.nominal_power_kw, 1)
        emitter_fit = calibration.emitter_fit if calibration else None
        if emitter_fit is not None:
            result |= {
                "emitter_samples": emitter_fit.sample_count,
                "emitter_usable": emitter_fit.usable,
                "learned_emitter_power_kw": round(emitter_fit.nominal_power_kw, 1),
                "learned_emitter_exponent": round(emitter_fit.exponent, 2),
                "emitter_fit_r_squared": round(emitter_fit.r_squared, 3),
            }
        return result

    def _update_calibration_issue(self) -> None:
        """Offer to start using a calibration that is ready (observe mode)."""
        issue_id = f"calibration_ready_{self.zone_id}"
        calibration = self._calibration
        if (
            self.calibration_mode() == MODE_OBSERVE
            and calibration is not None
            and calibration.ready
            and calibration.fit is not None
            and self.config_entry is not None
            and self.subentry_id is not None
        ):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="calibration_ready",
                translation_placeholders={
                    "zone": str(self.config.get("name", "")),
                    "ua": f"{calibration.fit.ua_w_per_k:.0f}",
                    "tau": f"{calibration.fit.time_constant_hours:.0f}",
                    "samples": str(calibration.sample_count),
                },
                data={
                    "entry_id": self.config_entry.entry_id,
                    "subentry_id": self.subentry_id,
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _maybe_record_calibration_sample(
        self,
        *,
        indoor_temp: float,
        indoor_is_measured: bool,
        outdoor_temp: float,
        solar_gain_kw: float,
        supply_temp: float | None,
        power_kw: float | None,
        now: datetime,
    ) -> None:
        """Accumulate one observation window and feed it to the fit.

        A window integrates delivered heat (thermal sensor, or electricity
        meter x COP - both independent of the building parameters being
        estimated), modelled solar gain and the indoor-outdoor difference,
        until the indoor temperature has moved far enough to rise above
        sensor quantization. Windows disturbed by tap water, an open window
        or an implausible temperature jump are discarded.
        """
        calibration = self._calibration
        if calibration is None or self.calibration_mode() == MODE_OFF:
            return
        if not self._calibration_allowed:
            calibration.last_result = RESULT_MULTI_ZONE
            return
        if not indoor_is_measured:
            calibration.last_result = RESULT_NO_INDOOR_SENSOR
            self._calibration_snapshot = None
            return
        prior = self.calibration_prior()
        if prior.area_m2 <= 0:
            calibration.last_result = RESULT_MISSING_BUILDING_CONFIG
            return

        heat_kw = read_power_kw(
            self.hass, self.config.get(CONF_HEAT_PUMP_THERMAL_POWER_SENSOR)
        )
        if heat_kw is None and power_kw is not None and supply_temp is not None:
            heat_kw = power_kw * HeatPumpConfig.from_config(self.config).cop_at(
                supply_temp=supply_temp, outdoor_temp=outdoor_temp
            )
        if heat_kw is None:
            calibration.last_result = RESULT_NO_POWER_READING
            self._calibration_snapshot = None
            return

        exclusion = self._active_exclusion() or self._calibration_taint
        self._calibration_taint = None
        snapshot = self._calibration_snapshot
        if (
            snapshot is not None
            and abs(indoor_temp - snapshot["last_indoor"]) > CALIBRATION_MAX_JUMP_C
        ):
            exclusion = RESULT_EXCLUDED_JUMP
        if exclusion is not None:
            calibration.record_exclusion(exclusion)
            self._calibration_snapshot = None
            await calibration.async_save()
            return

        current = {
            "heat_kw": max(0.0, heat_kw),
            "solar_kw": max(0.0, solar_gain_kw),
            "delta_t": indoor_temp - outdoor_temp,
        }
        if snapshot is None:
            self._calibration_snapshot = {
                "gas_start_kwh": self._gas_heat_counter_kwh(),
                "start": now,
                "last": now,
                "start_indoor": indoor_temp,
                "last_indoor": indoor_temp,
                "integrals": dict.fromkeys(current, 0.0),
                "last_values": current,
            }
            return

        step_h = (now - snapshot["last"]).total_seconds() / 3600.0
        if step_h <= 0 or step_h > 1.0:
            calibration.record_exclusion(RESULT_ELAPSED_GAP)
            self._calibration_snapshot = None
            return
        for key, value in snapshot["last_values"].items():
            snapshot["integrals"][key] += value * step_h
        snapshot["last"] = now
        snapshot["last_indoor"] = indoor_temp
        snapshot["last_values"] = current

        elapsed_h = (now - snapshot["start"]).total_seconds() / 3600.0
        moved = indoor_temp - snapshot["start_indoor"]
        # A gas meter reports in coarse steps; a longer window keeps one
        # step from dominating the window's gas heat.
        min_hours = (
            GAS_MIN_WINDOW_HOURS
            if self.config.get(CONF_GAS_METER_SENSOR)
            else CALIBRATION_MIN_HOURS
        )
        if abs(moved) < MIN_INDOOR_TEMP_DELTA_C or elapsed_h < min_hours:
            if elapsed_h > CALIBRATION_MAX_HOURS:
                calibration.record_exclusion(RESULT_ELAPSED_GAP)
                self._calibration_snapshot = None
            return

        gas_kw = 0.0
        if self.config.get(CONF_GAS_METER_SENSOR):
            gas_end = self._gas_heat_counter_kwh()
            gas_start = snapshot["gas_start_kwh"]
            if gas_start is None or gas_end is None or gas_end < gas_start:
                calibration.record_exclusion(RESULT_ELAPSED_GAP)
                self._calibration_snapshot = None
                return
            gas_kw = (gas_end - gas_start) / elapsed_h

        integrals = snapshot["integrals"]
        calibration.record_sample(
            Sample(
                delta_t=integrals["delta_t"] / elapsed_h,
                heat_kw=integrals["heat_kw"] / elapsed_h,
                solar_kw=integrals["solar_kw"] / elapsed_h,
                rate=moved / elapsed_h,
                gas_kw=gas_kw,
            ),
            prior,
        )
        await calibration.async_save()
        self._calibration_snapshot = None
        self._update_calibration_issue()

    # ------------------------------------------------------------------
    # Model accuracy
    # ------------------------------------------------------------------

    def _track_accuracy(
        self,
        *,
        now: datetime,
        indoor_temp: float,
        indoor_is_measured: bool,
        opt: ThermalOptimizationResult,
        durations: list[float],
        outdoor: list[float],
        solar: list[float],
    ) -> None:
        """Compare earlier 1-hour-ahead predictions with the measurement.

        Two predictions are kept per run: the model in use (possibly
        calibrated) and the energy-label model fed with the same planned
        heat. Their errors show whether calibration improves the model.
        The comparison assumes the plan was carried out.
        """
        if indoor_is_measured:
            remaining = []
            for due, predicted, predicted_label in self._pending_predictions:
                if due > now:
                    remaining.append((due, predicted, predicted_label))
                elif now - due <= ACCURACY_MATCH_TOLERANCE:
                    self._accuracy.append(
                        (now, predicted - indoor_temp, predicted_label - indoor_temp)
                    )
            self._pending_predictions = remaining
        else:
            self._pending_predictions = []
            return

        label_building = BuildingConfig.from_config(
            self.heat_coordinator.effective_config()
        )
        elapsed = 0.0
        predicted = label_temp = indoor_temp
        target_h = ACCURACY_HORIZON.total_seconds() / 3600.0
        for i, hours in enumerate(durations):
            if elapsed >= target_h or i >= len(opt.indoor_temps):
                break
            used = min(hours, target_h - elapsed)
            start_model = predicted
            predicted = start_model + (opt.indoor_temps[i] - start_model) * (
                used / hours
            )
            label_temp = label_building.next_indoor_temp(
                label_temp,
                outdoor_temp=outdoor[i],
                heat_input_kw=opt.thermal_power_kw[i],
                solar_gain_kw=solar[i] if i < len(solar) else 0.0,
                step_hours=used,
            )
            elapsed += used
        if elapsed >= target_h - 1e-6:
            self._pending_predictions.append(
                (now + ACCURACY_HORIZON, predicted, label_temp)
            )

    def accuracy_summary(self, now: datetime) -> dict[str, Any]:
        """Mean absolute error and bias of 1-hour-ahead predictions (24 h)."""
        recent = [a for a in self._accuracy if now - a[0] <= ACCURACY_WINDOW]
        if not recent:
            return {"samples": 0}
        n = len(recent)
        return {
            "samples": n,
            "mae_k": round(sum(abs(a[1]) for a in recent) / n, 3),
            "bias_k": round(sum(a[1] for a in recent) / n, 3),
            "mae_label_model_k": round(sum(abs(a[2]) for a in recent) / n, 3),
            "errors_k": [round(a[1], 2) for a in recent[-48:]],
        } | residual_diagnosis([a[1] for a in self._accuracy])
