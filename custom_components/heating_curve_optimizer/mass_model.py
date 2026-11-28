"""Mass-air building model: observer for the mass temperature and learning
of the mass-air coupling.

With the mass-air model (see `BuildingConfig.air_temp`) the optimizer's
state is the temperature of the thermal mass the heat pump heats (floor,
structure), which no sensor measures. `MassObserver` estimates it by
integrating the delivered heat against the measured room air:

    C dT_mass/dt = Q_heat_pump - H (T_mass - T_air,measured)

The same observer, replayed over a few days of 5-minute history, learns
the coupling H together with the solar factor and a scale on the heat
loss UA: for each candidate it predicts the air temperature 6 hours ahead
from every hour and keeps the one with the smallest error, compared with
the one-node model. The label-based UA is a rule of thumb; without the
scale a house that loses less heat than its label says needs less heat
than the plan expects, and the plan over-heats or over-pays.

Identified on a heavy house with underfloor heating: the heat pump heats
the mass, sun and internal gains warm the air directly, H around 55 W/K
per m² floor. A one-node model there stores the sun's heat in the whole
mass and predicts too warm hours later.
"""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.helpers import storage
from homeassistant.util import dt as dt_util

from .building_model import BuildingConfig
from .delayed_save import PeriodicSave
from .heatpump_model import HeatPumpConfig

# Default coupling per m² floor area when nothing is learned or set.
DEFAULT_AIR_COUPLING_W_PER_M2_K = 55.0
# Candidate couplings (kW/K) for the learning grid search.
AIR_COUPLING_GRID = (2.0, 3.0, 4.0, 5.0, 6.5, 8.0, 10.0, 13.0, 17.0, 22.0, 30.0)
# Multipliers on the building's solar factor searched with the coupling:
# low sun is shaded more than the window model knows.
SOLAR_FACTOR_GRID = (0.3, 0.45, 0.6, 0.75, 0.9, 1.0, 1.2)
# Multipliers on the label-based heat loss UA, searched with the coupling.
UA_SCALE_GRID = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3, 1.5)
HISTORY_INTERVAL = timedelta(minutes=5)
HISTORY_LENGTH = int(timedelta(days=14) / HISTORY_INTERVAL)
# Learning needs a few days, some heating and a clear gain over one node.
MIN_HISTORY_SAMPLES = int(timedelta(days=3) / HISTORY_INTERVAL)
MIN_HEATING_SHARE = 0.02
MIN_IMPROVEMENT = 0.85  # learned MAE must be below 85 % of the one-node MAE
PREDICTION_HOURS = 6
# A gap longer than this restarts the observer from the measured air.
MAX_OBSERVER_GAP = timedelta(minutes=30)
STORAGE_VERSION = 1
SAVE_INTERVAL = 1800  # s, between writes of the history


@dataclass
class HistorySample:
    """5-minute averages: air and outdoor (°C), solar, heat and electrical
    power (kW), supply temperature (°C, None when unknown).

    The power and supply fields are not used for learning; they are kept so
    a diagnostics download can be replayed offline (measured COP, backtest).
    `excluded`: an exclusion sensor (open window, fireplace, tap water) was
    on during the sample - heat the model does not see moved the air, so
    learning skips it.
    """

    air: float
    outdoor: float
    solar_kw: float
    heat_kw: float
    power_kw: float | None = None
    supply: float | None = None
    excluded: bool = False


@dataclass
class CouplingFit:
    """Result of the coupling grid search (errors in K, 6 h ahead)."""

    coupling_kw_per_k: float
    mae_k: float
    one_node_mae_k: float
    samples: int
    accepted: bool
    # Solar factor (absolute) learned with the coupling; the sun reaches the
    # air directly in this model, so an over-estimated window gain shows.
    # None for a fit stored before it was learned.
    solar_factor: float | None = None
    # Multiplier on the label-based UA learned with them (None: not learned).
    ua_scale: float | None = None


def _replay(
    samples: list[HistorySample], building: BuildingConfig, coupling: float
) -> float:
    """Mean absolute 6-hour-ahead air error of a coupling over the history.

    `coupling` 0 replays the one-node model. The observer runs over the
    whole history on the measured air; every hour a prediction starts from
    its estimate, driven by the measured heat, outdoor and solar.
    """
    model = BuildingConfig(
        area_m2=building.area_m2,
        comfort_min=building.comfort_min,
        comfort_max=building.comfort_max,
        internal_gain_kw=building.internal_gain_kw,
        solar_factor=building.solar_factor,
        air_coupling_kw_per_k=coupling,
    )
    model.ua_w_per_k = building.ua_w_per_k
    model.thermal_mass_kwh_per_k = building.thermal_mass_kwh_per_k
    step = HISTORY_INTERVAL.total_seconds() / 3600
    horizon = int(timedelta(hours=PREDICTION_HOURS) / HISTORY_INTERVAL)
    hourly = int(timedelta(hours=1) / HISTORY_INTERVAL)

    first = samples[0]
    mass = model.mass_temp_for_air(
        first.air,
        outdoor_temp=first.outdoor,
        solar_gain_kw=first.solar_kw * model.solar_factor,
    )
    observed: list[float] = []
    for sample in samples:
        observed.append(mass)
        mass = _observe(model, mass, sample.air, sample.heat_kw, step)

    errors: list[float] = []
    for start in _prediction_starts(samples, horizon, hourly):
        mass = observed[start]
        for i in range(start, start + horizon):
            sample = samples[i]
            mass = model.next_mass_temp(
                mass,
                outdoor_temp=sample.outdoor,
                heat_input_kw=sample.heat_kw,
                solar_gain_kw=sample.solar_kw * model.solar_factor,
                step_hours=step,
            )
        end = samples[start + horizon]
        predicted = model.air_temp(
            mass,
            outdoor_temp=end.outdoor,
            solar_gain_kw=end.solar_kw * model.solar_factor,
        )
        errors.append(abs(predicted - end.air))
    return sum(errors) / len(errors) if errors else float("inf")


def _prediction_starts(
    samples: list[HistorySample], horizon: int, hourly: int
) -> list[int]:
    """Hourly starts of the prediction windows with no excluded sample: a
    window through a fireplace evening or an open window would score the
    model on heat it cannot see. The observer still runs through them."""
    excluded = [s.excluded for s in samples]
    return [
        start
        for start in range(0, len(samples) - horizon, hourly)
        if not any(excluded[start : start + horizon + 1])
    ]


def _observe(
    building: BuildingConfig, mass: float, air: float, heat_kw: float, hours: float
) -> float:
    """One observer step: the mass integrates the heat against the air."""
    coupling = building.air_coupling_kw_per_k
    if coupling <= 0 or building.thermal_mass_kwh_per_k <= 0:
        return air
    return (
        mass
        + (heat_kw - coupling * (mass - air)) * hours / building.thermal_mass_kwh_per_k
    )


def fit_air_coupling(
    samples: list[HistorySample],
    building: BuildingConfig,
    ua_scales: tuple[float, ...] = UA_SCALE_GRID,
) -> CouplingFit | None:
    """Grid search for the coupling and solar factor that predict the air
    best, 6 h ahead, against the one-node model with its own best solar
    factor.

    None without enough history or heating to judge; `accepted` when the
    best coupling clearly beats the one-node model. `ua_scales`: the
    multipliers on the building's UA to search; (1.0,) keeps a UA known from
    elsewhere (the energy signature).
    """
    usable = [s for s in samples if not s.excluded]
    if len(usable) < MIN_HISTORY_SAMPLES:
        return None
    heating = sum(1 for s in usable if s.heat_kw > 0.1) / len(usable)
    if heating < MIN_HEATING_SHARE:
        return None
    horizon = int(timedelta(hours=PREDICTION_HOURS) / HISTORY_INTERVAL)
    hourly = int(timedelta(hours=1) / HISTORY_INTERVAL)
    if not _prediction_starts(samples, horizon, hourly):
        return None

    def candidate(solar: float, ua: float) -> BuildingConfig:
        model = copy.copy(building)
        model.solar_factor = building.solar_factor * solar
        model.ua_w_per_k = building.ua_w_per_k * ua
        return model

    def search(coupling: float | None) -> tuple[float, float, float, float]:
        """(mae, coupling, solar multiplier, UA scale) over the full grid.

        The coupling trades off against both the UA scale and the sun (it
        sets how fast the air follows the outdoor temperature and the sun):
        searched one or two at a time, the search stops in a wrong corner
        when the label UA is off."""
        couplings = AIR_COUPLING_GRID if coupling is None else (coupling,)
        mae, h, m, ua = min(
            (_replay(samples, candidate(sun, scale), c), c, sun, scale)
            for c in couplings
            for sun in SOLAR_FACTOR_GRID
            for scale in ua_scales
        )
        return mae, h, m, ua

    one_node, _h, _m, _ua = search(0.0)
    best_mae, best_coupling, best_solar, best_ua = search(None)
    return CouplingFit(
        coupling_kw_per_k=float(best_coupling),
        mae_k=round(float(best_mae), 4),
        one_node_mae_k=round(float(one_node), 4),
        samples=len(samples),
        accepted=bool(best_mae <= MIN_IMPROVEMENT * one_node),
        solar_factor=round(float(building.solar_factor * best_solar), 3),
        ua_scale=float(best_ua),
    )


def _averages(
    rows: list[tuple[float, float, float, float, float | None, float | None]],
) -> list[float | None]:
    """Column means; a column with missing values averages what it has."""
    means: list[float | None] = []
    for column in zip(*rows, strict=True):
        values = [v for v in column if v is not None]
        means.append(sum(values) / len(values) if values else None)
    return means


# A measured COP this far from the COP model means either the heat
# measurement is off (check it first) or the COP model needs calibrating.
COP_RATIO_PLAUSIBLE = (0.7, 1.4)
MIN_COP_RATIO_SAMPLES = 24  # two hours of running


def measured_cop_ratio(
    samples: list[HistorySample],
    heatpump: HeatPumpConfig,
    min_running_power_kw: float = 0.3,
) -> float | None:
    """Median of measured COP / modelled COP over the running samples.

    None without enough running samples with heat, power and supply
    temperature.
    """
    ratios = sorted(
        (s.heat_kw / s.power_kw)
        / heatpump.cop_at(supply_temp=s.supply, outdoor_temp=s.outdoor)
        for s in samples
        if not s.excluded
        and s.power_kw is not None
        and s.power_kw >= min_running_power_kw
        and s.supply is not None
        and s.heat_kw > 0
    )
    if len(ratios) < MIN_COP_RATIO_SAMPLES:
        return None
    middle = len(ratios) // 2
    if len(ratios) % 2:
        return ratios[middle]
    return (ratios[middle - 1] + ratios[middle]) / 2


class MassModelState:
    """Observer state, 5-minute history and learned coupling of one zone."""

    def __init__(self, store: storage.Store[dict[str, Any]]) -> None:
        """Initialize from an (unloaded) store."""
        self._store = store
        self._save = PeriodicSave(store, SAVE_INTERVAL)
        self._loaded = False
        self.mass_temp: float | None = None
        self._mass_time: datetime | None = None
        self.history: deque[HistorySample] = deque(maxlen=HISTORY_LENGTH)
        self._bucket: list[
            tuple[float, float, float, float, float | None, float | None]
        ] = []
        self._bucket_start: datetime | None = None
        self._bucket_excluded = False
        self.last_bucket_time: datetime | None = None
        self.fit: CouplingFit | None = None
        self.fitted_at: datetime | None = None

    async def async_load(self) -> None:
        """Restore the history and the last fit."""
        data = await self._store.async_load()
        self._loaded = True
        if not data:
            return
        for row in data.get("history", []):
            # Rows stored before `excluded` existed have six fields.
            sample = HistorySample(*row)
            sample.excluded = bool(sample.excluded)
            self.history.append(sample)
        if fit := data.get("fit"):
            self.fit = CouplingFit(**fit)
        if fitted_at := data.get("fitted_at"):
            self.fitted_at = dt_util.parse_datetime(fitted_at)

    async def async_flush(self) -> None:
        """Write the state now (unload); not before it was loaded."""
        if not self._loaded:
            return
        await self._save.flush(self._data())

    async def async_reset(self) -> None:
        """Forget the history and the learned fit; the observer keeps its
        mass temperature (it follows the measured air)."""
        self.history.clear()
        self._bucket = []
        self._bucket_start = None
        self._bucket_excluded = False
        self.fit = None
        self.fitted_at = None
        await self.async_flush()

    def history_rows(self) -> list[list[float | None]]:
        """The history as compact rows (oldest first), for storage and
        diagnostics: air, outdoor, solar_kw, heat_kw, power_kw, supply,
        excluded (0/1)."""
        return [
            [
                round(s.air, 3),
                round(s.outdoor, 2),
                round(s.solar_kw, 3),
                round(s.heat_kw, 3),
                None if s.power_kw is None else round(s.power_kw, 3),
                None if s.supply is None else round(s.supply, 2),
                1 if s.excluded else 0,
            ]
            for s in self.history
        ]

    def _data(self) -> dict[str, Any]:
        return {
            "history": self.history_rows(),
            "fit": vars(self.fit) if self.fit else None,
            "fitted_at": self.fitted_at.isoformat() if self.fitted_at else None,
        }

    def update(
        self,
        now: datetime,
        building: BuildingConfig,
        *,
        air: float,
        outdoor: float,
        solar_kw: float,
        heat_kw: float,
        power_kw: float | None = None,
        supply: float | None = None,
        excluded: bool = False,
    ) -> None:
        """Advance the observer to `now` and collect the history sample.

        `solar_kw` is the modelled window gain before the building's solar
        factor (the factor is applied when it is used).
        """
        solar = solar_kw * building.solar_factor
        if (
            self.mass_temp is None
            or self._mass_time is None
            or now - self._mass_time > MAX_OBSERVER_GAP
        ):
            self.mass_temp = building.mass_temp_for_air(
                air, outdoor_temp=outdoor, solar_gain_kw=solar
            )
        else:
            hours = (now - self._mass_time).total_seconds() / 3600
            self.mass_temp = _observe(building, self.mass_temp, air, heat_kw, hours)
        self._mass_time = now

        if self._bucket_start is None:
            self._bucket_start = now
        self._bucket.append((air, outdoor, solar_kw, heat_kw, power_kw, supply))
        self._bucket_excluded |= excluded
        if now - self._bucket_start >= HISTORY_INTERVAL:
            air_m, outdoor_m, solar_m, heat_m, power_m, supply_m = _averages(
                self._bucket
            )
            self.history.append(
                HistorySample(
                    float(air_m or 0.0),
                    float(outdoor_m or 0.0),
                    float(solar_m or 0.0),
                    float(heat_m or 0.0),
                    power_m,
                    supply_m,
                    self._bucket_excluded,
                )
            )
            self._bucket = []
            self._bucket_excluded = False
            self._bucket_start = now
            self.last_bucket_time = now
            self._save.request(now, self._data)

    def set_fit(self, now: datetime, fit: CouplingFit | None) -> None:
        """Store a fit computed (in the executor) by `fit_air_coupling`."""
        self.fit = fit
        self.fitted_at = now
        self._save.request_soon(now, self._data)
