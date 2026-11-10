"""Heat the gas boiler puts into the space heating, for the building model.

Without it a gas run warms the house with no heat in the model: the thermal
mass estimate lags (a negative buffer right after heating on gas) and the
learned heat loss comes out low. The heat comes from the best source the
user has:

1. a heat power sensor of the boiler (heat meter, ems-esp, ebusd) - measured;
2. a "boiler heats the house" signal times the boiler's output, where the
   output is learned from the gas meter: gas heat / hours the signal was on,
   per meter step, skipping steps with tap water;
3. nothing - the mass observer then only follows the measured air.

A gas meter cannot tell space heating from tap water (or cooking); that
separation comes from the heating signal and the DHW-active sensor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .helpers import read_power_kw

STORAGE_VERSION = 1

SOURCE_SENSOR = "sensor"
SOURCE_LEARNED = "learned"
SOURCE_CONFIGURED = "configured"
SOURCE_NONE = "none"

# A meter step counts only with enough heating time behind it: one coarse
# step over a few minutes of flame would give a wild rate.
MIN_STEP_HEATING_HOURS = 0.25
# Hours of heating behind the learned output before it replaces the
# configured one.
MIN_LEARNED_HOURS = 2.0
# Weight of the history against a new step, in hours: the learned output is
# a running mean over roughly the last day of heating.
LEARN_MEMORY_HOURS = 24.0
# Plausible boiler output; a step outside it (stale meter, misaligned step)
# is ignored.
OUTPUT_BOUNDS_KW = (0.5, 60.0)

HEATING_ACTIONS = ("heating", "preheating")


def heating_signal_on(hass: Any, entity_id: str | None) -> bool:
    """Whether the "boiler heats the house" signal is on.

    A climate entity counts when its ``hvac_action`` is heating; a binary
    sensor, switch or input boolean when it is on.
    """
    if not entity_id:
        return False
    state = hass.states.get(entity_id)
    if state is None:
        return False
    if entity_id.startswith("climate."):
        return bool(state.attributes.get("hvac_action") in HEATING_ACTIONS)
    return bool(state.state == "on")


@dataclass
class GasBoilerOutputLearner:
    """Learns the boiler's heat output from gas meter steps.

    `tick` is called every minute with whether the boiler heats the house,
    whether tap water is drawn and the gas meter's heat counter (kWh heat,
    efficiency applied). Heating time is summed between meter steps; at a
    step the step's heat over that time is one sample.
    """

    learned_kw: float | None = None
    learned_hours: float = 0.0
    _last_counter_kwh: float | None = None
    _last_tick: datetime | None = None
    _heating_hours: float = 0.0
    _dhw_seen: bool = False

    def tick(
        self,
        now: datetime,
        *,
        heating: bool,
        dhw: bool,
        counter_kwh: float | None,
    ) -> bool:
        """Advance one tick; True when the learned output changed."""
        last, self._last_tick = self._last_tick, now
        if last is not None:
            hours = (now - last).total_seconds() / 3600.0
            if 0 < hours <= 0.1:  # skip gaps (restart, missed ticks)
                if heating:
                    self._heating_hours += hours
                self._dhw_seen |= dhw
        if counter_kwh is None:
            return False
        previous = self._last_counter_kwh
        if previous is None or counter_kwh < previous:
            # First reading or a meter reset: start a fresh interval.
            self._start_interval(counter_kwh)
            return False
        if counter_kwh == previous:
            return False
        step_kwh = counter_kwh - previous
        heating_hours = self._heating_hours
        usable = not self._dhw_seen and heating_hours >= MIN_STEP_HEATING_HOURS
        self._start_interval(counter_kwh)
        if not usable:
            return False
        rate = step_kwh / heating_hours
        low, high = OUTPUT_BOUNDS_KW
        if not low <= rate <= high:
            return False
        if self.learned_kw is None:
            self.learned_kw = rate
        else:
            weight = heating_hours / (
                heating_hours + min(self.learned_hours, LEARN_MEMORY_HOURS)
            )
            self.learned_kw += weight * (rate - self.learned_kw)
        self.learned_hours += heating_hours
        return True

    def _start_interval(self, counter_kwh: float) -> None:
        self._last_counter_kwh = counter_kwh
        self._heating_hours = 0.0
        self._dhw_seen = False

    def output_kw(self, configured_kw: float) -> tuple[float, str]:
        """The output to use and where it comes from."""
        if self.learned_kw is not None and self.learned_hours >= MIN_LEARNED_HOURS:
            return self.learned_kw, SOURCE_LEARNED
        return configured_kw, SOURCE_CONFIGURED

    def as_dict(self) -> dict[str, Any]:
        """What survives a restart: the learned output, not the interval."""
        return {"learned_kw": self.learned_kw, "learned_hours": self.learned_hours}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> GasBoilerOutputLearner:
        data = data or {}
        learned = data.get("learned_kw")
        return cls(
            learned_kw=float(learned) if learned is not None else None,
            learned_hours=float(data.get("learned_hours", 0.0)),
        )


def gas_boiler_heat_kw(
    hass: Any,
    *,
    power_sensor: str | None,
    heating_sensor: str | None,
    dhw_on: bool,
    output_kw: float,
) -> float:
    """Heat the boiler delivers to the space heating now, in kW.

    A configured power sensor wins (W or kW; unreadable counts as 0, not as
    the estimate, so a broken sensor is not papered over). Otherwise the
    heating signal times `output_kw`, but not while tap water is drawn - a
    combi boiler gives tap water priority.
    """
    if power_sensor:
        kw = read_power_kw(hass, power_sensor, default_unit="kW")
        return max(kw or 0.0, 0.0)
    if not heating_sensor or dhw_on:
        return 0.0
    return output_kw if heating_signal_on(hass, heating_sensor) else 0.0
