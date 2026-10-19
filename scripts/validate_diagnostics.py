"""Validate the models against a diagnostics download.

Usage (in the development environment, Home Assistant installed):

    python scripts/validate_diagnostics.py config_entry-heating_curve_optimizer-....json

Reads `stored_data.validation` and reports, per zone:

- the 6-hour-ahead air prediction error of the one-node model against the
  mass-air model at every coupling of the learning grid (the backtest);
- the measured COP (heat / electrical power) against the configured COP
  model at the same supply and outdoor temperature;
- run and pause durations of the published on/off advice;
- how often the PV-surplus adjustment changed.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import itertools

from custom_components.heating_curve_optimizer.building_model import (
    BuildingConfig,
)
from custom_components.heating_curve_optimizer.heatpump_model import (
    HeatPumpConfig,
)
from custom_components.heating_curve_optimizer.mass_model import (
    AIR_COUPLING_GRID,
    HistorySample,
    _replay,
)


def _building(stored: dict[str, Any]) -> BuildingConfig:
    """The one-node building the integration used (from the plan data)."""
    optimization = stored.get("optimization", {})
    building = BuildingConfig(
        area_m2=1.0,
        comfort_min=float(optimization.get("comfort_min", 19.5)),
        comfort_max=float(optimization.get("comfort_max", 20.5)),
        internal_gain_kw=float(optimization.get("internal_gain_kw", 0.0)),
        solar_factor=float(optimization.get("solar_factor", 1.0)),
    )
    building.ua_w_per_k = float(optimization["building_ua_w_per_k"])
    building.thermal_mass_kwh_per_k = float(
        optimization["building_thermal_mass_kwh_per_k"]
    )
    return building


def _durations(changes: list[list[Any]]) -> dict[str, list[float]]:
    """Minutes spent in each on/off state between logged changes."""
    spans: dict[str, list[float]] = {"on": [], "off": []}
    for (start, state), (end, _next) in itertools.pairwise(changes):
        minutes = (
            datetime.fromisoformat(end) - datetime.fromisoformat(start)
        ).total_seconds() / 60
        spans["on" if state else "off"].append(round(minutes))
    return spans


def report_zone(name: str, data: dict[str, Any], stored: dict[str, Any]) -> None:
    print(f"== {name}")
    history = [HistorySample(*row) for row in data.get("history", [])]
    print(f"history: {len(history)} samples ({len(history) * 5 / 60:.1f} h)")
    if len(history) > 100:
        building = _building(stored)
        print(f"one node        MAE 6h = {_replay(history, building, 0.0):.3f} K")
        for coupling in AIR_COUPLING_GRID:
            mae = _replay(history, building, coupling)
            print(f"mass+air H={coupling:5.1f} MAE 6h = {mae:.3f} K")

        config = stored.get("config", {})
        heatpump = HeatPumpConfig.from_config(config)
        ratios = [
            (s.heat_kw / s.power_kw)
            / heatpump.cop_at(supply_temp=s.supply, outdoor_temp=s.outdoor)
            for s in history
            if s.power_kw and s.power_kw > 0.3 and s.supply and s.heat_kw > 0
        ]
        if ratios:
            print(
                f"measured / modelled COP: median {statistics.median(ratios):.2f} "
                f"over {len(ratios)} running samples"
            )

    spans = _durations(data.get("plan_advice_changes", []))
    for state, minutes in spans.items():
        if minutes:
            print(
                f"advice {state}: {len(minutes)} spans, shortest {min(minutes)} min, "
                f"median {statistics.median(minutes)} min"
            )
    adjustments = data.get("realtime_adjustments", [])
    if len(adjustments) > 1:
        hours = (
            datetime.fromisoformat(adjustments[-1][0])
            - datetime.fromisoformat(adjustments[0][0])
        ).total_seconds() / 3600
        print(
            f"PV adjustment changes: {len(adjustments)} in {hours:.1f} h "
            f"({len(adjustments) / max(hours, 1e-9):.2f}/h)"
        )


def main(path: str) -> None:
    diagnostics = json.loads(Path(path).read_text(encoding="utf-8"))
    stored = diagnostics["data"]["stored_data"]
    for name, data in stored.get("validation", {}).items():
        report_zone(name, data, stored)


if __name__ == "__main__":
    main(sys.argv[1])
