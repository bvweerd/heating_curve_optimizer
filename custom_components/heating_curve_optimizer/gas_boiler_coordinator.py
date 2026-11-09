"""Coordinator for the optional hybrid gas boiler.

Policy: **cheapest source for the heat needed now**, gas as comfort backup.

- When the heat-pump-only plan heats now (or the house is, or is about to
  be, below the band), gas is recommended if it is cheaper per kWh of heat
  than the plan's marginal heat (shadow price, at most electricity price /
  COP now). Holding the house at the bottom of the band is heat needed
  too: the plan only stays in the band because it heats.
- While the plan coasts (no heat now, house in the band), no gas: there is
  no heat to replace.
- When the plan shows the heat pump cannot bring the house back into the
  band within the lookahead (the plan already uses the heat pump as hard
  as it usefully can), gas is recommended regardless of price - unless
  the user switched off ``CONF_GAS_COMFORT_BACKUP``, in which case gas
  must also be cheaper.

The result is published as ``prefer_gas_boiler`` for the user's own
automation; this integration never actuates hardware.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COMFORT_LOOKAHEAD_HOURS,
    CONF_COMFORT_TOLERANCE_C,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_GAS_COMFORT_BACKUP,
    CONF_GAS_PRICE_SENSOR,
    DEFAULT_COMFORT_LOOKAHEAD_HOURS,
    DEFAULT_COMFORT_TOLERANCE_C,
    DEFAULT_GAS_COMFORT_BACKUP,
    DOMAIN,
)
from .coordinator_heat import INDOOR_SOURCE_SENSOR, HeatCalculationCoordinator
from .coordinator_optimization import OptimizationCoordinator
from .coordinator_weather import _update_failed
from .gas_boiler_model import GasBoilerConfig, compare_heat_pump_and_gas
from .heatpump_model import HeatPumpConfig
from .helpers import usable_state

_LOGGER = logging.getLogger(__name__)


REASON_BELOW_BAND = "below_comfort_band"
REASON_CANNOT_KEEP_UP = "heat_pump_cannot_keep_up"


def _plan_heats_now(optimization_data: dict[str, Any]) -> bool:
    """Whether the heat-pump plan delivers heat in the current step: its
    published on/off advice, else its planned heat output."""
    plan_on = optimization_data.get("heat_pump_plan_on")
    if plan_on is not None:
        return bool(plan_on)
    power = optimization_data.get("thermal_power_kw") or []
    return bool(power) and float(power[0]) > 0


def _comfort_at_risk(
    heat_data: dict[str, Any],
    optimization_data: dict[str, Any],
    comfort_lookahead_hours: float = DEFAULT_COMFORT_LOOKAHEAD_HOURS,
    comfort_tolerance_c: float = DEFAULT_COMFORT_TOLERANCE_C,
) -> tuple[bool, str | None, float | None]:
    """(at risk, reason, lowest planned indoor temperature in the lookahead).

    ``REASON_CANNOT_KEEP_UP``: the heat-pump-only plan is still below the
    comfort band at the end of the lookahead. ``REASON_BELOW_BAND``: the
    house is (or dips) below the band but the plan recovers in time.
    """
    comfort_min = optimization_data.get("comfort_min", heat_data.get("lower_bound"))
    # Per step: a scheduled setback is a planned dip, not a shortfall.
    step_floors = optimization_data.get("comfort_mins") or []
    planned: list[float] = []
    floors: list[float] = []
    elapsed = 0.0
    for i, (temp, hours) in enumerate(
        zip(
            optimization_data.get("indoor_temps") or [],
            optimization_data.get("step_durations_hours") or [],
            strict=False,
        )
    ):
        if elapsed >= comfort_lookahead_hours:
            break
        planned.append(temp)
        if comfort_min is not None:
            step_floor = step_floors[i] if i < len(step_floors) else comfort_min
            floors.append(step_floor - comfort_tolerance_c)
        elapsed += hours
    lowest = min(planned) if planned else None
    if comfort_min is None:
        return False, None, lowest
    floor_now = floors[0] if floors else comfort_min - comfort_tolerance_c
    indoor = heat_data.get("indoor_temperature")
    below_now = (
        heat_data.get("indoor_temperature_source") == "sensor"
        and indoor is not None
        and indoor < floor_now
    )
    dips = any(temp < floor for temp, floor in zip(planned, floors, strict=True))
    if planned and planned[-1] < floors[-1] and (below_now or dips):
        return True, REASON_CANNOT_KEEP_UP, lowest
    if below_now or dips:
        return True, REASON_BELOW_BAND, lowest
    return False, None, lowest


# Safety-net poll; real-world responsiveness comes from the state-change
# listeners on both price sensors (see async_setup).
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=15)

# "Gas cheaper" switches only when the other source is clearly cheaper -
# by this share of the gas cost or this amount, whichever is larger - and
# not within this long of the previous switch. Near break-even (a heat
# pump at an evening price peak) the plain comparison flipped on every
# quarter-hour price and every small change of the planned COP. A switch
# held back only by the hold time is made the moment the hold ends.
GAS_CHEAPER_DEADBAND_REL = 0.05
GAS_CHEAPER_DEADBAND_ABS = 0.005  # EUR/kWh heat
GAS_CHEAPER_MIN_HOLD = timedelta(hours=1)


class GasBoilerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator comparing heat-pump vs. gas-boiler cost per kWh thermal."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        heat_coordinator: HeatCalculationCoordinator,
        optimization_coordinator: OptimizationCoordinator,
        config: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the gas boiler coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Gas Boiler Comparison",
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.heat_coordinator = heat_coordinator
        self.optimization_coordinator = optimization_coordinator
        self.config = config
        self._entry_id = entry_id
        self._gas_price_sensor = config.get(CONF_GAS_PRICE_SENSOR)
        self._electricity_price_sensor = config.get(CONF_CONSUMPTION_PRICE_SENSOR)
        self._unsub: Any = None
        self._unsub_plan: Any = None
        # Published flags (with dead band) and since when, per comparison:
        # "now" (heat made now) and "plan" (the plan's marginal heat).
        self._flags: dict[str, tuple[bool, datetime]] = {}
        # When a switch held back by the hold time becomes due (this run).
        self._switch_due: list[datetime] = []
        self._unsub_hold: Any = None

    async def async_setup(self) -> None:
        """Track both price sensors - either changing makes the comparison stale."""
        sensors = [
            s for s in (self._gas_price_sensor, self._electricity_price_sensor) if s
        ]
        if sensors:
            self._unsub = async_track_state_change_event(
                self.hass, sensors, self._handle_price_change
            )
            _LOGGER.debug("Gas boiler comparison tracking price sensors: %s", sensors)
        # A new heat-pump plan can change whether comfort is at risk.
        self._unsub_plan = self.optimization_coordinator.async_add_listener(
            self._handle_plan_update
        )

    def inputs_ready(self) -> bool:
        """Whether both prices report and the heat-pump plan exists.

        Holds back the first comparison after a restart: it needs the plan's
        operating point, and the price sensors' integrations may still load.
        """
        return (
            usable_state(self.hass, self._gas_price_sensor) is not None
            and usable_state(self.hass, self._electricity_price_sensor) is not None
            and self.optimization_coordinator.data is not None
        )

    @callback
    def _handle_plan_update(self) -> None:
        if self.data is None and not self.inputs_ready():
            # Startup: the scheduled first refresh waits for the inputs;
            # refreshing now would only log an error.
            return
        self.hass.async_create_task(self.async_request_refresh())

    async def _handle_price_change(self, event: Event[EventStateChangedData]) -> None:
        """Refresh unconditionally - this computation is O(1), unlike the
        DP re-run the main optimizer's 10%-change gate exists to avoid."""
        if self.data is None and not self.inputs_ready():
            return  # startup: the scheduled first refresh waits for inputs
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Clean up event tracking."""
        for unsub in (self._unsub, self._unsub_plan, self._unsub_hold):
            if unsub:
                unsub()
        self._unsub = None
        self._unsub_plan = None
        self._unsub_hold = None
        await super().async_shutdown()

    def _read_gas_price_eur_per_m3(self) -> float | None:
        """Read the current gas price. No forecast support (see module
        docstring's design note): gas contracts reprice far less often
        than electricity, so only the current value is ever consumed even
        when the sensor happens to carry forecast attributes. Always reads
        `state.state` directly for that reason - a forecast array's [0] is
        the price at the start of the forecast window (e.g. midnight), not
        "now", so it must never be used as a stand-in for the current price."""
        if not self._gas_price_sensor:
            return None
        state = self.hass.states.get(self._gas_price_sensor)
        if not state or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Compute the heat-pump vs. gas-boiler cost comparison."""
        if not self._gas_price_sensor:
            raise _update_failed("no_gas_price_sensor")

        gas_price = self._read_gas_price_eur_per_m3()
        if gas_price is None:
            if self.hass.is_running:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"gas_price_sensor_unavailable_{self._entry_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="gas_price_sensor_unavailable",
                    translation_placeholders={"sensor": self._gas_price_sensor},
                )
            raise _update_failed(
                "gas_price_sensor_unavailable",
                translation_placeholders={"sensor": self._gas_price_sensor},
            )
        ir.async_delete_issue(
            self.hass, DOMAIN, f"gas_price_sensor_unavailable_{self._entry_id}"
        )

        # The main OptimizationCoordinator already raises its own repair
        # issue for this same sensor - a second one here would be
        # redundant, so this is a plain (non-issue-creating) failure.
        if not self._electricity_price_sensor:
            raise _update_failed("no_price_sensor")
        electricity_state = self.hass.states.get(self._electricity_price_sensor)
        if not electricity_state or electricity_state.state in (
            "unknown",
            "unavailable",
        ):
            raise _update_failed(
                "gas_boiler_electricity_price_unavailable",
                translation_placeholders={"sensor": self._electricity_price_sensor},
            )
        try:
            electricity_price = float(electricity_state.state)
        except (ValueError, TypeError) as err:
            raise _update_failed(
                "gas_boiler_electricity_price_unavailable",
                translation_placeholders={"sensor": self._electricity_price_sensor},
            ) from err

        heat_data = self.heat_coordinator.data
        optimization_data = self.optimization_coordinator.data
        if not heat_data or not optimization_data:
            # Transient - the other coordinators haven't completed their
            # first refresh yet. Self-heals once they do; no repair issue.
            raise _update_failed("gas_boiler_operating_point_unavailable")

        outdoor_temp = heat_data.get("outdoor_temperature")
        future_supply_temps = optimization_data.get("supply_temps")
        supply_temp = future_supply_temps[0] if future_supply_temps else None
        if outdoor_temp is None or supply_temp is None:
            raise _update_failed("gas_boiler_operating_point_unavailable")

        # The optimizer's own COP for this step: the same (possibly
        # calibrated) model, at the planned supply and forecast weather.
        planned_cops = optimization_data.get("cop") or []
        if planned_cops and planned_cops[0] > 0:
            cop = float(planned_cops[0])
        else:
            cop = HeatPumpConfig.from_config(self.config).cop_at(
                supply_temp=supply_temp, outdoor_temp=outdoor_temp
            )
        spot_cost_eur_per_kwh = electricity_price / cop
        # Gas now replaces the plan's marginal kWh of heat pump heat, which
        # may be bought hours earlier or later: the shadow price. A
        # pre-heated house coasting through a price peak costs that, not the
        # peak price / COP. Capped at the cost of heating right now, which
        # the plan can always fall back to; the cap also keeps a comfort
        # penalty in the shadow price from posing as an energy cost.
        shadow_price = optimization_data.get("shadow_price_eur_per_kwh")
        heat_pump_cost_eur_per_kwh = (
            spot_cost_eur_per_kwh
            if shadow_price is None
            else min(max(float(shadow_price), 0.0), spot_cost_eur_per_kwh)
        )

        boiler = GasBoilerConfig.from_config(self.config)
        gas_cost_eur_per_kwh = boiler.cost_per_kwh_thermal(gas_price)

        if not self._plan_settled(heat_data):
            # Right after a (re)start, before the indoor sensor reports, the
            # plan starts from the target temperature: its shadow price is a
            # placeholder (capped at the spot cost), so nothing is compared.
            return {
                "available": False,
                "reason": "waiting_for_plan",
                "gas_price_eur_per_m3": gas_price,
                "electricity_price_eur_per_kwh": electricity_price,
                "gas_cost_eur_per_kwh": gas_cost_eur_per_kwh,
            }

        now = dt_util.utcnow()
        self._switch_due = []
        # Heat made now: what "gas cheaper" / "heat pump cheaper" show.
        now_comparison = compare_heat_pump_and_gas(
            heat_pump_cost_eur_per_kwh=spot_cost_eur_per_kwh,
            gas_cost_eur_per_kwh=gas_cost_eur_per_kwh,
        )
        gas_cheaper = self._flag_with_deadband(
            "now", now_comparison.savings_eur_per_kwh, gas_cost_eur_per_kwh, now
        )
        # The plan's marginal heat: what the gas advice decides on.
        comparison = compare_heat_pump_and_gas(
            heat_pump_cost_eur_per_kwh=heat_pump_cost_eur_per_kwh,
            gas_cost_eur_per_kwh=gas_cost_eur_per_kwh,
        )
        gas_cheaper_than_plan = self._flag_with_deadband(
            "plan", comparison.savings_eur_per_kwh, gas_cost_eur_per_kwh, now
        )

        self._schedule_hold_refresh()

        comfort_at_risk, comfort_reason, lowest_planned = _comfort_at_risk(
            heat_data,
            optimization_data,
            comfort_lookahead_hours=float(
                self.config.get(
                    CONF_COMFORT_LOOKAHEAD_HOURS, DEFAULT_COMFORT_LOOKAHEAD_HOURS
                )
            ),
            comfort_tolerance_c=float(
                self.config.get(CONF_COMFORT_TOLERANCE_C, DEFAULT_COMFORT_TOLERANCE_C)
            ),
        )
        # Comfort backup: gas regardless of price when the heat pump cannot
        # restore comfort; otherwise the cheaper source makes the heat the
        # plan needs now (also the heat that holds the band's floor).
        comfort_backup = bool(
            self.config.get(CONF_GAS_COMFORT_BACKUP, DEFAULT_GAS_COMFORT_BACKUP)
        )
        heat_needed_now = _plan_heats_now(optimization_data)
        prefer_gas_boiler = (
            comfort_backup and comfort_reason == REASON_CANNOT_KEEP_UP
        ) or ((heat_needed_now or comfort_at_risk) and gas_cheaper_than_plan)

        return {
            "available": True,
            "gas_price_eur_per_m3": gas_price,
            "electricity_price_eur_per_kwh": electricity_price,
            "heat_pump_cop": cop,
            "supply_temp": supply_temp,
            "outdoor_temp": outdoor_temp,
            # The plan's marginal heat (shadow price, at most the cost now).
            "heat_pump_cost_eur_per_kwh": comparison.heat_pump_cost_eur_per_kwh,
            "heat_pump_cost_now_eur_per_kwh": spot_cost_eur_per_kwh,
            "shadow_price_eur_per_kwh": shadow_price,
            "gas_cost_eur_per_kwh": comparison.gas_cost_eur_per_kwh,
            # Heat made now vs. gas (positive: gas cheaper).
            "savings_eur_per_kwh": now_comparison.savings_eur_per_kwh,
            "savings_pct": now_comparison.savings_pct,
            "gas_cheaper": gas_cheaper,
            "gas_cheaper_since": self._flag_since("now"),
            # The plan's marginal heat vs. gas: the advice's price check.
            "plan_savings_eur_per_kwh": comparison.savings_eur_per_kwh,
            "gas_cheaper_than_plan": gas_cheaper_than_plan,
            "comfort_backup": comfort_backup,
            "heat_needed_now": heat_needed_now,
            "comfort_at_risk": comfort_at_risk,
            "comfort_reason": comfort_reason,
            "lowest_planned_indoor_temp": lowest_planned,
            "prefer_gas_boiler": prefer_gas_boiler,
        }

    def _plan_settled(self, heat_data: dict[str, Any]) -> bool:
        """Whether the plan starts from the measured indoor temperature (or
        no indoor sensor is configured)."""
        if not self.heat_coordinator.has_real_indoor_sensor:
            return True
        return heat_data.get("indoor_temperature_source") == INDOOR_SOURCE_SENSOR

    def _schedule_hold_refresh(self) -> None:
        """Re-compare when the earliest held-back switch is allowed, rather
        than at the next price change or poll."""
        if self._unsub_hold:
            self._unsub_hold()
            self._unsub_hold = None
        if not self._switch_due:
            return

        @callback
        def _hold_ended(_now: datetime) -> None:
            self._unsub_hold = None
            self.hass.async_create_task(self.async_request_refresh())

        self._unsub_hold = async_track_point_in_utc_time(
            self.hass, _hold_ended, min(self._switch_due)
        )

    def _flag_since(self, key: str) -> str | None:
        flag = self._flags.get(key)
        return flag[1].isoformat() if flag else None

    def _flag_with_deadband(
        self,
        key: str,
        savings_eur_per_kwh: float,
        gas_cost_eur_per_kwh: float,
        now: datetime,
    ) -> bool:
        """A "gas cheaper" flag with a dead band and a minimum hold time.

        `savings_eur_per_kwh` is heat pump cost minus gas cost. The first
        comparison sets the flag plainly; after that it only switches when
        the other source is cheaper by more than the dead band and the
        previous switch is at least `GAS_CHEAPER_MIN_HOLD` ago.
        """
        flag = self._flags.get(key)
        if flag is None:
            self._flags[key] = (savings_eur_per_kwh > 0, now)
            return savings_eur_per_kwh > 0
        cheaper, since = flag
        margin = max(
            GAS_CHEAPER_DEADBAND_ABS,
            GAS_CHEAPER_DEADBAND_REL * max(gas_cost_eur_per_kwh, 0.0),
        )
        switch = (
            savings_eur_per_kwh < -margin if cheaper else savings_eur_per_kwh > margin
        )
        if switch and now - since >= GAS_CHEAPER_MIN_HOLD:
            self._flags[key] = (not cheaper, now)
            return not cheaper
        if switch:
            self._switch_due.append(since + GAS_CHEAPER_MIN_HOLD)
        return cheaper
