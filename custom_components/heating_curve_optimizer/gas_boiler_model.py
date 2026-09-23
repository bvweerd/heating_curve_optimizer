"""Gas boiler cost model, for the optional hybrid gas-boiler comparison.

Thermal equivalent role to `heatpump_model.py`'s `HeatPumpConfig`: converts a
configured appliance's parameters into a cost per kWh of heat delivered, so
it can be compared against the heat pump's own cost per kWh
(`heatpump_model.HeatPumpConfig.cop_at` combined with the electricity
price). See `gas_boiler_coordinator.py` for the "is heat actually needed
right now" gating this feeds into - this module is only the pure cost math,
deliberately unaware of whether heat is currently needed at all.

Pure Python, no Home Assistant dependency - see building_model.py's module
docstring for why that matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_GAS_BOILER_EFFICIENCY,
    CONF_GAS_CALORIFIC_VALUE,
    DEFAULT_GAS_BOILER_EFFICIENCY,
    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
)


@dataclass
class GasBoilerConfig:
    """Gas boiler cost parameters.

    `efficiency` is a plain fraction (0.90 = 90%), matching how
    `cop_compensation_factor` and other multipliers are already expressed
    elsewhere in this codebase, not a 0-100 percentage.
    """

    efficiency: float = DEFAULT_GAS_BOILER_EFFICIENCY
    calorific_value_kwh_per_m3: float = DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> GasBoilerConfig:
        """Build a `GasBoilerConfig` from a merged config-entry dict."""
        return cls(
            efficiency=float(
                config.get(CONF_GAS_BOILER_EFFICIENCY, DEFAULT_GAS_BOILER_EFFICIENCY)
            ),
            calorific_value_kwh_per_m3=float(
                config.get(
                    CONF_GAS_CALORIFIC_VALUE,
                    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
                )
            ),
        )

    def cost_per_kwh_thermal(self, gas_price_eur_per_m3: float) -> float:
        """Cost (EUR) per kWh of heat delivered, at this gas price.

        `(gas_price / calorific_value) / efficiency`. A non-positive
        calorific value or efficiency makes the cost undefined rather than
        a divide-by-zero crash or a nonsensical negative/zero cost - such a
        misconfiguration must never look like "free heat" and win every
        comparison against the heat pump.
        """
        if self.calorific_value_kwh_per_m3 <= 0 or self.efficiency <= 0:
            return float("inf")
        return (
            gas_price_eur_per_m3 / self.calorific_value_kwh_per_m3
        ) / self.efficiency


@dataclass
class HybridComparison:
    """Result of comparing heat-pump cost against gas-boiler cost."""

    heat_pump_cost_eur_per_kwh: float
    gas_cost_eur_per_kwh: float
    prefer_gas: bool
    savings_eur_per_kwh: float
    savings_pct: float


def compare_heat_pump_and_gas(
    heat_pump_cost_eur_per_kwh: float, gas_cost_eur_per_kwh: float
) -> HybridComparison:
    """Compare the two per-kWh-thermal costs.

    `savings_eur_per_kwh` is `heat_pump_cost - gas_cost`: positive means gas
    is cheaper. An exact tie deterministically prefers the heat pump (not
    gas) so floating-point noise on an equal-cost boundary never flips the
    recommendation.
    """
    savings = heat_pump_cost_eur_per_kwh - gas_cost_eur_per_kwh
    savings_pct = (
        (savings / heat_pump_cost_eur_per_kwh) * 100.0
        if heat_pump_cost_eur_per_kwh > 0
        else 0.0
    )
    return HybridComparison(
        heat_pump_cost_eur_per_kwh=heat_pump_cost_eur_per_kwh,
        gas_cost_eur_per_kwh=gas_cost_eur_per_kwh,
        prefer_gas=savings > 0,
        savings_eur_per_kwh=savings,
        savings_pct=savings_pct,
    )
