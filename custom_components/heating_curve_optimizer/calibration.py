"""Thermal calibration: learn UA and thermal mass from real operation.

`building_model.BuildingConfig`'s
`ua_w_per_k` and `thermal_mass_kwh_per_k` start as rule-of-thumb estimates
(energy label, construction weight class). This module learns the real
values for a specific home from how its actual indoor temperature responds
to actual heat input, the same idea as battery_controller's
`efficiency_calibration.py` learning charge/discharge efficiency from real
dispatch instead of a nameplate number - a rolling window of real samples,
persisted via HA `Store`, gated behind a minimum sample count before a
learned value is trusted enough to change the plan, with a reset service as
the escape hatch (mirrors `CALIBRATION_WINDOW` /
`CALIBRATION_APPLY_THRESHOLD` / `async_reset` there almost one for one).

## The fit

Each sample observes one real step of the building's response: indoor
temperature before and after, outdoor temperature, and the heat + solar
input believed to have been delivered over that step. The 1R1C model says:

    T_in[t+1] - T_in[t]   heat_in_kw + solar_kw - (UA/1000) * (T_in - T_out)
    ------------------- = --------------------------------------------------
         step_hours                    thermal_mass_kwh_per_k

Rearranged, with `rate = (T_in[t+1]-T_in[t]) / step_hours` (°C/h) and
`delta_t = T_in[t] - T_out[t]`:

    thermal_mass_kwh_per_k * rate + (UA/1000) * delta_t = heat_in_kw + solar_kw

This is linear in the two unknowns `(thermal_mass_kwh_per_k, UA/1000)`, so
across N >= 2 samples it is an ordinary 2-variable least-squares fit, solved
in closed form (`fit_ua_and_thermal_mass`) - no external numerical
dependency needed.

## Independence from the prior being corrected

Feeding the fit `heat_in_kw` computed *from* the current UA estimate (e.g.
the model's own predicted heat loss) would be circular: the regression
would just rediscover its own prior. The heat input a sample carries must
come from something the UA/thermal-mass estimate did not produce - in this
integration, that is the real electricity meter
(`CONF_POWER_CONSUMPTION`) converted to thermal power via the heat pump's
COP curve (`heatpump_model.HeatPumpConfig.cop_at`), which depends on
`k_factor`/`base_cop`/outdoor and supply temperature, never on UA or
thermal mass. See coordinator_optimization.py's
`_maybe_record_calibration_sample` for
where that independence is enforced.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from homeassistant.helpers import storage

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Rolling window of samples behind the current fit - old samples age out
# rather than a lifetime average locking in early, noisy measurements.
CALIBRATION_WINDOW = 200

# A fit needs at least this many samples before it is trusted enough to
# override the label-based prior. Two samples are the mathematical minimum
# to solve for two unknowns; this is a much higher bar against noise.
MIN_SAMPLES_TO_APPLY = 30

# A fitted value further than this multiple from the label-based prior is
# almost certainly a bad sample (sensor glitch, mode transition, defrost
# cycle) rather than a real correction - mirrors
# CALIBRATION_ACCEPT_MIN/MAX in battery_controller's efficiency_calibration.py.
PLAUSIBLE_RATIO_BOUNDS = (0.3, 3.0)

# A step whose observed indoor-temperature change is smaller than this is
# mostly sensor-quantization noise, not signal - most HA temperature
# sensors report to 0.1°C, so a step needs to move the reading by a few
# multiples of that to be trustworthy. Mirrors
# CALIBRATION_MIN_DELTA_KWH/CALIBRATION_SOC_QUANTUM_FACTOR in
# battery_controller's efficiency_calibration.py: the floor is on the raw
# observed delta (here, °C), not on the derived rate (°C/h) - dividing a
# quantized delta by a short elapsed window would otherwise amplify the
# same quantization noise into an apparently large rate.
MIN_INDOOR_TEMP_DELTA_C = 0.3

RESULT_NO_RESULT = "no_result"
RESULT_FITTED = "fitted"
RESULT_IMPLAUSIBLE = "implausible"
RESULT_SINGULAR = "singular"
# Set directly by coordinator_optimization.py for a
# skip that happens before a sample ever reaches record_sample - mirrors
# battery_controller's richer last_result vocabulary
# (CALIBRATION_NO_SOC_SOURCE/..._STEP_INCOMPLETE/etc. in
# efficiency_calibration.py), so a user can tell "never had the chance to
# learn anything because X isn't configured/ready" apart from "learned
# something, then rejected it."
RESULT_NO_INDOOR_SENSOR = "no_indoor_sensor"
RESULT_NO_POWER_READING = "no_power_reading"
RESULT_ELAPSED_GAP = "elapsed_gap"
RESULT_MISSING_BUILDING_CONFIG = "missing_building_config"
RESULT_STEP_TOO_SMALL = "step_too_small"


def fit_ua_and_thermal_mass(
    samples: list[tuple[float, float, float]],
) -> tuple[float, float] | None:
    """Least-squares fit of (thermal_mass_kwh_per_k, ua_w_per_k).

    Each sample is `(delta_t, heat_and_solar_kw, rate_c_per_h)`:
    - `delta_t`: T_in - T_out at the start of the step, °C
    - `heat_and_solar_kw`: heat input + solar gain over the step, kW
    - `rate_c_per_h`: (T_in_end - T_in_start) / step_hours, °C/h

    Solves the normal equations of ordinary least squares for
    `thermal_mass_kwh_per_k * rate + (ua_w_per_k/1000) * delta_t =
    heat_and_solar_kw`. Returns `None` if there are fewer than 2 samples,
    the system is singular (samples too collinear to separate the two
    unknowns - e.g. delta_t barely varies), or the fit is unphysical
    (either unknown comes out <= 0).
    """
    if len(samples) < 2:
        return None

    s_rr = s_rd = s_dd = s_rb = s_db = 0.0
    for delta_t, heat_and_solar_kw, rate_c_per_h in samples:
        s_rr += rate_c_per_h * rate_c_per_h
        s_rd += rate_c_per_h * delta_t
        s_dd += delta_t * delta_t
        s_rb += rate_c_per_h * heat_and_solar_kw
        s_db += delta_t * heat_and_solar_kw

    det = s_rr * s_dd - s_rd * s_rd
    if abs(det) < 1e-9:
        return None

    thermal_mass = (s_rb * s_dd - s_db * s_rd) / det
    k = (s_rr * s_db - s_rd * s_rb) / det  # k = ua_w_per_k / 1000

    if thermal_mass <= 0 or k <= 0:
        return None
    return thermal_mass, k * 1000.0


def _within_plausible_bounds(value: float, prior: float) -> bool:
    """Return whether `value` is within PLAUSIBLE_RATIO_BOUNDS of `prior`."""
    if prior <= 0:
        return value > 0
    lo, hi = PLAUSIBLE_RATIO_BOUNDS
    return lo * prior <= value <= hi * prior


@dataclass
class ThermalCalibrationState:
    """Rolling window of thermal-response samples and the fit they produce.

    One instance per config entry (one building). `store` persists samples
    and the last fit across restarts; `async_reset` is the escape hatch a
    bad fit needs (mirrors battery_controller's per-direction reset
    services).
    """

    store: storage.Store[dict[str, Any]]
    samples: deque[tuple[float, float, float]] = field(
        default_factory=lambda: deque(maxlen=CALIBRATION_WINDOW)
    )
    learned_thermal_mass_kwh_per_k: float | None = None
    learned_ua_w_per_k: float | None = None
    last_result: str = RESULT_NO_RESULT

    @property
    def sample_count(self) -> int:
        """Number of observations behind the current fit."""
        return len(self.samples)

    @property
    def applied(self) -> bool:
        """Whether the learned values currently override the label-based prior."""
        return (
            self.sample_count >= MIN_SAMPLES_TO_APPLY
            and self.learned_ua_w_per_k is not None
            and self.learned_thermal_mass_kwh_per_k is not None
        )

    def record_sample(
        self,
        *,
        delta_t: float,
        heat_and_solar_kw: float,
        rate_c_per_h: float,
        prior_ua_w_per_k: float,
        prior_thermal_mass_kwh_per_k: float,
    ) -> bool:
        """Fold in one observed step; return whether the fit changed.

        The sample itself is always accepted into the window (it is a real
        observation), but a resulting fit that lands outside
        PLAUSIBLE_RATIO_BOUNDS of the label-based prior is treated as a bad
        window (collinear data, a burst of sensor noise) and not applied -
        the previous fit, if any, is kept rather than replaced by something
        implausible.
        """
        self.samples.append((delta_t, heat_and_solar_kw, rate_c_per_h))

        fit = fit_ua_and_thermal_mass(list(self.samples))
        if fit is None:
            self.last_result = RESULT_SINGULAR
            return False

        thermal_mass, ua = fit
        if not _within_plausible_bounds(
            ua, prior_ua_w_per_k
        ) or not _within_plausible_bounds(thermal_mass, prior_thermal_mass_kwh_per_k):
            _LOGGER.debug(
                "Thermal calibration: dropping implausible fit "
                "(UA=%.1f vs prior %.1f, mass=%.2f vs prior %.2f, n=%d)",
                ua,
                prior_ua_w_per_k,
                thermal_mass,
                prior_thermal_mass_kwh_per_k,
                self.sample_count,
            )
            self.last_result = RESULT_IMPLAUSIBLE
            return False

        previous_ua = self.learned_ua_w_per_k
        self.learned_ua_w_per_k = ua
        self.learned_thermal_mass_kwh_per_k = thermal_mass
        self.last_result = RESULT_FITTED
        moved = previous_ua is None or abs(ua - previous_ua) > 0.5
        if moved and self.applied:
            _LOGGER.info(
                "Thermal calibration updated: UA=%.1f W/K, thermal_mass=%.2f "
                "kWh/K (n=%d samples)",
                ua,
                thermal_mass,
                self.sample_count,
            )
        return moved

    async def async_load(self) -> None:
        """Restore persisted samples and the last fit, if any."""
        stored = await self.store.async_load()
        if stored is None:
            return
        raw_samples = stored.get("samples", [])
        self.samples = deque((tuple(s) for s in raw_samples), maxlen=CALIBRATION_WINDOW)
        self.learned_ua_w_per_k = stored.get("learned_ua_w_per_k")
        self.learned_thermal_mass_kwh_per_k = stored.get(
            "learned_thermal_mass_kwh_per_k"
        )
        if self.applied:
            _LOGGER.info(
                "Restored thermal calibration: UA=%.1f W/K, thermal_mass=%.2f "
                "kWh/K (n=%d samples)",
                self.learned_ua_w_per_k,
                self.learned_thermal_mass_kwh_per_k,
                self.sample_count,
            )

    async def async_save(self) -> None:
        """Persist the current samples and fit."""
        await self.store.async_save(
            {
                "samples": [list(s) for s in self.samples],
                "learned_ua_w_per_k": self.learned_ua_w_per_k,
                "learned_thermal_mass_kwh_per_k": self.learned_thermal_mass_kwh_per_k,
            }
        )

    async def async_reset(self) -> None:
        """Clear all samples and the learned fit, reverting to the prior."""
        if self.samples or self.learned_ua_w_per_k is not None:
            _LOGGER.info(
                "Resetting thermal calibration (%d samples, UA=%s, mass=%s)",
                self.sample_count,
                self.learned_ua_w_per_k,
                self.learned_thermal_mass_kwh_per_k,
            )
        self.samples.clear()
        self.learned_ua_w_per_k = None
        self.learned_thermal_mass_kwh_per_k = None
        self.last_result = RESULT_NO_RESULT
        await self.async_save()
