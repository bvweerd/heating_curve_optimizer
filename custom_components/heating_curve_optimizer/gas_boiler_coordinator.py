"""Coordinator for the optional hybrid gas-boiler cost comparison.

Compares the heat pump's cost per kWh thermal (electricity price / COP,
reusing `heatpump_model.HeatPumpConfig.cop_at`) against a configured gas
boiler's cost per kWh thermal (`gas_boiler_model.GasBoilerConfig`), and
publishes a `prefer_gas_boiler` recommendation the user's own automation can
act on - this integration never actuates real hardware directly (see
`climate.py`).

Only instantiated when a `GAS_SUBENTRY_TYPE` subentry is configured (see
`__init__.py`) - fully additive, reads `HeatCalculationCoordinator.data` and
`OptimizationCoordinator.data` but never writes back into them, so an
installation without the subentry is entirely unaffected.

The recommendation is gated on whether heat is actually needed right now:
comparing gas cost against heat-pump cost in isolation would be wrong,
because coasting on the building's thermal buffer costs nothing and always
beats both paid sources. See `_async_update_data`'s `heat_currently_needed`
computation.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_GAS_PRICE_SENSOR,
    DOMAIN,
)
from .coordinator import (
    HeatCalculationCoordinator,
    OptimizationCoordinator,
    _update_failed,
)
from .gas_boiler_model import GasBoilerConfig, compare_heat_pump_and_gas
from .heatpump_model import HeatPumpConfig

_LOGGER = logging.getLogger(__name__)

# Safety-net poll; real-world responsiveness comes from the state-change
# listeners on both price sensors (see async_setup).
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=15)


class GasBoilerCoordinator(DataUpdateCoordinator):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Coordinator comparing heat-pump vs. gas-boiler cost per kWh thermal."""

    def __init__(
        self,
        hass: HomeAssistant,
        heat_coordinator: HeatCalculationCoordinator,
        optimization_coordinator: OptimizationCoordinator,
        config: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the gas boiler coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Gas Boiler Comparison",
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.heat_coordinator = heat_coordinator
        self.optimization_coordinator = optimization_coordinator
        self.config = config
        self._entry_id = entry_id
        self._gas_price_sensor = config.get(CONF_GAS_PRICE_SENSOR)
        self._electricity_price_sensor = config.get(CONF_CONSUMPTION_PRICE_SENSOR)
        self._unsub = None

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

    async def _handle_price_change(self, event: Event) -> None:
        """Refresh unconditionally - this computation is O(1), unlike the
        DP re-run the main optimizer's 10%-change gate exists to avoid."""
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Clean up event tracking."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _read_gas_price_eur_per_m3(self) -> float | None:
        """Read the current gas price. No forecast support (see module
        docstring's design note): gas contracts reprice far less often
        than electricity, so only the current value is ever consumed even
        when the sensor happens to carry forecast attributes. Always reads
        `state.state` directly for that reason - a forecast array's [0] is
        the price at the start of the forecast window (e.g. midnight), not
        "now", so it must never be used as a stand-in for the current price
        (matches coordinator.py's own `current_price = float(price_state.state)`
        convention for the same "current price" use case)."""
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
        future_supply_temps = optimization_data.get("future_supply_temperatures")
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

        # Real ("is the heat pump actually drawing power") signal preferred
        # over the modeled demand-factor proxy - see coordinator.py's
        # heat_pump_actively_running for the full rationale. Only fall back
        # to the modeled signal when no power sensor is configured/available
        # (None, not False).
        actively_running = optimization_data.get("heat_pump_actively_running")
        if actively_running is not None:
            heat_currently_needed = actively_running
            heat_currently_needed_source = "power_sensor"
        else:
            heat_currently_needed = heat_data.get(
                "heat_pump_on", heat_data.get("net_heat_loss", 0.0) > 0.0
            )
            heat_currently_needed_source = "modeled_demand"

        prefer_gas_boiler = bool(heat_currently_needed) and comparison.prefer_gas

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
            "heat_currently_needed": bool(heat_currently_needed),
            "heat_currently_needed_source": heat_currently_needed_source,
            "prefer_gas_boiler": prefer_gas_boiler,
        }
