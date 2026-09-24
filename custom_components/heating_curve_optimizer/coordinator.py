"""Data update coordinators for the Heating Curve Optimizer integration.

This module is a thin facade re-exporting the three coordinator classes and
their shared helpers from their own per-coordinator modules.  Import from
here to avoid breaking any existing ``from .coordinator import ...`` usage
in the rest of the package.
"""

from .coordinator_weather import WeatherDataCoordinator, _update_failed
from .coordinator_heat import HeatCalculationCoordinator
from .coordinator_optimization import (
    OptimizationCoordinator,
    IDLE_POWER_THRESHOLD_KW,
    _calculate_supply_temp_from_curve,
    _calculate_cop,
)

__all__ = [
    "WeatherDataCoordinator",
    "HeatCalculationCoordinator",
    "OptimizationCoordinator",
    "_update_failed",
    "IDLE_POWER_THRESHOLD_KW",
    "_calculate_supply_temp_from_curve",
    "_calculate_cop",
]
