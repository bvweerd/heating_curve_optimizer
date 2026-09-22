"""Tests for the 1R1C building physics model."""

import math

import pytest

from custom_components.heating_curve_optimizer.building_model import (
    BuildingConfig,
    EmitterConfig,
)


def test_ua_scales_with_energy_label():
    """A worse energy label must produce a higher UA (more heat loss) for the
    same floor area - the whole point of the label."""
    good = BuildingConfig(area_m2=150, energy_label="A")
    bad = BuildingConfig(area_m2=150, energy_label="G")
    assert bad.ua_w_per_k > good.ua_w_per_k


def test_thermal_mass_scales_with_area_and_class():
    """Thermal mass must scale with floor area and with construction weight."""
    small = BuildingConfig(area_m2=80, energy_label="C")
    large = BuildingConfig(area_m2=200, energy_label="C")
    assert large.thermal_mass_kwh_per_k > small.thermal_mass_kwh_per_k

    light = BuildingConfig(area_m2=150, energy_label="C", thermal_mass_class="light")
    heavy = BuildingConfig(area_m2=150, energy_label="C", thermal_mass_class="heavy")
    assert heavy.thermal_mass_kwh_per_k > light.thermal_mass_kwh_per_k


def test_time_constant_is_positive_and_finite_for_normal_building():
    """tau = C/UA should land in a physically plausible range (hours, not
    seconds or years) for a normal Dutch home."""
    building = BuildingConfig(area_m2=150, energy_label="C")
    assert 5.0 < building.time_constant_hours < 200.0


def test_heat_loss_is_zero_at_equal_temperatures():
    building = BuildingConfig(area_m2=150, energy_label="C")
    assert building.heat_loss_kw(20.0, 20.0) == 0.0


def test_heat_loss_scales_linearly_with_delta_t():
    building = BuildingConfig(area_m2=150, energy_label="C")
    loss_10 = building.heat_loss_kw(20.0, 10.0)
    loss_20 = building.heat_loss_kw(20.0, 0.0)
    assert math.isclose(loss_20, 2 * loss_10, rel_tol=1e-9)


def test_next_indoor_temp_conserves_energy():
    """The core property this whole redesign exists to guarantee (REDESIGN.md
    §2.1.A): the temperature change over a step must exactly match the net
    energy in divided by thermal mass. No offset-derived side-channel."""
    building = BuildingConfig(area_m2=150, energy_label="C")
    t_in = 20.0
    heat_in = 3.0
    solar = 0.5
    outdoor = 5.0
    step_hours = 1.0

    next_t = building.next_indoor_temp(
        t_in,
        outdoor_temp=outdoor,
        heat_input_kw=heat_in,
        solar_gain_kw=solar,
        step_hours=step_hours,
    )

    loss = building.heat_loss_kw(t_in, outdoor)
    expected_delta = (
        (heat_in + solar - loss) * step_hours / building.thermal_mass_kwh_per_k
    )
    assert math.isclose(next_t - t_in, expected_delta, rel_tol=1e-9)


def test_next_indoor_temp_drifts_toward_outdoor_with_no_heating():
    """With zero heat input the building must cool when outdoor is colder,
    never spontaneously warm up (no free energy)."""
    building = BuildingConfig(area_m2=150, energy_label="C")
    next_t = building.next_indoor_temp(
        20.0, outdoor_temp=0.0, heat_input_kw=0.0, step_hours=1.0
    )
    assert next_t < 20.0


def test_next_indoor_temp_energy_conserved_over_multi_step_random_sequence():
    """Property test over a longer random-ish sequence: cumulative energy in
    must equal cumulative temperature change * thermal mass, step by step."""
    building = BuildingConfig(area_m2=120, energy_label="D", thermal_mass_class="heavy")
    t_in = 19.5
    outdoor_series = [2.0, -3.0, 1.0, 6.0, -1.0, 4.0, 0.0, -5.0]
    heat_series = [2.5, 4.0, 0.0, 1.0, 3.5, 0.5, 2.0, 5.0]
    solar_series = [0.0, 0.0, 1.2, 2.0, 0.0, 0.8, 0.0, 0.0]
    step_hours = 0.5

    for outdoor, heat, solar in zip(outdoor_series, heat_series, solar_series):
        loss = building.heat_loss_kw(t_in, outdoor)
        expected_delta = (
            (heat + solar - loss) * step_hours / building.thermal_mass_kwh_per_k
        )
        next_t = building.next_indoor_temp(
            t_in,
            outdoor_temp=outdoor,
            heat_input_kw=heat,
            solar_gain_kw=solar,
            step_hours=step_hours,
        )
        assert math.isclose(next_t - t_in, expected_delta, rel_tol=1e-9)
        t_in = next_t


def test_discretize_states_spans_comfort_band_plus_margin():
    building = BuildingConfig(
        area_m2=150, energy_label="C", comfort_min=19.0, comfort_max=21.0
    )
    states = building.discretize_states(resolution=0.1, margin=1.5)
    assert states[0] == pytest.approx(17.5)
    assert states[-1] == pytest.approx(22.5)
    assert len(states) > 1


def test_discretize_states_resolution_controls_count():
    building = BuildingConfig(
        area_m2=150, energy_label="C", comfort_min=19.0, comfort_max=21.0
    )
    coarse = building.discretize_states(resolution=0.5, margin=1.0)
    fine = building.discretize_states(resolution=0.1, margin=1.0)
    assert len(fine) > len(coarse)


def test_from_config_derives_comfort_band_from_existing_keys():
    """No new config keys should be required: target temp + hysteresis
    (already present for the number entities) must be enough."""
    config = {
        "area_m2": 150,
        "energy_label": "B",
        "target_indoor_temp": 20.0,
        "indoor_temp_hysteresis_lower": 0.5,
        "indoor_temp_hysteresis_upper": 0.3,
    }
    building = BuildingConfig.from_config(config)
    assert building.comfort_min == pytest.approx(19.5)
    assert building.comfort_max == pytest.approx(20.3)


def test_emitter_available_power_zero_below_indoor_temp():
    """A supply temperature at or below the room can deliver no heat."""
    emitter = EmitterConfig(nominal_delta_t=25.0, nominal_power_kw=6.0)
    assert emitter.available_power_kw(supply_temp=20.0, indoor_temp=20.0) == 0.0
    assert emitter.available_power_kw(supply_temp=18.0, indoor_temp=20.0) == 0.0


def test_emitter_available_power_matches_nominal_at_design_point():
    emitter = EmitterConfig(exponent=1.3, nominal_delta_t=25.0, nominal_power_kw=6.0)
    available = emitter.available_power_kw(supply_temp=45.0, indoor_temp=20.0)
    assert available == pytest.approx(6.0, rel=1e-6)


def test_emitter_available_power_increases_with_supply_temp():
    """More offset (higher supply temp) must deliver more power - this is the
    coupling the legacy model lacked entirely (REDESIGN.md §2.1.A/H)."""
    emitter = EmitterConfig.sized_to_building(
        BuildingConfig(area_m2=150, energy_label="C"),
        design_outdoor_temp=-10.0,
        design_supply_temp=45.0,
    )
    low = emitter.available_power_kw(supply_temp=35.0, indoor_temp=20.0)
    high = emitter.available_power_kw(supply_temp=45.0, indoor_temp=20.0)
    assert high > low


def test_emitter_sized_to_building_covers_peak_loss_at_design_point():
    """By construction, the sized emitter's nominal output must equal the
    building's heat loss at the design outdoor temperature."""
    building = BuildingConfig(area_m2=150, energy_label="C", comfort_max=20.5)
    emitter = EmitterConfig.sized_to_building(
        building, design_outdoor_temp=-10.0, design_supply_temp=45.0
    )
    expected = building.heat_loss_kw(building.comfort_max, -10.0)
    assert emitter.nominal_power_kw == pytest.approx(expected, rel=1e-6)


def test_underfloor_emitter_flatter_than_radiator():
    """Underfloor heating's lower exponent means less power is gained per
    degree of extra supply temperature than a radiator, at supply
    temperatures below the design point."""
    building = BuildingConfig(area_m2=150, energy_label="C")
    radiator = EmitterConfig.sized_to_building(
        building,
        design_outdoor_temp=-10.0,
        design_supply_temp=45.0,
        emitter_type="radiator",
    )
    underfloor = EmitterConfig.sized_to_building(
        building,
        design_outdoor_temp=-10.0,
        design_supply_temp=45.0,
        emitter_type="underfloor",
    )
    # Below the (shared) design point, the flatter underfloor curve
    # delivers relatively more of its nominal power than the radiator does.
    rad_fraction = (
        radiator.available_power_kw(supply_temp=35.0, indoor_temp=20.0)
        / radiator.nominal_power_kw
    )
    floor_fraction = (
        underfloor.available_power_kw(supply_temp=35.0, indoor_temp=20.0)
        / underfloor.nominal_power_kw
    )
    assert floor_fraction > rad_fraction
