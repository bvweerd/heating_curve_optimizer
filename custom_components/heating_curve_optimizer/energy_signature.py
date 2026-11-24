"""Energy signature: the building's heat loss from daily heat balances.

Short calibration windows fail on slow buildings: with floor heating most of
a window's heat goes into the screed and reaches the air hours later, so the
air temperature in the window says little about the heat put in. Over a
whole day what the mass stores is small next to what flows through it, so
the daily balance

    heat (kWh) = UA x (indoor - outdoor) x 24 h
                 - internal gains x 24 h - solar factor x window gain (kWh)
                 + C x (indoor at the end - indoor at the start)

holds well. Fitting it over days with different outdoor temperatures gives
UA, the internal gains and the solar factor - the classic energy signature
of a building. The thermal mass C is taken from the prior (only its product
with a small daily air change enters). The fit is pulled towards the prior
(the label model), so a few days cannot produce a wild result, and it is
only used once it passes the gates below. The dynamics (how fast the air
follows the mass) stay with the mass model.

Hours with an exclusion (open window, fireplace, tap water) are left out of
the balance, and after a long one also the hours in which its heat is still
in the air. A day counts with what is left when that is enough; it weighs
less, because the storage term is larger next to fewer hours. Days with too
little coverage are not used.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

STORAGE_VERSION = 1

MIN_COVERAGE_HOURS = 22.0
MIN_CLEAN_HOURS = 12.0
# An exclusion this long leaves heat (or cold) in the air that the storage
# term does not describe; this long afterwards is left out as well.
LONG_EXCLUSION_HOURS = 0.5
SETTLE_HOURS = 2.0
MAX_TICK_HOURS = 0.1  # a longer step is a gap (restart, missed ticks)
MAX_DAYS = 60
MIN_DAYS = 10
MIN_DELTA_T_SPREAD = 1.5  # K, standard deviation of the daily indoor-outdoor
MIN_R_SQUARED = 0.5
# Prior standard deviations: how far the data must pull to move a parameter.
PRIOR_UA_REL_SD = 0.5
PRIOR_INTERNAL_SD_KW = 0.2
PRIOR_SOLAR_SD = 0.5
# Expected scatter of a day's heat around the model, kWh.
DAY_SD_KWH = 4.0

REJECT_COVERAGE = "coverage"
REJECT_EXCLUDED = "excluded"


@dataclass
class DaySample:
    """One day's balance over its clean hours, scaled to 24 h."""

    day: str
    delta_t: float  # mean indoor - outdoor, K
    heat_kwh: float  # heat pump + gas boiler heat
    solar_kwh: float  # modelled window gain before the solar factor
    indoor_change: float  # indoor change over the clean hours, K per 24 h
    hours: float = 24.0  # clean hours behind it


@dataclass
class SignatureFit:
    ua_w_per_k: float
    internal_gain_kw: float
    solar_factor: float
    r_squared: float
    days: int
    delta_t_spread: float
    mean_abs_error_kwh: float
    usable: bool
    reason: str | None


@dataclass(frozen=True)
class SignaturePrior:
    ua_w_per_k: float
    internal_gain_kw: float
    solar_factor: float
    thermal_mass_kwh_per_k: float
    area_m2: float


@dataclass
class _Day:
    day: str
    hours: float = 0.0
    excluded_hours: float = 0.0
    heat_kwh: float = 0.0
    delta_t_h: float = 0.0
    solar_kwh: float = 0.0
    indoor_start: float | None = None
    indoor_last: float | None = None
    indoor_change: float = 0.0  # over the clean steps


@dataclass
class EnergySignature:
    """Collects daily balances and fits the signature over them."""

    days: deque[DaySample] = field(default_factory=lambda: deque(maxlen=MAX_DAYS))
    rejected: dict[str, int] = field(
        default_factory=lambda: {REJECT_COVERAGE: 0, REJECT_EXCLUDED: 0}
    )
    fit: SignatureFit | None = None
    _current: _Day | None = None
    _last_tick: datetime | None = None
    _last_indoor: float | None = None
    _exclusion_hours: float = 0.0  # length of the exclusion going on
    _settle_hours: float = 0.0  # still to leave out after a long one

    def tick(
        self,
        now: datetime,
        local_day: date,
        *,
        heat_kw: float,
        indoor: float,
        outdoor: float,
        solar_kw: float,
        excluded: bool,
    ) -> DaySample | None:
        """Integrate one step; at a day change close the previous day and
        return it when it is usable."""
        day = local_day.isoformat()
        closed: DaySample | None = None
        if self._current is not None and self._current.day != day:
            closed = self._close(self._current)
            self._current = None
        if self._current is None:
            self._current = _Day(day=day, indoor_start=indoor)
        current = self._current
        last, self._last_tick = self._last_tick, now
        last_indoor, self._last_indoor = self._last_indoor, indoor
        if last is not None:
            hours = (now - last).total_seconds() / 3600.0
            if 0 < hours <= MAX_TICK_HOURS:
                current.hours += hours
                if self._leave_out(excluded, hours):
                    current.excluded_hours += hours
                else:
                    current.heat_kwh += heat_kw * hours
                    current.delta_t_h += (indoor - outdoor) * hours
                    current.solar_kwh += max(solar_kw, 0.0) * hours
                    if last_indoor is not None:
                        current.indoor_change += indoor - last_indoor
        current.indoor_last = indoor
        return closed

    def _leave_out(self, excluded: bool, hours: float) -> bool:
        """Whether this step is excluded or settling after an exclusion."""
        if excluded:
            self._exclusion_hours += hours
            if self._exclusion_hours >= LONG_EXCLUSION_HOURS:
                self._settle_hours = SETTLE_HOURS
            return True
        self._exclusion_hours = 0.0
        if self._settle_hours > 0:
            self._settle_hours -= hours
            return True
        return False

    def _close(self, day: _Day) -> DaySample | None:
        if day.hours < MIN_COVERAGE_HOURS:
            self.rejected[REJECT_COVERAGE] += 1
            return None
        clean = day.hours - day.excluded_hours
        if clean < MIN_CLEAN_HOURS:
            self.rejected[REJECT_EXCLUDED] += 1
            return None
        scale = 24.0 / clean
        sample = DaySample(
            day=day.day,
            delta_t=day.delta_t_h / clean,
            heat_kwh=day.heat_kwh * scale,
            solar_kwh=day.solar_kwh * scale,
            indoor_change=day.indoor_change * scale,
            hours=clean,
        )
        self.days.append(sample)
        return sample

    def refit(self, prior: SignaturePrior) -> SignatureFit | None:
        self.fit = fit_signature(list(self.days), prior)
        return self.fit

    # Storage -------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": [asdict(d) for d in self.days],
            "rejected": dict(self.rejected),
            "current": asdict(self._current) if self._current else None,
            "exclusion_hours": self._exclusion_hours,
            "settle_hours": self._settle_hours,
            "last_tick": self._last_tick.isoformat() if self._last_tick else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EnergySignature:
        state = cls()
        if not data:
            return state
        for row in data.get("days", []):
            state.days.append(DaySample(**row))
        state.rejected.update(data.get("rejected") or {})
        # A day in progress from before `indoor_change` was summed is dropped.
        current = data.get("current")
        if current and "indoor_change" in current:
            state._current = _Day(**current)
        state._exclusion_hours = float(data.get("exclusion_hours", 0.0))
        state._settle_hours = float(data.get("settle_hours", 0.0))
        if last := data.get("last_tick"):
            state._last_tick = datetime.fromisoformat(last)
        return state


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Solve a small linear system by Gaussian elimination with pivoting."""
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
                a[r] = [x - factor * y for x, y in zip(a[r], a[col], strict=True)]
    return [a[i][n] / a[i][i] for i in range(n)]


def fit_signature(days: list[DaySample], prior: SignaturePrior) -> SignatureFit | None:
    """Fit UA, internal gains and solar factor to the daily balances, pulled
    towards the prior. None without days."""
    if not days:
        return None
    # y = heat - C x indoor change = 24 dT x UA - 24 x internal - solar x f
    rows = [[24.0 * d.delta_t, -24.0, -d.solar_kwh] for d in days]
    ys = [d.heat_kwh - prior.thermal_mass_kwh_per_k * d.indoor_change for d in days]
    prior_theta = [
        prior.ua_w_per_k / 1000.0,
        prior.internal_gain_kw,
        prior.solar_factor,
    ]
    prior_sd = [
        max(PRIOR_UA_REL_SD * prior.ua_w_per_k / 1000.0, 1e-3),
        PRIOR_INTERNAL_SD_KW,
        PRIOR_SOLAR_SD,
    ]
    # A part day, scaled to 24 h, has the scatter scaled along.
    ws = [(d.hours / 24.0 / DAY_SD_KWH) ** 2 for d in days]
    matrix = [
        [
            sum(w * r[i] * r[j] for w, r in zip(ws, rows, strict=True))
            + (1.0 / prior_sd[i] ** 2 if i == j else 0)
            for j in range(3)
        ]
        for i in range(3)
    ]
    vector = [
        sum(w * r[i] * y for w, r, y in zip(ws, rows, ys, strict=True))
        + prior_theta[i] / prior_sd[i] ** 2
        for i in range(3)
    ]
    theta = _solve(matrix, vector)
    if theta is None:
        return None
    ua_kw, internal_kw, solar = theta
    predicted = [sum(r[i] * theta[i] for i in range(3)) for r in rows]
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predicted, strict=True))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mean_dt = sum(d.delta_t for d in days) / len(days)
    spread = (sum((d.delta_t - mean_dt) ** 2 for d in days) / len(days)) ** 0.5
    mae = sum(abs(y - p) for y, p in zip(ys, predicted, strict=True)) / len(ys)

    reason: str | None = None
    if len(days) < MIN_DAYS:
        reason = "too_few_days"
    elif spread < MIN_DELTA_T_SPREAD:
        reason = "too_little_temperature_spread"
    elif r_squared < MIN_R_SQUARED:
        reason = "poor_fit"
    elif not 0.3 * prior.ua_w_per_k <= ua_kw * 1000.0 <= 3.0 * prior.ua_w_per_k:
        reason = "implausible_heat_loss"
    elif not 0.0 <= internal_kw <= 12.0 * max(prior.area_m2, 1.0) / 1000.0:
        reason = "implausible_internal_gain"
    elif not 0.0 <= solar <= 2.5:
        reason = "implausible_solar_factor"
    return SignatureFit(
        ua_w_per_k=ua_kw * 1000.0,
        internal_gain_kw=internal_kw,
        solar_factor=solar,
        r_squared=r_squared,
        days=len(days),
        delta_t_spread=spread,
        mean_abs_error_kwh=mae,
        usable=reason is None,
        reason=reason,
    )
