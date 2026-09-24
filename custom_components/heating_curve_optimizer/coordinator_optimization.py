"""Optimization coordinator for the Heating Curve Optimizer integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import issue_registry as ir, storage
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .building_model import BuildingConfig, EmitterConfig
from .calibration import ThermalCalibrationState
from .calibration import STORAGE_VERSION as CALIBRATION_STORAGE_VERSION
from .calibration import (
    MIN_INDOOR_TEMP_DELTA_C,
    RESULT_ELAPSED_GAP,
    RESULT_MISSING_BUILDING_CONFIG,
    RESULT_NO_INDOOR_SENSOR,
    RESULT_NO_POWER_READING,
    RESULT_STEP_TOO_SMALL,
)
from .const import (
    CONF_BASE_COP,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_EMITTER_TYPE,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_K_FACTOR,
    CONF_OFFSET_DELTA_T,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_PLANNING_WINDOW,
    CONF_POWER_CONSUMPTION,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_TIME_BASE,
    DEFAULT_COP_AT_35,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_EMITTER_TYPE,
    DEFAULT_K_FACTOR,
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_REALTIME_INTERVAL_S,
    DEFAULT_TIME_BASE,
    DOMAIN,
    INDOOR_TEMPERATURE,
)
from .coordinator_weather import _update_failed
from .coordinator_heat import HeatCalculationCoordinator
from .heatpump_model import HeatPumpConfig
from .helpers import extract_price_forecast_with_interval, max_offset_change
from .realtime_controller import RealtimeController, create_realtime_controller
from .thermal_optimizer import (
    DEFAULT_OFFSET_MAX,
    DEFAULT_OFFSET_MIN,
    optimize_thermal_schedule,
)

_LOGGER = logging.getLogger(__name__)

# Below this, the heat pump's electricity meter reading is treated as
# "confirmed idle" rather than "actively running" (standby/parasitic draw
# noise floor, not a real heating cycle). Used by
# OptimizationCoordinator._async_update_data's heat_pump_actively_running
# field - the hybrid gas-boiler feature's primary "is heat needed right now"
# signal when a power sensor is configured.
IDLE_POWER_THRESHOLD_KW = 0.1


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
    """Calculate COP from supply and outdoor temperatures.

    Clamped above by the Carnot COP for the actual lift (see
    heatpump_model.HeatPumpConfig.cop_at for the full rationale) so the
    displayed baseline/optimized COP and cost-savings figures can't run
    ahead of what a real heat pump could deliver at a small lift.
    """
    cop = (
        base_cop
        + outdoor_temp_coefficient * outdoor_temp
        - k_factor * (supply_temp - 35)
    ) * cop_compensation
    lift_k = supply_temp - outdoor_temp
    if lift_k > 0.1:
        carnot_cop = (supply_temp + 273.15) / lift_k
        cop = min(cop, carnot_cop)
    return max(0.5, cop)


class OptimizationCoordinator(DataUpdateCoordinator):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
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
        self._last_price: float | None = None
        self._current_buffer: float = 0.0  # Track actual buffer state
        self._current_offset: int = 0  # Track current offset for change constraint
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

        Runs every DEFAULT_REALTIME_INTERVAL_S seconds, once a full
        optimization cycle has already published a thermal_v2 result this
        session - otherwise there is no planned offset or shadow price to
        adjust around yet.
        """
        if self._realtime_controller is None:
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
        max_adjustment = max_offset_change(time_base, offset_delta_t)

        planned_offsets = thermal_v2.get("offsets", [])
        planned_offset = planned_offsets[0] if planned_offsets else 0
        shadow_price = thermal_v2.get("shadow_price_eur_per_kwh", 0.0)

        action = self._realtime_controller.get_control_action(
            current_grid_w=current_grid_w,
            shadow_price_eur_per_kwh=shadow_price,
            planned_offset=planned_offset,
            max_adjustment=max_adjustment,
            offset_min=DEFAULT_OFFSET_MIN,
            offset_max=DEFAULT_OFFSET_MAX,
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
            # net_heat_loss > 0: losing heat -> buffer decreases
            # net_heat_loss < 0: solar gain -> buffer increases
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

        # "Do nothing" cost comparison (offset=0, plain heating curve) -
        # independent of which optimizer runs below, used as the
        # cost_savings baseline. Runs in executor since it loops the full
        # planning window.
        result = await self.hass.async_add_executor_job(
            self._calculate_baseline,
            demand_forecast,
            price_forecast,
            temp_forecast,
            planning_window,
            time_base,
            k_factor,
            base_cop,
            outdoor_temp_coefficient,
            cop_compensation,
            min_supply,
            max_supply,
            min_outdoor,
            max_outdoor,
        )

        # The redesigned thermal optimizer (building_model.py/
        # heatpump_model.py/thermal_optimizer.py) is the only optimizer -
        # no legacy DP fallback. `_run_thermal_v2_optimization` never
        # raises itself (it catches internally and reports
        # `available: False`); a cycle where it's unavailable surfaces as
        # an ordinary coordinator failure instead of silently reusing a
        # different algorithm's answer.
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
        if not thermal_v2_result.get("available"):
            raise _update_failed(
                "thermal_optimizer_failed",
                translation_placeholders={"error": str(thermal_v2_result.get("error"))},
            )
        result["thermal_v2"] = thermal_v2_result

        v2_offsets = thermal_v2_result.get("offsets", [])
        v2_comfort_min = thermal_v2_result.get("building_comfort_min")
        v2_mass = thermal_v2_result.get("building_thermal_mass_kwh_per_k")
        v2_indoor_temps = thermal_v2_result.get("indoor_temps", [])
        # Re-express the v2 indoor-temperature trajectory as "stored kWh
        # above the comfort floor" so the existing heat_buffer sensor keeps
        # the same physical meaning - see REDESIGN.md §2.1.B on why that
        # floor-relative energy is the right equivalent of the legacy
        # buffer, now backed by a real state variable.
        if v2_comfort_min is not None and v2_mass is not None:
            v2_buffer = [
                round((t - v2_comfort_min) * v2_mass, 3) for t in v2_indoor_temps
            ]
        else:
            v2_buffer = []

        result["optimized_offset"] = v2_offsets[0] if v2_offsets else 0
        result["optimized_offsets"] = v2_offsets
        result["future_supply_temperatures"] = thermal_v2_result.get("supply_temps", [])
        result["buffer_evolution"] = v2_buffer
        result["initial_buffer"] = v2_buffer[0] if v2_buffer else 0.0
        result["total_cost"] = thermal_v2_result.get("total_cost_eur", 0.0)
        result["cost_savings"] = round(
            result.get("baseline_cost", 0.0)
            - thermal_v2_result.get("total_cost_eur", 0.0),
            3,
        )

        # Phase 4 (REDESIGN.md): feed one real observation into thermal
        # calibration, using whatever `result` now says was actually
        # applied.
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

        # Update tracked state for next optimization run.
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

        # Real (not modeled) confirmation of whether the heat pump is
        # currently drawing power - the hybrid gas-boiler feature (if
        # configured) uses this as its primary signal for "is heat actually
        # needed right now", preferring it over the demand-factor estimate
        # since it reflects what the appliance is actually doing. None means
        # "no power sensor configured/available", distinct from a confirmed
        # idle reading.
        power_kw = self._read_power_consumption_kw()
        result["heat_pump_power_kw"] = power_kw
        result["heat_pump_actively_running"] = (
            power_kw > IDLE_POWER_THRESHOLD_KW if power_kw is not None else None
        )

        # async_add_executor_job's return type is Any in this environment
        # (HomeAssistant is untyped - no py.typed here); result is genuinely
        # the dict[str, Any] this method built up from _calculate_baseline
        # and the thermal_v2 result.
        return cast(dict[str, Any], result)

    def _calculate_baseline(
        self,
        demand_forecast: list[float],
        price_forecast: list[float],
        temp_forecast: list[float],
        planning_window: int,
        time_base: int,
        k_factor: float,
        base_cop: float,
        outdoor_temp_coefficient: float,
        cop_compensation: float,
        min_supply: float,
        max_supply: float,
        min_outdoor: float,
        max_outdoor: float,
    ) -> dict[str, Any]:
        """Cost/supply-temperature of doing nothing (offset=0, plain
        heating curve) - the "what would this cost without optimization"
        comparison point `cost_savings` is measured against, independent of
        which optimizer actually drives `optimized_offset` (blocking call
        in executor).
        """
        max_steps = planning_window
        demand_limited = demand_forecast[:max_steps]
        price_limited = price_forecast[:max_steps]
        temp_limited = temp_forecast[:max_steps]
        step_hours = time_base / 60.0
        horizon = min(len(demand_limited), len(price_limited), len(temp_limited))

        baseline_supply_temps = []
        baseline_cop_list = []
        baseline_cost = 0.0

        for i in range(horizon):
            outdoor_temp = temp_limited[i]
            base_temp = _calculate_supply_temp_from_curve(
                outdoor_temp, min_supply, max_supply, min_outdoor, max_outdoor
            )
            base_cop_value = _calculate_cop(
                base_temp,
                outdoor_temp,
                base_cop,
                k_factor,
                outdoor_temp_coefficient,
                cop_compensation,
            )
            baseline_supply_temps.append(round(base_temp, 1))
            baseline_cop_list.append(round(base_cop_value, 3))

            demand = max(0.0, demand_limited[i])
            if base_cop_value > 0:
                baseline_cost += (
                    (demand / base_cop_value) * step_hours * price_limited[i]
                )

        return {
            "baseline_supply_temperatures": baseline_supply_temps,
            "baseline_cop": baseline_cop_list,
            "baseline_cost": round(baseline_cost, 3),
            "prices": [round(p, 5) for p in price_limited],
            "demand_forecast": [round(d, 3) for d in demand_limited],
            "outdoor_forecast": [round(t, 1) for t in temp_limited],
            "timestamp": dt_util.utcnow(),
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
        if self._calibration is None:
            return
        if not self.heat_coordinator.has_real_indoor_sensor:
            self._calibration.last_result = RESULT_NO_INDOOR_SENSOR
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
        if previous is None:
            pass  # First cycle ever - nothing to compare against yet.
        elif previous.get("heat_and_solar_kw") is None:
            self._calibration.last_result = RESULT_NO_POWER_READING
        else:
            elapsed_hours = (now - previous["timestamp"]).total_seconds() / 3600.0
            # Skip startup gaps (near-zero elapsed) and long outages (HA
            # restart, network loss) - both make the observed rate
            # meaningless rather than merely noisy.
            if not (0.05 <= elapsed_hours <= 3.0):
                self._calibration.last_result = RESULT_ELAPSED_GAP
            else:
                building = BuildingConfig.from_config(self.config)
                if building.area_m2 <= 0:
                    self._calibration.last_result = RESULT_MISSING_BUILDING_CONFIG
                elif (
                    abs(indoor_temp - previous["indoor_temp"]) < MIN_INDOOR_TEMP_DELTA_C
                ):
                    self._calibration.last_result = RESULT_STEP_TOO_SMALL
                else:
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
        """Run the thermal DP optimizer (blocking call, executor).

        Every error is caught and reported as `available: False` instead of
        propagating - the caller (`_async_update_data`) is what decides
        that an unavailable result means the whole update cycle failed
        (`_update_failed("thermal_optimizer_failed", ...)`), not this
        method itself.

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
            # `applied` already guarantees both learned_* fields are set
            # (calibration.py); the is-not-None checks here just let mypy
            # see that too, rather than trusting the property's own name.
            calibration = self._calibration
            learned_ua = calibration.learned_ua_w_per_k if calibration else None
            learned_mass = (
                calibration.learned_thermal_mass_kwh_per_k if calibration else None
            )
            if (
                calibration is not None
                and calibration.applied
                and learned_ua is not None
                and learned_mass is not None
            ):
                building.ua_w_per_k = learned_ua
                building.thermal_mass_kwh_per_k = learned_mass
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
            # check pump vs. radiator sizing, rather than block optimization
            # on a new required field.
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
                "calibration_last_result": (
                    calibration.last_result if calibration is not None else None
                ),
                "timestamp": dt_util.utcnow(),
            }
        except Exception as err:
            _LOGGER.warning("Thermal optimizer failed: %s", err)
            return {
                "available": False,
                "error": str(err),
                "timestamp": dt_util.utcnow(),
            }
