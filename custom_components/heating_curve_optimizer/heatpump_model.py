"""Heat pump performance model: COP and deliverable thermal power.

Thermal equivalent of `efficiency_curve.py` in battery_controller: `cop_at`
plays the role of the charge/discharge efficiency curve, and
`electrical_power_kw` converts a thermal power decision into the electrical
power that is actually bought from the grid (or covered by PV).

Pure Python, no Home Assistant dependency - see building_model.py's module
docstring for why that matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_BASE_COP,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_K_FACTOR,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_COP_AT_35,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_K_FACTOR,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
)
from .helpers import calculate_defrost_factor

# Below this COP the "electrical power" calculation switches to a steep
# penalty instead of dividing by a near-zero number. Matches the legacy
# optimizer's floor (optimizer.py), kept for continuity of behaviour.
MIN_COP = 0.5


@dataclass
class HeatPumpConfig:
    """Heat pump performance parameters.

    `cop_at` reuses the exact COP formula the legacy optimizer used
    (optimizer.py's `_calculate_cop`), including the defrost factor -
    behaviour here is not new, only correctly *coupled to delivered heat*
    now that thermal_optimizer.py asks the emitter how much power a given
    supply temperature can actually push into the room.
    """

    base_cop_at_35: float = DEFAULT_COP_AT_35
    k_factor: float = DEFAULT_K_FACTOR
    outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT
    cop_compensation_factor: float = DEFAULT_COP_COMPENSATION_FACTOR
    max_thermal_power_kw: float = 8.0
    min_cop: float = MIN_COP

    @classmethod
    def from_config(
        cls, config: dict[str, Any], *, max_thermal_power_kw: float = 8.0
    ) -> HeatPumpConfig:
        """Build a `HeatPumpConfig` from a merged config-entry dict."""
        return cls(
            base_cop_at_35=float(config.get(CONF_BASE_COP, DEFAULT_COP_AT_35)),
            k_factor=float(config.get(CONF_K_FACTOR, DEFAULT_K_FACTOR)),
            outdoor_temp_coefficient=float(
                config.get(
                    CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
                )
            ),
            cop_compensation_factor=float(
                config.get(
                    CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
                )
            ),
            max_thermal_power_kw=max_thermal_power_kw,
        )

    def cop_at(
        self,
        *,
        supply_temp: float,
        outdoor_temp: float,
        humidity: float = 80.0,
    ) -> float:
        """COP at this operating point, including defrost losses.

        COP = (base + outdoor_coefficient * T_outdoor
                    - k_factor * (T_supply - 35)) * compensation_factor
              * defrost_factor(T_outdoor, humidity)
        """
        cop = (
            self.base_cop_at_35
            + self.outdoor_temp_coefficient * outdoor_temp
            - self.k_factor * (supply_temp - 35.0)
        ) * self.cop_compensation_factor
        cop *= calculate_defrost_factor(outdoor_temp, humidity)
        return max(self.min_cop, cop)

    def electrical_power_kw(
        self,
        *,
        thermal_power_kw: float,
        supply_temp: float,
        outdoor_temp: float,
        humidity: float = 80.0,
    ) -> float:
        """Electrical power (kW) needed to deliver `thermal_power_kw`."""
        if thermal_power_kw <= 0:
            return 0.0
        cop = self.cop_at(
            supply_temp=supply_temp, outdoor_temp=outdoor_temp, humidity=humidity
        )
        return thermal_power_kw / cop if cop > 0 else thermal_power_kw * 10
