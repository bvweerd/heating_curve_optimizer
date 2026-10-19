"""Tests for the mass-air building model (building_model + mass_model)."""

from __future__ import annotations

import copy
import math
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.heating_curve_optimizer.building_model import (
    BuildingConfig,
    EmitterConfig,
)
from custom_components.heating_curve_optimizer.heatpump_model import HeatPumpConfig
from custom_components.heating_curve_optimizer.mass_model import (
    MIN_HISTORY_SAMPLES,
    HistorySample,
    fit_air_coupling,
)
from custom_components.heating_curve_optimizer.thermal_optimizer import (
    optimize_thermal_schedule,
)

from .conftest import make_entry, setup_entry


def _building(coupling: float = 0.0) -> BuildingConfig:
    return BuildingConfig(
        area_m2=159,
        energy_label="A+",
        thermal_mass_class="heavy",
        comfort_min=19.7,
        comfort_max=20.7,
        target_temp=20.0,
        internal_gain_kw=0.35,
        air_coupling_kw_per_k=coupling,
    )


def test_air_and_mass_temperature_are_inverse():
    building = _building(8.0)
    mass = building.mass_temp_for_air(20.0, outdoor_temp=5.0, solar_gain_kw=2.0)
    air = building.air_temp(mass, outdoor_temp=5.0, solar_gain_kw=2.0)
    assert air == pytest.approx(20.0)
    # Sun warms the air above the mass; without it the air is below.
    assert building.air_temp(20.0, outdoor_temp=5.0, solar_gain_kw=4.0) > 20.0
    assert building.air_temp(20.0, outdoor_temp=5.0, solar_gain_kw=0.0) < 20.0


def test_one_node_is_the_special_case():
    one = _building(0.0)
    assert one.air_temp(20.3, outdoor_temp=0.0) == 20.3
    kwargs = {"outdoor_temp": 2.0, "heat_input_kw": 3.0, "solar_gain_kw": 1.0}
    assert one.next_mass_temp(20.0, step_hours=0.25, **kwargs) == pytest.approx(
        one.next_indoor_temp(20.0, step_hours=0.25, **kwargs)
    )
    # A very stiff coupling converges to the one-node model too.
    stiff = _building(1e6)
    assert stiff.next_mass_temp(20.0, step_hours=0.25, **kwargs) == pytest.approx(
        one.next_indoor_temp(20.0, step_hours=0.25, **kwargs), abs=1e-4
    )


def _synthetic_history(
    coupling: float, days: int = 5, truth: BuildingConfig | None = None
) -> list[HistorySample]:
    """5-minute history of a house that truly has this coupling: two heating
    runs a day, sunny middays, a daily outdoor swing."""
    if truth is None:
        truth = _building()
    truth.air_coupling_kw_per_k = coupling
    step = 5 / 60
    mass = 20.0
    samples = []
    for i in range(days * 288):
        hour = (i * 5 / 60) % 24
        outdoor = 6.0 + 4.0 * math.sin((hour - 9) / 24 * 2 * math.pi)
        solar = (
            max(0.0, 4.0 * math.sin((hour - 7) / 10 * math.pi))
            if 7 < hour < 17
            else 0.0
        )
        heat = 6.0 if (2 <= hour < 5 or 19 <= hour < 21) else 0.0
        air = truth.air_temp(mass, outdoor_temp=outdoor, solar_gain_kw=solar)
        samples.append(HistorySample(air, outdoor, solar, heat))
        mass = truth.next_mass_temp(
            mass,
            outdoor_temp=outdoor,
            heat_input_kw=heat,
            solar_gain_kw=solar,
            step_hours=step,
        )
    return samples


def test_learning_finds_the_coupling_of_a_mass_air_house():
    fit = fit_air_coupling(_synthetic_history(8.0), _building())
    assert fit is not None
    assert fit.coupling_kw_per_k == 8.0
    assert fit.accepted
    assert fit.mae_k < fit.one_node_mae_k


def test_learning_rejects_a_one_node_house_and_short_history():
    # A stiff coupling is a one-node house: no clear gain, not accepted.
    fit = fit_air_coupling(_synthetic_history(1e6), _building())
    assert fit is not None and not fit.accepted
    short = _synthetic_history(8.0)[: MIN_HISTORY_SAMPLES - 1]
    assert fit_air_coupling(short, _building()) is None


def test_optimizer_judges_comfort_on_the_air():
    """Sun warms the air above a cool mass: the plan reports the air as
    indoor temperature and the mass separately."""
    building = _building(8.0)
    emitter = EmitterConfig.sized_to_building(
        building,
        design_outdoor_temp=-10.0,
        design_supply_temp=33.0,
        emitter_type="underfloor",
    )
    result = optimize_thermal_schedule(
        building=building,
        heatpump=HeatPumpConfig(max_thermal_power_kw=9.0),
        emitter=emitter,
        outdoor_temps=[8.0] * 12,
        prices=[0.30] * 12,
        solar_gain_kw=[4.0] * 12,
        initial_indoor_temp=20.2,
        water_min=27.0,
        water_max=33.0,
        outdoor_min=-10.0,
        outdoor_max=18.0,
        time_base=60,
    )
    assert len(result.mass_temps) == len(result.indoor_temps) == 12
    assert all(
        air > mass
        for air, mass in zip(result.indoor_temps, result.mass_temps, strict=True)
    )


async def test_zone_with_mass_model_observes_and_learns(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry(zone_overrides={"building_model": "mass_air"})
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    data = coordinator.data
    summary = data["mass_model"]
    assert summary["source"] == "default"
    assert summary["air_coupling_kw_per_k"] == pytest.approx(150 * 55 / 1000)
    assert data["mass_temp"] is not None
    assert len(data["mass_temps"]) == len(data["indoor_temps"])

    # Learn from a synthetic history of a mass-air house.
    mass = coordinator._mass
    assert mass is not None
    building = coordinator.building_config(
        coordinator.heat_coordinator.effective_config()
    )
    mass.history.extend(_synthetic_history(8.0, truth=copy.copy(building)))
    await coordinator._learn_air_coupling(dt_util.utcnow(), building)
    await coordinator.async_refresh()
    summary = coordinator.data["mass_model"]
    assert summary["learned_accepted"] is True
    assert summary["source"] == "learned"

    # The observer advances with the delivered heat between ticks.
    start = mass.mass_temp
    coordinator._update_mass(dt_util.utcnow() + timedelta(minutes=5))
    assert mass.mass_temp is not None and start is not None


async def test_one_node_zone_does_not_use_the_mass_model(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    data = entry.runtime_data.optimization_coordinator.data
    # Learned in the background, but not used.
    assert data["mass_model"]["enabled"] is False
    assert data["mass_model"]["source"] is None
    assert data["mass_temps"] == data["indoor_temps"]


class _FakeStore:
    def __init__(self, data=None):
        self.data = data
        self.saved = None

    async def async_load(self):
        return self.data

    def async_delay_save(self, data_func, delay):
        self.saved = data_func()


async def test_history_and_fit_survive_a_restart():
    from custom_components.heating_curve_optimizer.mass_model import (
        CouplingFit,
        MassModelState,
    )

    store = _FakeStore()
    state = MassModelState(store)  # type: ignore[arg-type]
    building = _building(8.0)
    start = dt_util.utcnow()
    for minute in range(11):
        state.update(
            start + timedelta(minutes=minute),
            building,
            air=20.0,
            outdoor=5.0,
            solar_kw=0.0,
            heat_kw=8.0,
        )
    # The 5-minute buckets closed: averaged samples, saved.
    assert len(state.history) == 2
    assert state.history[0].heat_kw == 8.0
    # More heat than the loss raises the mass above what holds the air at 20 °C.
    assert state.mass_temp is not None
    assert state.mass_temp > building.mass_temp_for_air(20.0, outdoor_temp=5.0)
    state.set_fit(start, CouplingFit(8.0, 0.1, 0.2, 900, True))

    restored = MassModelState(_FakeStore(store.saved))  # type: ignore[arg-type]
    await restored.async_load()
    assert len(restored.history) == 2
    assert restored.fit == CouplingFit(8.0, 0.1, 0.2, 900, True)
    assert restored.fitted_at == start


async def test_diagnostics_carry_the_validation_history(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    """A diagnostics download holds what is needed to validate offline: the
    5-minute history (also in one-node mode), the on/off advice changes and
    the PV-adjustment changes."""
    from custom_components.heating_curve_optimizer.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    start = dt_util.utcnow()
    for minute in range(11):
        coordinator._update_mass(start + timedelta(minutes=minute))

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    validation = diagnostics["stored_data"]["validation"]["primary_zone"]
    assert validation["history_columns"][:4] == [
        "air",
        "outdoor",
        "solar_kw",
        "heat_kw",
    ]
    assert len(validation["history"]) == 2
    assert len(validation["history"][0]) == 6
    assert validation["history_end"] is not None
    # The first published advice is logged.
    assert validation["plan_advice_changes"]
    assert validation["realtime_adjustments"] == []


def test_measured_cop_ratio_flags_a_scaled_heat_measurement():
    from custom_components.heating_curve_optimizer.mass_model import (
        measured_cop_ratio,
    )

    heatpump = HeatPumpConfig()
    cop = heatpump.cop_at(supply_temp=30.0, outdoor_temp=8.0)
    samples = [
        HistorySample(20.0, 8.0, 0.0, 1.5 * cop * 1.0, 1.0, 30.0) for _ in range(30)
    ]
    assert measured_cop_ratio(samples, heatpump) == pytest.approx(1.5)
    # Idle samples and too few running samples give no verdict.
    idle = [HistorySample(20.0, 8.0, 0.0, 0.0, 0.01, None) for _ in range(30)]
    assert measured_cop_ratio(idle + samples[:5], heatpump) is None


async def test_cop_model_far_from_measured_raises_a_repair_issue(
    hass: HomeAssistant, mock_open_meteo, price_state
) -> None:
    from homeassistant.helpers import issue_registry as ir

    from custom_components.heating_curve_optimizer.const import DOMAIN

    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.optimization_coordinator
    issue = (DOMAIN, f"cop_model_mismatch_{entry.entry_id}")

    coordinator._update_cop_model_issue(1.54)
    assert issue in ir.async_get(hass).issues
    coordinator._update_cop_model_issue(1.05)
    assert issue not in ir.async_get(hass).issues
