"""Thermal calibration: learn the building model from real operation.

The 1R1C model of `building_model.BuildingConfig` starts from rule-of-thumb
values (energy label, construction class, window areas, W/m² internal
gains). This module learns four parameters of a specific home from how its
measured indoor temperature responds to the heat that was put in:

    C · rate + UA · ΔT − s · Q_solar − g = Q_heat

- ``C``   thermal mass, kWh/K
- ``UA``  heat loss coefficient, kW/K (stored as W/K)
- ``s``   correction factor on the modelled window solar gain
- ``g``   constant internal gains, kW

``rate`` is the observed indoor temperature change (K/h) over an observation
window, ``ΔT`` the mean indoor-outdoor difference, ``Q_solar`` the mean
modelled solar gain and ``Q_heat`` the mean delivered heat - measured by a
thermal power sensor, or electrical power × modelled COP. In the latter
case an error in the COP level scales ``C``, ``UA`` and ``g`` alike: the
time constant ``C/UA`` and the electricity needed to hold a temperature
(``UA/COP``) stay correct, which is what the optimizer depends on.

The fit is ridge-regularised towards the label-based prior (each parameter
normalised by its prior), so a handful of samples cannot produce wild
values, and the prior fades as evidence accumulates. A fit is only offered
for use when it passes quality gates: enough samples, enough variation
(heating *and* coasting), a minimum coefficient of determination, and
plausible values.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from homeassistant.helpers import storage

from .const import ENERGY_LABELS, calculate_htc_from_energy_label

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 2

# Rolling window of samples behind the current fit.
CALIBRATION_WINDOW = 300
# Quality gates before a fit may be applied.
MIN_SAMPLES_TO_APPLY = 30
MIN_R_SQUARED = 0.5
# Both heating and coasting windows are needed to separate C from UA.
MIN_SHARE_EACH_DIRECTION = 0.15
# A window closes once the indoor temperature has moved this far.
MIN_INDOOR_TEMP_DELTA_C = 0.3
# Ridge strength, in "equivalent samples" pulling each normalised parameter
# towards its prior.
PRIOR_STRENGTH = 3.0

# Plausibility bounds, as multiples of the prior (UA, C) or absolute.
RATIO_BOUNDS = (0.3, 3.0)
SOLAR_FACTOR_BOUNDS = (0.0, 2.5)
INTERNAL_GAIN_MAX_W_PER_M2 = 12.0

# Calibration modes (zone setting).
MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_APPLY = "apply"
CALIBRATION_MODES = [MODE_OFF, MODE_OBSERVE, MODE_APPLY]

# Status reported to the user.
STATUS_OFF = "off"
STATUS_UNAVAILABLE = "unavailable"
STATUS_COLLECTING = "collecting"
STATUS_PROVISIONAL = "provisional"
STATUS_READY = "ready"
STATUS_APPLIED = "applied"
CALIBRATION_STATUSES = [
    STATUS_OFF,
    STATUS_UNAVAILABLE,
    STATUS_COLLECTING,
    STATUS_PROVISIONAL,
    STATUS_READY,
    STATUS_APPLIED,
]

# Why the latest observation window did not become a sample.
RESULT_NO_RESULT = "no_result"
RESULT_FITTED = "fitted"
RESULT_IMPLAUSIBLE = "implausible"
RESULT_SINGULAR = "singular"
RESULT_NO_INDOOR_SENSOR = "no_indoor_sensor"
RESULT_NO_POWER_READING = "no_power_reading"
RESULT_ELAPSED_GAP = "elapsed_gap"
RESULT_MISSING_BUILDING_CONFIG = "missing_building_config"
RESULT_STEP_TOO_SMALL = "step_too_small"
RESULT_EXCLUDED_DHW = "excluded_dhw"
RESULT_EXCLUDED_WINDOW = "excluded_window_open"
RESULT_EXCLUDED_JUMP = "excluded_temperature_jump"
RESULT_MULTI_ZONE = "multi_zone"

EXCLUSION_RESULTS = (
    RESULT_EXCLUDED_DHW,
    RESULT_EXCLUDED_WINDOW,
    RESULT_EXCLUDED_JUMP,
    RESULT_ELAPSED_GAP,
)


@dataclass(frozen=True)
class Sample:
    """One observation window, as means over the window."""

    delta_t: float  # indoor - outdoor, K
    heat_kw: float  # heat pump heat (measured, or electrical x modelled COP)
    solar_kw: float  # modelled window solar gain
    rate: float  # observed indoor temperature change, K/h
    gas_kw: float = 0.0  # gas boiler heat from the gas meter (absolute)

    def as_list(self) -> list[float]:
        return [self.delta_t, self.heat_kw, self.solar_kw, self.rate, self.gas_kw]


@dataclass(frozen=True)
class Prior:
    """Label-based starting values the fit is regularised towards."""

    ua_w_per_k: float
    thermal_mass_kwh_per_k: float
    internal_gain_kw: float
    area_m2: float


@dataclass(frozen=True)
class FitResult:
    """A fitted parameter set and its quality."""

    ua_w_per_k: float
    thermal_mass_kwh_per_k: float
    solar_factor: float
    internal_gain_kw: float
    # Heat pump heat scale (1 = the COP model is right). Only learned from
    # gas-meter periods when the heat pump heat is itself modelled.
    cop_scale: float
    r_squared: float
    sample_count: int
    heating_share: float
    plausible: bool

    @property
    def time_constant_hours(self) -> float:
        return self.thermal_mass_kwh_per_k / (self.ua_w_per_k / 1000.0)


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None if singular."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col:
                factor = a[r][col] / a[col][col]
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


# Gas-heated observation windows needed before the COP scale is learned.
MIN_GAS_SAMPLES = 5


def _ridge(
    rows: list[list[float]], ys: list[float], prior_x: list[float]
) -> list[float] | None:
    """Ridge least squares towards ``prior_x`` (columns already normalised)."""
    n = len(prior_x)
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(n)] for i in range(n)]
    aty = [sum(r[i] * y for r, y in zip(rows, ys, strict=True)) for i in range(n)]
    for i in range(n):
        lam = PRIOR_STRENGTH * max(ata[i][i] / len(rows), 1e-9)
        ata[i][i] += lam
        aty[i] += lam * prior_x[i]
    return _solve(ata, aty)


def fit_building(samples: list[Sample], prior: Prior) -> FitResult | None:
    """Ridge least-squares fit of (C, UA, s, g[, φ]), regularised to the prior.

        C·rate + UA·ΔT − s·Q_solar − g − φ·Q_hp = Q_hp + Q_gas

    ``φ`` (heat pump heat scale − 1) is only included when enough windows
    contain absolute gas heat; without them it is not identifiable (every
    parameter could be scaled together) and is fixed at 0.
    """
    if len(samples) < 2:
        return None
    use_gas = sum(1 for s in samples if s.gas_kw > 0.1) >= MIN_GAS_SAMPLES
    # Each parameter expressed relative to its prior, so one ridge strength
    # fits all of them.
    scales = [
        max(prior.thermal_mass_kwh_per_k, 0.5),
        max(prior.ua_w_per_k / 1000.0, 0.02),
        1.0,
        max(prior.internal_gain_kw, 0.2),
        1.0,
    ]
    prior_x = [
        prior.thermal_mass_kwh_per_k / scales[0],
        (prior.ua_w_per_k / 1000.0) / scales[1],
        1.0,
        prior.internal_gain_kw / scales[3],
        0.0,
    ]
    n = 5 if use_gas else 4
    rows = [
        [
            s.rate * scales[0],
            s.delta_t * scales[1],
            -s.solar_kw * scales[2],
            -scales[3],
            -s.heat_kw * scales[4],
        ][:n]
        for s in samples
    ]
    ys = [s.heat_kw + s.gas_kw for s in samples]
    x = _ridge(rows, ys, prior_x[:n])
    if x is None:
        return None

    thermal_mass = x[0] * scales[0]
    ua_kw = x[1] * scales[1]
    solar_factor = x[2] * scales[2]
    internal_kw = x[3] * scales[3]
    cop_scale = 1.0 + (x[4] if use_gas else 0.0)
    if thermal_mass <= 0 or ua_kw <= 0 or cop_scale <= 0:
        return None

    predicted = [sum(r[i] * x[i] for i in range(n)) for r in rows]
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predicted, strict=True))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    heating_share = sum(1 for s in samples if s.rate > 0) / len(samples)

    lo, hi = RATIO_BOUNDS
    plausible = (
        lo * prior.ua_w_per_k <= ua_kw * 1000.0 <= hi * prior.ua_w_per_k
        and lo * prior.thermal_mass_kwh_per_k
        <= thermal_mass
        <= hi * prior.thermal_mass_kwh_per_k
        and SOLAR_FACTOR_BOUNDS[0] <= solar_factor <= SOLAR_FACTOR_BOUNDS[1]
        and 0.0
        <= internal_kw
        <= INTERNAL_GAIN_MAX_W_PER_M2 * max(prior.area_m2, 1.0) / 1000.0
        and COP_SCALE_BOUNDS[0] <= cop_scale <= COP_SCALE_BOUNDS[1]
    )
    return FitResult(
        ua_w_per_k=ua_kw * 1000.0,
        thermal_mass_kwh_per_k=thermal_mass,
        solar_factor=solar_factor,
        internal_gain_kw=internal_kw,
        cop_scale=cop_scale,
        r_squared=r_squared,
        sample_count=len(samples),
        heating_share=heating_share,
        plausible=plausible,
    )


# --- Phase 2: COP curve from measured heat --------------------------------

MIN_COP_SAMPLES = 20
COP_SCALE_BOUNDS = (0.5, 1.5)


@dataclass(frozen=True)
class CopSample:
    """One steady heat pump operating point with measured heat."""

    outdoor_temp: float
    supply_temp: float
    defrost_factor: float
    cop: float  # measured thermal / electrical

    def as_list(self) -> list[float]:
        return [self.outdoor_temp, self.supply_temp, self.defrost_factor, self.cop]


@dataclass(frozen=True)
class CopFit:
    """Learned COP curve (compensation factor folded in, i.e. 1.0)."""

    base_cop: float
    outdoor_coefficient: float
    k_factor: float
    sample_count: int
    r_squared: float
    mean_abs_error: float

    @property
    def usable(self) -> bool:
        return (
            self.sample_count >= MIN_COP_SAMPLES
            and 1.0 <= self.base_cop <= 9.0
            and 0.0 <= self.k_factor <= 0.4
            and -0.05 <= self.outdoor_coefficient <= 0.3
        )


def fit_cop(
    samples: list[CopSample], prior: tuple[float, float, float]
) -> CopFit | None:
    """Fit ``COP/defrost = b + a·T_out − k·(T_sup − 35)``, ridge to the prior.

    ``prior`` is (base COP, outdoor coefficient, k-factor) with the configured
    compensation factor already applied. A narrow spread of outdoor or supply
    temperatures leaves the corresponding slope at its prior.
    """
    if len(samples) < 3:
        return None
    scales = [max(prior[0], 1.0), max(abs(prior[1]), 0.02), max(prior[2], 0.02)]
    prior_x = [prior[0] / scales[0], prior[1] / scales[1], prior[2] / scales[2]]
    rows = [
        [scales[0], s.outdoor_temp * scales[1], -(s.supply_temp - 35.0) * scales[2]]
        for s in samples
    ]
    ys = [s.cop / max(s.defrost_factor, 0.1) for s in samples]
    x = _ridge(rows, ys, prior_x)
    if x is None:
        return None
    predicted = [sum(r[i] * x[i] for i in range(3)) for r in rows]
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predicted, strict=True))
    return CopFit(
        base_cop=x[0] * scales[0],
        outdoor_coefficient=x[1] * scales[1],
        k_factor=x[2] * scales[2],
        sample_count=len(samples),
        r_squared=1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0,
        mean_abs_error=sum(abs(y - p) for y, p in zip(ys, predicted, strict=True))
        / len(ys),
    )


# --- Phase 3: emitter curve --------------------------------------------------

MIN_EMITTER_SAMPLES = 20
EMITTER_EXPONENT_BOUNDS = (0.9, 1.6)


@dataclass(frozen=True)
class EmitterSample:
    """Delivered heat at one supply-minus-indoor temperature difference."""

    delta_t: float  # supply - indoor, K
    heat_kw: float

    def as_list(self) -> list[float]:
        return [self.delta_t, self.heat_kw]


@dataclass(frozen=True)
class EmitterFit:
    """Learned emitter curve: Q = nominal · (ΔT / nominal_delta_t) ^ exponent."""

    nominal_power_kw: float
    nominal_delta_t: float
    exponent: float
    sample_count: int
    r_squared: float

    @property
    def usable(self) -> bool:
        return (
            self.sample_count >= MIN_EMITTER_SAMPLES
            and EMITTER_EXPONENT_BOUNDS[0]
            <= self.exponent
            <= EMITTER_EXPONENT_BOUNDS[1]
            and self.nominal_power_kw > 0
            and self.r_squared >= MIN_R_SQUARED
        )


def fit_emitter(
    samples: list[EmitterSample],
    *,
    prior_nominal_kw: float,
    prior_exponent: float,
    nominal_delta_t: float,
) -> EmitterFit | None:
    """Fit ``ln Q = ln Q_n + n · ln(ΔT/ΔT_n)``, ridge to the prior."""
    usable = [s for s in samples if s.delta_t > 1.0 and s.heat_kw > 0.05]
    if len(usable) < 3 or prior_nominal_kw <= 0:
        return None
    prior_x = [math.log(prior_nominal_kw), prior_exponent]
    rows = [[1.0, math.log(s.delta_t / nominal_delta_t)] for s in usable]
    ys = [math.log(s.heat_kw) for s in usable]
    x = _ridge(rows, ys, prior_x)
    if x is None:
        return None
    predicted = [x[0] + x[1] * r[1] for r in rows]
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predicted, strict=True))
    return EmitterFit(
        nominal_power_kw=math.exp(x[0]),
        nominal_delta_t=nominal_delta_t,
        exponent=x[1],
        sample_count=len(usable),
        r_squared=1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0,
    )


# --- Phase 4: does a single thermal mass describe the building? --------------

MIN_RESIDUAL_SAMPLES = 48
TWO_MASS_AUTOCORRELATION = 0.6


def residual_diagnosis(errors: list[float]) -> dict[str, Any]:
    """Lag-1 autocorrelation of the 1-hour-ahead prediction errors.

    A 1R1C model that fits well leaves errors that look like noise. Strongly
    correlated errors (the model is wrong in the same direction hour after
    hour, typically too slow right after heating starts and too fast later)
    point at a second, faster thermal mass - e.g. room air and furniture on
    top of a heavy screed.
    """
    if len(errors) < MIN_RESIDUAL_SAMPLES:
        return {"residual_autocorrelation": None, "two_mass_suspected": None}
    mean = sum(errors) / len(errors)
    centred = [e - mean for e in errors]
    denom = sum(c * c for c in centred)
    if denom <= 0:
        return {"residual_autocorrelation": 0.0, "two_mass_suspected": False}
    autocorr = sum(a * b for a, b in itertools.pairwise(centred)) / denom
    return {
        "residual_autocorrelation": round(autocorr, 2),
        "two_mass_suspected": autocorr >= TWO_MASS_AUTOCORRELATION,
    }


def fit_passes_quality_gates(fit: FitResult | None) -> bool:
    """Whether a fit is good enough to be used by the optimizer."""
    return (
        fit is not None
        and fit.plausible
        and fit.sample_count >= MIN_SAMPLES_TO_APPLY
        and fit.r_squared >= MIN_R_SQUARED
        and MIN_SHARE_EACH_DIRECTION
        <= fit.heating_share
        <= 1.0 - MIN_SHARE_EACH_DIRECTION
    )


@dataclass
class ThermalCalibrationState:
    """Samples, fits and exclusion statistics for one zone, persisted."""

    store: storage.Store[dict[str, Any]]
    samples: deque[Sample] = field(
        default_factory=lambda: deque(maxlen=CALIBRATION_WINDOW)
    )
    cop_samples: deque[CopSample] = field(
        default_factory=lambda: deque(maxlen=CALIBRATION_WINDOW)
    )
    emitter_samples: deque[EmitterSample] = field(
        default_factory=lambda: deque(maxlen=CALIBRATION_WINDOW)
    )
    fit: FitResult | None = None
    cop_fit: CopFit | None = None
    emitter_fit: EmitterFit | None = None
    last_result: str = RESULT_NO_RESULT
    exclusions: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(EXCLUSION_RESULTS, 0)
    )

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def ready(self) -> bool:
        """The building fit passes every quality gate."""
        return fit_passes_quality_gates(self.fit)

    def record_exclusion(self, reason: str) -> None:
        """Count a discarded observation window."""
        self.last_result = reason
        if reason in self.exclusions:
            self.exclusions[reason] += 1

    def record_sample(self, sample: Sample, prior: Prior) -> FitResult | None:
        """Add one building observation and refit."""
        self.samples.append(sample)
        fit = fit_building(list(self.samples), prior)
        if fit is None:
            self.last_result = RESULT_SINGULAR
            return None
        self.fit = fit
        self.last_result = RESULT_FITTED if fit.plausible else RESULT_IMPLAUSIBLE
        return fit

    def record_cop_sample(
        self, sample: CopSample, prior: tuple[float, float, float]
    ) -> None:
        """Add one measured COP point and refit the COP curve."""
        self.cop_samples.append(sample)
        self.cop_fit = fit_cop(list(self.cop_samples), prior)

    def record_emitter_sample(
        self,
        sample: EmitterSample,
        *,
        prior_nominal_kw: float,
        prior_exponent: float,
        nominal_delta_t: float,
    ) -> None:
        """Add one emitter operating point and refit the emitter curve."""
        self.emitter_samples.append(sample)
        self.emitter_fit = fit_emitter(
            list(self.emitter_samples),
            prior_nominal_kw=prior_nominal_kw,
            prior_exponent=prior_exponent,
            nominal_delta_t=nominal_delta_t,
        )

    def refit(
        self,
        prior: Prior,
        *,
        cop_prior: tuple[float, float, float] | None = None,
        emitter_prior: tuple[float, float, float] | None = None,
    ) -> None:
        """Refit stored samples against (possibly changed) priors."""
        self.fit = fit_building(list(self.samples), prior) if self.samples else None
        if cop_prior is not None and self.cop_samples:
            self.cop_fit = fit_cop(list(self.cop_samples), cop_prior)
        if emitter_prior is not None and self.emitter_samples:
            nominal_kw, exponent, nominal_delta_t = emitter_prior
            self.emitter_fit = fit_emitter(
                list(self.emitter_samples),
                prior_nominal_kw=nominal_kw,
                prior_exponent=exponent,
                nominal_delta_t=nominal_delta_t,
            )

    async def async_load(self) -> None:
        """Restore persisted samples and exclusion counters."""
        stored = await self.store.async_load()
        if not stored:
            return
        self.samples = deque(
            (Sample(*values) for values in stored.get("samples", [])),
            maxlen=CALIBRATION_WINDOW,
        )
        self.cop_samples = deque(
            (CopSample(*values) for values in stored.get("cop_samples", [])),
            maxlen=CALIBRATION_WINDOW,
        )
        self.emitter_samples = deque(
            (EmitterSample(*values) for values in stored.get("emitter_samples", [])),
            maxlen=CALIBRATION_WINDOW,
        )
        for key, value in (stored.get("exclusions") or {}).items():
            if key in self.exclusions:
                self.exclusions[key] = int(value)

    async def async_save(self) -> None:
        """Persist samples and exclusion counters."""
        await self.store.async_save(
            {
                "samples": [s.as_list() for s in self.samples],
                "cop_samples": [s.as_list() for s in self.cop_samples],
                "emitter_samples": [s.as_list() for s in self.emitter_samples],
                "exclusions": dict(self.exclusions),
            }
        )

    async def async_reset(self) -> None:
        """Clear all samples and fits."""
        _LOGGER.info("Resetting thermal calibration (%d samples)", self.sample_count)
        self.samples.clear()
        self.cop_samples.clear()
        self.emitter_samples.clear()
        self.fit = None
        self.cop_fit = None
        self.emitter_fit = None
        self.last_result = RESULT_NO_RESULT
        self.exclusions = dict.fromkeys(EXCLUSION_RESULTS, 0)
        await self.async_save()


def effective_energy_label(
    ua_w_per_k: float, area_m2: float, ventilation_type: str, ceiling_height: float
) -> str:
    """The energy label whose estimated heat loss is closest to ``ua_w_per_k``."""
    best_label = ENERGY_LABELS[0]
    best_error = math.inf
    for label in ENERGY_LABELS:
        estimate = calculate_htc_from_energy_label(
            label,
            area_m2,
            ventilation_type=ventilation_type,
            ceiling_height=ceiling_height,
        )
        error = abs(math.log(estimate / ua_w_per_k)) if estimate > 0 else math.inf
        if error < best_error:
            best_label, best_error = label, error
    return best_label
