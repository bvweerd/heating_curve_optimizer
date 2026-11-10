"""Tests for the gas boiler's heat in the building model (gas_boiler_heat.py)."""

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.gas_boiler_heat import (
    MIN_LEARNED_HOURS,
    SOURCE_CONFIGURED,
    SOURCE_LEARNED,
    GasBoilerOutputLearner,
    gas_boiler_heat_kw,
    heating_signal_on,
)

T0 = datetime(2026, 11, 10, 14, 0, tzinfo=UTC)


def test_heating_signal_accepts_binary_switch_and_climate(hass: HomeAssistant):
    hass.states.async_set("binary_sensor.cv", "on")
    hass.states.async_set("switch.cv", "off")
    hass.states.async_set("climate.room", "heat", {"hvac_action": "heating"})
    hass.states.async_set("climate.idle", "heat", {"hvac_action": "idle"})
    assert heating_signal_on(hass, "binary_sensor.cv") is True
    assert heating_signal_on(hass, "switch.cv") is False
    assert heating_signal_on(hass, "climate.room") is True
    assert heating_signal_on(hass, "climate.idle") is False
    assert heating_signal_on(hass, "binary_sensor.missing") is False
    assert heating_signal_on(hass, None) is False


def test_heat_power_sensor_wins_over_the_signal(hass: HomeAssistant):
    hass.states.async_set("sensor.boiler_heat", "4200", {"unit_of_measurement": "W"})
    hass.states.async_set("binary_sensor.cv", "off")
    kw = gas_boiler_heat_kw(
        hass,
        power_sensor="sensor.boiler_heat",
        heating_sensor="binary_sensor.cv",
        dhw_on=False,
        output_kw=6.0,
    )
    assert kw == 4.2
    hass.states.async_set("sensor.boiler_heat", "unavailable")
    kw = gas_boiler_heat_kw(
        hass,
        power_sensor="sensor.boiler_heat",
        heating_sensor="binary_sensor.cv",
        dhw_on=False,
        output_kw=6.0,
    )
    assert kw == 0.0  # a broken sensor is not replaced by the estimate


def test_signal_times_output_but_not_during_tap_water(hass: HomeAssistant):
    hass.states.async_set("binary_sensor.cv", "on")

    def heat(dhw_on: bool) -> float:
        return gas_boiler_heat_kw(
            hass,
            power_sensor=None,
            heating_sensor="binary_sensor.cv",
            dhw_on=dhw_on,
            output_kw=5.5,
        )

    assert heat(False) == 5.5
    assert heat(True) == 0.0
    assert (
        gas_boiler_heat_kw(
            hass, power_sensor=None, heating_sensor=None, dhw_on=False, output_kw=5.5
        )
        == 0.0
    )


def _run(learner, minutes, *, heating, dhw=False, counter, start):
    """Tick once per minute; the meter reports `counter` at the last tick."""
    changed = False
    for m in range(minutes):
        now = start + timedelta(minutes=m + 1)
        reading = counter if m == minutes - 1 else None
        changed |= learner.tick(now, heating=heating, dhw=dhw, counter_kwh=reading)
    return changed, start + timedelta(minutes=minutes)


def test_learns_output_from_meter_steps_and_heating_time():
    learner = GasBoilerOutputLearner()
    learner.tick(T0, heating=False, dhw=False, counter_kwh=1000.0)
    now = T0
    # Three hourly meter steps, 30 min of heating each, 3 kWh heat: 6 kW.
    counter = 1000.0
    for _ in range(3):
        _, now = _run(learner, 30, heating=True, counter=None, start=now)
        counter += 3.0
        changed, now = _run(learner, 30, heating=False, counter=counter, start=now)
        assert changed
    assert abs(learner.learned_kw - 6.0) < 1e-6
    assert abs(learner.learned_hours - 1.5) < 1e-6
    output, source = learner.output_kw(9.0)
    assert source == SOURCE_CONFIGURED  # not yet enough hours
    _, now = _run(learner, 60, heating=True, counter=counter + 6.0, start=now)
    assert learner.learned_hours >= MIN_LEARNED_HOURS
    output, source = learner.output_kw(9.0)
    assert source == SOURCE_LEARNED
    assert abs(output - 6.0) < 1e-6


def test_skips_steps_with_tap_water_or_too_little_heating():
    learner = GasBoilerOutputLearner()
    learner.tick(T0, heating=False, dhw=False, counter_kwh=1000.0)
    # Gas used while tap water ran: not a heating sample.
    changed, now = _run(learner, 60, heating=True, dhw=True, counter=1004.0, start=T0)
    assert not changed and learner.learned_kw is None
    # Gas used with only 5 min of heating: too short to trust.
    changed, now = _run(learner, 5, heating=True, counter=None, start=now)
    changed, now = _run(learner, 55, heating=False, counter=1006.0, start=now)
    assert not changed and learner.learned_kw is None
    # Gas used without heating at all (cooking): skipped.
    changed, now = _run(learner, 60, heating=False, counter=1006.5, start=now)
    assert not changed and learner.learned_kw is None


def test_implausible_rate_and_meter_reset_are_ignored():
    learner = GasBoilerOutputLearner()
    learner.tick(T0, heating=False, dhw=False, counter_kwh=1000.0)
    changed, now = _run(learner, 30, heating=True, counter=1100.0, start=T0)
    assert not changed and learner.learned_kw is None  # 200 kW
    changed, now = _run(learner, 30, heating=True, counter=5.0, start=now)
    assert not changed  # reset: new interval


def test_learned_output_survives_a_restart():
    learner = GasBoilerOutputLearner(learned_kw=5.8, learned_hours=12.0)
    restored = GasBoilerOutputLearner.from_dict(learner.as_dict())
    assert restored.learned_kw == 5.8 and restored.learned_hours == 12.0
    assert GasBoilerOutputLearner.from_dict(None).learned_kw is None
