"""Real-time PV-surplus controller (phase 5b, docs/redesign/REDESIGN.md).

Modelled on battery_controller's `zero_grid_controller.py`: a fast feedback
loop on a live grid-power reading, layered on top of the periodic DP
optimizer's plan rather than replacing it - the same relationship
`ZeroGridController` has to the DP schedule there.

Deliberately not a port of the continuous integrator battery_controller
uses (`target = last_target - gain * grid_error`, tuned so the settling
time stays bounded under sensor delay - see `ZERO_GRID_LOOP_GAIN` there).
That design exists because a battery inverter accepts a continuous power
setpoint; a heat pump here only ever accepts a whole-degree curve offset
(the same `offset` the DP itself plans in). Chasing a continuous target
with a discrete actuator is exactly the mismatch that causes oscillation,
so this is a plain deadbanded step controller instead: exporting more than
the deadband nudges the offset up by one degree, importing more than the
deadband nudges it back down, anything in between holds. No gain tuning
needed because there is no continuous quantity being tracked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .thermal_optimizer import DEFAULT_OFFSET_MAX, DEFAULT_OFFSET_MIN

_LOGGER = logging.getLogger(__name__)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class RealtimeControllerConfig:
    """Configuration for the real-time PV-surplus controller."""

    deadband_w: float = 300.0


class RealtimeController:
    """Nudges the offset up/down between DP runs based on live grid power.

    Only ever adjusts *around* the DP's planned offset for the current
    step - it never bypasses the DP's own curve-limit or ramp-rate
    constraints (the caller clamps `max_adjustment` to the same
    `max_offset_change` the DP itself respects), and its output is
    published as a separate sensor (`realtime_offset.py`) rather than
    silently changing `optimized_offset` - phase 3's backward-compatibility
    guarantee (legacy behaviour unchanged unless a user opts in) stays
    untouched by this.
    """

    def __init__(self, config: RealtimeControllerConfig):
        """Initialize the controller."""
        self.config = config
        self._last_adjustment: int = 0

    @property
    def last_adjustment(self) -> int:
        """Return the current adjustment memory, in whole degrees."""
        return self._last_adjustment

    def reset(self, value: int = 0) -> None:
        """Reset the adjustment memory (e.g. on a mode change or new DP run)."""
        self._last_adjustment = value

    def calculate_adjustment(
        self,
        *,
        current_grid_w: float,
        shadow_price_eur_per_kwh: float,
        max_adjustment: int,
    ) -> int:
        """Return the offset adjustment (whole degrees) to layer on the DP plan.

        `current_grid_w`: positive = importing from the grid, negative =
        exporting (PV surplus). `shadow_price_eur_per_kwh`: the DP's own
        estimate of what storing an extra kWh of heat right now is worth
        (`thermal_optimizer.optimize_thermal_schedule`'s return value) -
        acting on surplus is only worthwhile when this is positive; zero or
        negative means the building is already at (or past) the comfort
        ceiling, where more heat is wasted, not stored.
        """
        if max_adjustment <= 0:
            self._last_adjustment = 0
            return 0

        if current_grid_w <= -self.config.deadband_w and shadow_price_eur_per_kwh > 0:
            target = self._last_adjustment + 1
        elif current_grid_w >= self.config.deadband_w:
            target = self._last_adjustment - 1
        else:
            target = self._last_adjustment

        self._last_adjustment = int(_clamp(target, -max_adjustment, max_adjustment))
        return self._last_adjustment

    def get_control_action(
        self,
        *,
        current_grid_w: float,
        shadow_price_eur_per_kwh: float,
        planned_offset: int,
        max_adjustment: int,
        offset_min: int = DEFAULT_OFFSET_MIN,
        offset_max: int = DEFAULT_OFFSET_MAX,
    ) -> dict[str, Any]:
        """Return the adjustment plus the resulting effective offset.

        `effective_offset` is clamped to `[offset_min, offset_max]` - the
        DP's own absolute curve-offset bound - not just to `planned_offset
        +/- max_adjustment`. `max_adjustment` only bounds the *ramp-rate*
        step; without this clamp a `planned_offset` already at the DP's
        bound (e.g. +4 on a cold snap) plus a same-direction adjustment
        would report an effective_offset outside the range the heating
        curve is ever configured to reach.
        """
        adjustment = self.calculate_adjustment(
            current_grid_w=current_grid_w,
            shadow_price_eur_per_kwh=shadow_price_eur_per_kwh,
            max_adjustment=max_adjustment,
        )
        effective_offset = int(
            _clamp(planned_offset + adjustment, offset_min, offset_max)
        )
        return {
            "adjustment": adjustment,
            "effective_offset": effective_offset,
            "planned_offset": planned_offset,
            "current_grid_w": current_grid_w,
            "shadow_price_eur_per_kwh": shadow_price_eur_per_kwh,
        }


def create_realtime_controller(config: dict[str, Any]) -> RealtimeController:
    """Create a `RealtimeController` from a merged config-entry dict."""
    from .const import CONF_REALTIME_DEADBAND_W, DEFAULT_REALTIME_DEADBAND_W

    controller_config = RealtimeControllerConfig(
        deadband_w=float(
            config.get(CONF_REALTIME_DEADBAND_W, DEFAULT_REALTIME_DEADBAND_W)
        )
    )
    return RealtimeController(controller_config)
