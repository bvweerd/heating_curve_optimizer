"""Coordinator for the optional hybrid gas boiler.

Policy: **heat pump first**, gas as comfort backup.

- While the heat pump keeps the house in its comfort band, it is the heat
  source - even when gas is cheaper.
- When the indoor temperature is (or is about to be) below the band but
  the heat-pump-only plan recovers within ``COMFORT_LOOKAHEAD_HOURS``, gas
  is only recommended if it is cheaper per kWh of heat.
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
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_GAS_COMFORT_BACKUP,
    CONF_GAS_PRICE_SENSOR,
    DEFAULT_GAS_COMFORT_BACKUP,
    DOMAIN,
)
from .coordinator_heat import HeatCalculationCoordinator
from .coordinator_optimization import OptimizationCoordinator
from .coordinator_weather import _update_failed
from .gas_boiler_model import GasBoilerConfig, compare_heat_pump_and_gas
from .heatpump_model import HeatPumpConfig

_LOGGER = logging.getLogger(__name__)

# How far ahead the heat-pump-only plan is checked for a comfort breach.
COMFORT_LOOKAHEAD_HOURS = 3.0
# Tolerance below the comfort floor before the plan counts as a breach.
COMFORT_TOLERANCE_C = 0.1


REASON_BELOW_BAND = "below_comfort_band"
REASON_CANNOT_KEEP_UP = "heat_pump_cannot_keep_up"


def _comfort_at_risk(
    heat_data: dict[str, Any], optimization_data: dict[str, Any]
) -> tuple[bool, str | None, float | None]:
    """(at risk, reason, lowest planned indoor temperature in the lookahead).

    ``REASON_CANNOT_KEEP_UP``: the heat-pump-only plan is still below the
    comfort band at the end of the lookahead. ``REASON_BELOW_BAND``: the
    house is (or dips) below the band but the plan recovers in time.
    """
    comfort_min = optimization_data.get("comfort_min", heat_data.get("lower_bound"))
    planned: list[float] = []
    elapsed = 0.0
    for temp, hours in zip(
        optimization_data.get("indoor_temps") or [],
        optimization_data.get("step_durations_hours") or [],
        strict=False,
    ):
        if elapsed >= COMFORT_LOOKAHEAD_HOURS:
            break
        planned.append(temp)
        elapsed += hours
    lowest = min(planned) if planned else None
    if comfort_min is None:
        return False, None, lowest
    floor = comfort_min - COMFORT_TOLERANCE_C
    indoor = heat_data.get("indoor_temperature")
    below_now = (
        heat_data.get("indoor_temperature_source") == "sensor"
        and indoor is not None
        and indoor < floor
    )
    dips = lowest is not None and lowest < floor
    if planned and planned[-1] < floor and (below_now or dips):
        return True, REASON_CANNOT_KEEP_UP, lowest
    if below_now or dips:
        return True, REASON_BELOW_BAND, lowest
    return False, None, lowest


# Safety-net poll; real-world responsiveness comes from the state-change
# listeners on both price sensors (see async_setup).
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=15)


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

    @callback
    def _handle_plan_update(self) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    async def _handle_price_change(self, event: Event[EventStateChangedData]) -> None:
        """Refresh unconditionally - this computation is O(1), unlike the
        DP re-run the main optimizer's 10%-change gate exists to avoid."""
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Clean up event tracking."""
        for unsub in (self._unsub, self._unsub_plan):
            if unsub:
                unsub()
        self._unsub = None
        self._unsub_plan = None
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

        heatpump = HeatPumpConfig.from_config(self.config)
        cop = heatpump.cop_at(supply_temp=supply_temp, outdoor_temp=outdoor_temp)
        heat_pump_cost_eur_per_kwh = electricity_price / cop

        boiler = GasBoilerConfig.from_config(self.config)
        gas_cost_eur_per_kwh = boiler.cost_per_kwh_thermal(gas_price)

        comparison = compare_heat_pump_and_gas(
            heat_pump_cost_eur_per_kwh=heat_pump_cost_eur_per_kwh,
            gas_cost_eur_per_kwh=gas_cost_eur_per_kwh,
        )

        comfort_at_risk, comfort_reason, lowest_planned = _comfort_at_risk(
            heat_data, optimization_data
        )
        # Comfort backup: gas regardless of price when the heat pump cannot
        # restore comfort; otherwise only when comfort is at risk and gas
        # is the cheaper source.
        comfort_backup = bool(
            self.config.get(CONF_GAS_COMFORT_BACKUP, DEFAULT_GAS_COMFORT_BACKUP)
        )
        prefer_gas_boiler = (
            comfort_backup and comfort_reason == REASON_CANNOT_KEEP_UP
        ) or (comfort_at_risk and comparison.prefer_gas)

        return {
            "available": True,
            "gas_price_eur_per_m3": gas_price,
            "electricity_price_eur_per_kwh": electricity_price,
            "heat_pump_cop": cop,
            "supply_temp": supply_temp,
            "outdoor_temp": outdoor_temp,
            "heat_pump_cost_eur_per_kwh": comparison.heat_pump_cost_eur_per_kwh,
            "gas_cost_eur_per_kwh": comparison.gas_cost_eur_per_kwh,
            "savings_eur_per_kwh": comparison.savings_eur_per_kwh,
            "savings_pct": comparison.savings_pct,
            "gas_cheaper": comparison.prefer_gas,
            "comfort_backup": comfort_backup,
            "comfort_at_risk": comfort_at_risk,
            "comfort_reason": comfort_reason,
            "lowest_planned_indoor_temp": lowest_planned,
            "prefer_gas_boiler": prefer_gas_boiler,
        }
