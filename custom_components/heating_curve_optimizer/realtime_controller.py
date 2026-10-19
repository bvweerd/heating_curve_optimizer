"""Real-time PV-surplus controller.

A fast feedback loop on live grid power, layered on top of the periodic DP
plan. The heat pump only accepts whole-degree curve offsets, so this is a
deadbanded step controller rather than a continuous integrator: exporting
more than the deadband (while the DP says stored heat is worth something)
raises the offset one degree above the plan. The extra degree is stepped
back down once the surplus is gone, judged on the grid power the house
would have *without* the heat pump: the import the heat pump itself causes
by soaking up the surplus must not undo the adjustment, or the offset flaps
between "export -> up" and "import -> down". An idle compressor with no
export also steps it down (the extra offset absorbs nothing and would wait
to be applied at the next start, at the full grid price), and so does a
non-positive shadow price (the building is full).

Every change holds for `min_hold_s` before the next one, so the heat pump's
own response to a new offset settles before it is judged.

The adjustment is only ever *upward* from the plan, bounded to
``[0, max_adjustment]``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .const import (
    CONF_REALTIME_DEADBAND_W,
    DEFAULT_OFFSET_MAX,
    DEFAULT_OFFSET_MIN,
    DEFAULT_REALTIME_DEADBAND_W,
    DEFAULT_REALTIME_MIN_HOLD_S,
)

_LOGGER = logging.getLogger(__name__)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class RealtimeControllerConfig:
    """Configuration for the real-time PV-surplus controller."""

    deadband_w: float = DEFAULT_REALTIME_DEADBAND_W
    min_hold_s: float = DEFAULT_REALTIME_MIN_HOLD_S


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
        self._last_change: datetime | None = None

    @property
    def last_adjustment(self) -> int:
        """Return the current adjustment memory, in whole degrees."""
        return self._last_adjustment

    def reset(self, value: int = 0) -> None:
        """Reset the adjustment memory (and the hold timer)."""
        self._last_adjustment = value
        self._last_change = None

    def _direction(
        self,
        *,
        current_grid_w: float,
        shadow_price_eur_per_kwh: float,
        heat_pump_running: bool | None,
        heat_pump_power_w: float | None,
    ) -> int:
        """+1, -1 or 0: where the surplus says the adjustment should go."""
        deadband = self.config.deadband_w
        if current_grid_w <= -deadband and shadow_price_eur_per_kwh > 0:
            return 1
        if shadow_price_eur_per_kwh <= 0 or heat_pump_running is False:
            return -1
        if heat_pump_power_w is not None:
            # Grid power without the heat pump: still exporting beyond the
            # deadband means the heat pump is running on surplus - hold.
            return -1 if current_grid_w - heat_pump_power_w > -deadband else 0
        return -1 if current_grid_w >= deadband else 0

    def calculate_adjustment(
        self,
        *,
        current_grid_w: float,
        shadow_price_eur_per_kwh: float,
        max_adjustment: int,
        heat_pump_running: bool | None = None,
        heat_pump_power_w: float | None = None,
        now: datetime | None = None,
    ) -> int:
        """Return the offset adjustment (whole degrees) to layer on the DP plan.

        `current_grid_w`: positive = importing from the grid, negative =
        exporting (PV surplus). `shadow_price_eur_per_kwh`: the DP's own
        estimate of what storing an extra kWh of heat right now is worth
        (`thermal_optimizer.optimize_thermal_schedule`'s return value) -
        acting on surplus is only worthwhile when this is positive; zero or
        negative means the building is already at (or past) the comfort
        ceiling, where more heat is wasted, not stored.
        `heat_pump_running` / `heat_pump_power_w`: the measured heat pump;
        None when unknown (then only grid import steps the adjustment down).
        `now`: enables the minimum hold time between changes.
        """
        if max_adjustment <= 0:
            self.reset()
            return 0

        current = min(self._last_adjustment, max_adjustment)
        target = int(
            _clamp(
                current
                + self._direction(
                    current_grid_w=current_grid_w,
                    shadow_price_eur_per_kwh=shadow_price_eur_per_kwh,
                    heat_pump_running=heat_pump_running,
                    heat_pump_power_w=heat_pump_power_w,
                ),
                0,
                max_adjustment,
            )
        )
        if target != current and now is not None:
            held = self._last_change is not None and now - self._last_change < (
                timedelta(seconds=self.config.min_hold_s)
            )
            if held:
                target = current
            else:
                self._last_change = now
        self._last_adjustment = target
        return target

    def get_control_action(
        self,
        *,
        current_grid_w: float,
        shadow_price_eur_per_kwh: float,
        planned_offset: int,
        max_adjustment: int,
        heat_pump_running: bool | None = None,
        heat_pump_power_w: float | None = None,
        now: datetime | None = None,
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
            heat_pump_running=heat_pump_running,
            heat_pump_power_w=heat_pump_power_w,
            now=now,
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
    controller_config = RealtimeControllerConfig(
        deadband_w=float(
            config.get(CONF_REALTIME_DEADBAND_W, DEFAULT_REALTIME_DEADBAND_W)
        )
    )
    return RealtimeController(controller_config)
