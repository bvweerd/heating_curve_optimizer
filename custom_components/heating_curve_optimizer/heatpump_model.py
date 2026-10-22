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
    CONF_DEFROST_BASE_PENALTY,
    CONF_DEFROST_COLD_THRESHOLD,
    CONF_DEFROST_FREE_THRESHOLD,
    CONF_DEFROST_MIN_COP_MULTIPLIER,
    CONF_HEAT_PUMP_MAX_THERMAL_POWER,
    CONF_K_FACTOR,
    CONF_MIN_COP,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_COP_AT_35,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_DEFROST_BASE_PENALTY,
    DEFAULT_DEFROST_COLD_THRESHOLD,
    DEFAULT_DEFROST_FREE_THRESHOLD,
    DEFAULT_DEFROST_MIN_COP_MULTIPLIER,
    DEFAULT_K_FACTOR,
    DEFAULT_MIN_COP,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
)
from .helpers import calculate_defrost_factor

# Floor for the COP so electrical power never divides by a near-zero number.
MIN_COP = DEFAULT_MIN_COP

# Outdoor temperature at which a configured maximum thermal power is rated
# (the A7/W35 point of a data sheet).
CAPACITY_RATING_OUTDOOR_TEMP = 7.0
# Air-source capacity falls about 2 % per K colder outdoor air; bounded so a
# far-off rating point cannot make the heat pump vanish or explode.
CAPACITY_CHANGE_PER_K = 0.02
CAPACITY_FACTOR_BOUNDS = (0.5, 1.25)


@dataclass
class HeatPumpConfig:
    """Heat pump performance parameters.

    `cop_at` is the single COP implementation used everywhere in the
    integration (optimizer, sensors, calibration, gas-boiler comparison),
    so every figure the user sees is computed with the same model.
    """

    base_cop_at_35: float = DEFAULT_COP_AT_35
    k_factor: float = DEFAULT_K_FACTOR
    outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT
    cop_compensation_factor: float = DEFAULT_COP_COMPENSATION_FACTOR
    max_thermal_power_kw: float = 8.0
    # Outdoor temperature at which `max_thermal_power_kw` holds.
    capacity_rating_temp: float = CAPACITY_RATING_OUTDOOR_TEMP
    min_cop: float = DEFAULT_MIN_COP
    defrost_free_threshold: float = DEFAULT_DEFROST_FREE_THRESHOLD
    defrost_cold_threshold: float = DEFAULT_DEFROST_COLD_THRESHOLD
    defrost_base_penalty: float = DEFAULT_DEFROST_BASE_PENALTY
    defrost_min_cop_multiplier: float = DEFAULT_DEFROST_MIN_COP_MULTIPLIER

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        *,
        max_thermal_power_kw: float = 8.0,
        fallback_rating_temp: float = CAPACITY_RATING_OUTDOOR_TEMP,
    ) -> HeatPumpConfig:
        """Build a `HeatPumpConfig` from a merged config-entry dict.

        A configured `CONF_HEAT_PUMP_MAX_THERMAL_POWER` (rated at 7 °C
        outdoor) wins over the `max_thermal_power_kw` fallback supplied by
        the caller, which holds at `fallback_rating_temp`.
        """
        rating_temp = fallback_rating_temp
        configured = config.get(CONF_HEAT_PUMP_MAX_THERMAL_POWER)
        if configured is not None and float(configured) > 0:
            max_thermal_power_kw = float(configured)
            rating_temp = CAPACITY_RATING_OUTDOOR_TEMP
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
            capacity_rating_temp=rating_temp,
            min_cop=float(config.get(CONF_MIN_COP, DEFAULT_MIN_COP)),
            defrost_free_threshold=float(
                config.get(CONF_DEFROST_FREE_THRESHOLD, DEFAULT_DEFROST_FREE_THRESHOLD)
            ),
            defrost_cold_threshold=float(
                config.get(CONF_DEFROST_COLD_THRESHOLD, DEFAULT_DEFROST_COLD_THRESHOLD)
            ),
            defrost_base_penalty=float(
                config.get(CONF_DEFROST_BASE_PENALTY, DEFAULT_DEFROST_BASE_PENALTY)
            ),
            defrost_min_cop_multiplier=float(
                config.get(
                    CONF_DEFROST_MIN_COP_MULTIPLIER,
                    DEFAULT_DEFROST_MIN_COP_MULTIPLIER,
                )
            ),
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

        Clamped above by the Carnot COP for lifting heat from
        `outdoor_temp` to `supply_temp` (`T_hot_K / (T_hot_K - T_cold_K)`,
        the second-law upper bound no real heat pump can exceed). The
        linear formula above is a fit for typical operating ranges and has
        no such ceiling built in - at a small lift (mild outdoor temp with
        a low supply temp), combined with an aggressively tuned
        `k_factor`/`base_cop_at_35`, it can report a COP no real machine
        could deliver, which the optimizer would then chase as free heat.
        Real heat pumps land well under this limit (accepted for typical
        default parameters/operating ranges - see tests), so this only
        engages for a lift small enough, or parameters extreme enough,
        that the linear fit runs away from what physics allows.
        """
        cop = (
            self.base_cop_at_35
            + self.outdoor_temp_coefficient * outdoor_temp
            - self.k_factor * (supply_temp - 35.0)
        ) * self.cop_compensation_factor
        cop *= calculate_defrost_factor(
            outdoor_temp,
            humidity,
            defrost_free_threshold=self.defrost_free_threshold,
            defrost_cold_threshold=self.defrost_cold_threshold,
            defrost_base_penalty=self.defrost_base_penalty,
            defrost_min_cop_multiplier=self.defrost_min_cop_multiplier,
        )
        lift_k = supply_temp - outdoor_temp
        if lift_k > 0.1:
            carnot_cop = (supply_temp + 273.15) / lift_k
            cop = min(cop, carnot_cop)
        return max(self.min_cop, cop)

    def max_thermal_power_at(self, outdoor_temp: float) -> float:
        """Maximum thermal output (kW) at this outdoor temperature.

        An air-source heat pump loses capacity in colder air; the rated
        maximum is scaled by `CAPACITY_CHANGE_PER_K` per K away from the
        rating temperature, within `CAPACITY_FACTOR_BOUNDS`.
        """
        low, high = CAPACITY_FACTOR_BOUNDS
        factor = 1.0 + CAPACITY_CHANGE_PER_K * (
            outdoor_temp - self.capacity_rating_temp
        )
        return self.max_thermal_power_kw * min(high, max(low, factor))

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
