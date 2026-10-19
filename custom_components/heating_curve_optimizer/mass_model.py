"""Mass-air building model: observer for the mass temperature and learning
of the mass-air coupling.

With the mass-air model (see `BuildingConfig.air_temp`) the optimizer's
state is the temperature of the thermal mass the heat pump heats (floor,
structure), which no sensor measures. `MassObserver` estimates it by
integrating the delivered heat against the measured room air:

    C dT_mass/dt = Q_heat_pump - H (T_mass - T_air,measured)

The same observer, replayed over a few days of 5-minute history, learns
the coupling H: for each candidate H it predicts the air temperature 1 and
6 hours ahead from every hour and keeps the H with the smallest error -
but only when that beats the one-node model clearly.

Identified on a heavy house with underfloor heating: the heat pump heats
the mass, sun and internal gains warm the air directly, H around 55 W/K
per m² floor. A one-node model there stores the sun's heat in the whole
mass and predicts too warm hours later.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.helpers import storage
from homeassistant.util import dt as dt_util

from .building_model import BuildingConfig
from .heatpump_model import HeatPumpConfig

# Default coupling per m² floor area when nothing is learned or set.
DEFAULT_AIR_COUPLING_W_PER_M2_K = 55.0
# Candidate couplings (kW/K) for the learning grid search.
AIR_COUPLING_GRID = (2.0, 3.0, 4.0, 5.0, 6.5, 8.0, 10.0, 13.0, 17.0, 22.0, 30.0)
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


@dataclass
class HistorySample:
    """5-minute averages: air and outdoor (°C), solar, heat and electrical
    power (kW), supply temperature (°C, None when unknown).

    The power and supply fields are not used for learning; they are kept so
    a diagnostics download can be replayed offline (measured COP, backtest).
    """

    air: float
    outdoor: float
    solar_kw: float
    heat_kw: float
    power_kw: float | None = None
    supply: float | None = None


@dataclass
class CouplingFit:
    """Result of the coupling grid search (errors in K, 6 h ahead)."""

    coupling_kw_per_k: float
    mae_k: float
    one_node_mae_k: float
    samples: int
    accepted: bool


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
    for start in range(0, len(samples) - horizon, hourly):
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
    samples: list[HistorySample], building: BuildingConfig
) -> CouplingFit | None:
    """Grid search for the coupling that predicts the air best, 6 h ahead.

    None without enough history or heating to judge; `accepted` only when
    the best coupling clearly beats the one-node model.
    """
    if len(samples) < MIN_HISTORY_SAMPLES:
        return None
    heating = sum(1 for s in samples if s.heat_kw > 0.1) / len(samples)
    if heating < MIN_HEATING_SHARE:
        return None
    one_node = _replay(samples, building, 0.0)
    best_coupling, best_mae = min(
        ((h, _replay(samples, building, h)) for h in AIR_COUPLING_GRID),
        key=lambda item: item[1],
    )
    return CouplingFit(
        coupling_kw_per_k=float(best_coupling),
        mae_k=round(float(best_mae), 4),
        one_node_mae_k=round(float(one_node), 4),
        samples=len(samples),
        accepted=bool(best_mae <= MIN_IMPROVEMENT * one_node),
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
        if s.power_kw is not None
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
        self.mass_temp: float | None = None
        self._mass_time: datetime | None = None
        self.history: deque[HistorySample] = deque(maxlen=HISTORY_LENGTH)
        self._bucket: list[
            tuple[float, float, float, float, float | None, float | None]
        ] = []
        self._bucket_start: datetime | None = None
        self.last_bucket_time: datetime | None = None
        self.fit: CouplingFit | None = None
        self.fitted_at: datetime | None = None

    async def async_load(self) -> None:
        """Restore the history and the last fit."""
        data = await self._store.async_load()
        if not data:
            return
        for row in data.get("history", []):
            self.history.append(HistorySample(*row))
        if fit := data.get("fit"):
            self.fit = CouplingFit(**fit)
        if fitted_at := data.get("fitted_at"):
            self.fitted_at = dt_util.parse_datetime(fitted_at)

    def history_rows(self) -> list[list[float | None]]:
        """The history as compact rows (oldest first), for storage and
        diagnostics: air, outdoor, solar_kw, heat_kw, power_kw, supply."""
        return [
            [
                round(s.air, 3),
                round(s.outdoor, 2),
                round(s.solar_kw, 3),
                round(s.heat_kw, 3),
                None if s.power_kw is None else round(s.power_kw, 3),
                None if s.supply is None else round(s.supply, 2),
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
                )
            )
            self._bucket = []
            self._bucket_start = now
            self.last_bucket_time = now
            self._store.async_delay_save(self._data, 600)

    def set_fit(self, now: datetime, fit: CouplingFit | None) -> None:
        """Store a fit computed (in the executor) by `fit_air_coupling`."""
        self.fit = fit
        self.fitted_at = now
        self._store.async_delay_save(self._data, 10)
