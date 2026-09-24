"""Real-time PV-surplus controller.

A fast feedback loop on live grid power, layered on top of the periodic DP
plan. The heat pump only accepts whole-degree curve offsets, so this is a
deadbanded step controller rather than a continuous integrator: exporting
more than the deadband (while the DP says stored heat is worth something)
raises the offset one degree above the plan; once the export is gone,
importing more than the deadband steps that extra back down.

The adjustment is only ever *upward* from the plan, bounded to
``[0, max_adjustment]``. Importing from the grid is the normal state of a
running heat pump, so import alone must never push the offset below what
the DP planned - it only unwinds surplus-driven increases.
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
    """Raises the offset above the DP plan while PV surplus is exported.

    The caller bounds `max_adjustment` by the DP's own ramp-rate limit; the
    result is published as the effective offset of the real-time sensor,
    never written back into the plan itself.
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

        self._last_adjustment = int(_clamp(target, 0, max_adjustment))
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
