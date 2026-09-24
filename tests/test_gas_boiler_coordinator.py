"""Tests for GasBoilerCoordinator (gas_boiler_coordinator.py)."""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.heating_curve_optimizer.const import DOMAIN
from custom_components.heating_curve_optimizer.gas_boiler_coordinator import (
    GasBoilerCoordinator,
)

BASE_CONFIG = {
    "gas_price_sensor": "sensor.gas_price",
    "consumption_price_sensor": "sensor.elec_price",
    "gas_boiler_efficiency": 0.9,
    "gas_calorific_value_kwh_per_m3": 9.77,
    "base_cop": 4.2,
    "k_factor": 0.11,
    "outdoor_temp_coefficient": 0.025,
    "cop_compensation_factor": 1.0,
}


def _make_coordinator(
    hass: HomeAssistant,
    *,
    config: dict | None = None,
    heat_data: dict | None = None,
    optimization_data: dict | None = None,
    entry_id: str = "test_entry",
) -> GasBoilerCoordinator:
    heat_coordinator = MagicMock()
    heat_coordinator.data = (
        heat_data
        if heat_data is not None
        else {
            "outdoor_temperature": -5.0,
            "heat_pump_on": True,
            "net_heat_loss": 1.0,
        }
    )
    optimization_coordinator = MagicMock()
    optimization_coordinator.data = (
        optimization_data
        if optimization_data is not None
        else {
            "supply_temps": [40.0],
            "heat_pump_actively_running": True,
        }
    )
    return GasBoilerCoordinator(
        hass,
        None,
        heat_coordinator,
        optimization_coordinator,
        config if config is not None else dict(BASE_CONFIG),
        entry_id,
    )


@pytest.mark.asyncio
async def test_computes_correct_comparison(hass: HomeAssistant):
    hass.states.async_set("sensor.gas_price", "1.20")
    hass.states.async_set("sensor.elec_price", "0.35")
    coordinator = _make_coordinator(hass)

    data = await coordinator._async_update_data()

    assert data["available"] is True
    assert data["gas_price_eur_per_m3"] == pytest.approx(1.20)
    assert data["electricity_price_eur_per_kwh"] == pytest.approx(0.35)
    expected_gas_cost = (1.20 / 9.77) / 0.9
    assert data["gas_cost_eur_per_kwh"] == pytest.approx(expected_gas_cost)
    assert data["heat_pump_cost_eur_per_kwh"] == pytest.approx(
        0.35 / data["heat_pump_cop"]
    )


@pytest.mark.asyncio
async def test_gas_price_ignores_forecast_attribute_uses_state(hass: HomeAssistant):
    """Regression test: the gas price must always come from state.state,
    never from a forecast_prices[0] the sensor happens to also carry -
    forecast[0] is the price at the start of the forecast window (e.g.
    midnight), not "now", and this feature is documented as instantaneous-
    only (module docstring, docs/reference/configuration.md)."""
    hass.states.async_set(
        "sensor.gas_price",
        "1.20",
        {"forecast_prices": [0.10, 0.20, 0.30]},
    )
    hass.states.async_set("sensor.elec_price", "0.35")
    coordinator = _make_coordinator(hass)

    data = await coordinator._async_update_data()

    assert data["gas_price_eur_per_m3"] == pytest.approx(1.20)


COMFORTABLE_HEAT = {
    "outdoor_temperature": -5.0,
    "indoor_temperature": 20.0,
    "indoor_temperature_source": "sensor",
    "lower_bound": 19.7,
}
COMFORTABLE_PLAN = {
    "supply_temps": [40.0],
    "comfort_min": 19.7,
    "indoor_temps": [20.0, 19.9, 19.8, 19.8],
    "step_durations_hours": [1.0, 1.0, 1.0, 1.0],
}


@pytest.mark.parametrize(
    ("heat", "plan", "gas_price", "expected", "reason"),
    [
        # Comfortable, gas cheaper: stay on the heat pump.
        ({}, {}, "0.10", False, None),
        # House already below the comfort band, gas cheaper: gas.
        ({"indoor_temperature": 19.3}, {}, "0.10", True, "below_comfort_band"),
        # Plan predicts the heat pump cannot keep up, gas cheaper: gas.
        (
            {},
            {"indoor_temps": [19.8, 19.6, 19.4, 19.2]},
            "0.10",
            True,
            "heat_pump_cannot_keep_up",
        ),
        # Below the band, plan recovers, heat pump cheaper: heat pump.
        ({"indoor_temperature": 19.3}, {}, "3.00", False, "below_comfort_band"),
        # Heat pump cannot restore comfort: gas as backup even when dearer.
        (
            {},
            {"indoor_temps": [19.8, 19.6, 19.4, 19.2]},
            "3.00",
            True,
            "heat_pump_cannot_keep_up",
        ),
        # Temporary dip the heat pump recovers from, gas dearer: heat pump.
        (
            {},
            {"indoor_temps": [19.5, 19.8, 19.9, 20.0]},
            "3.00",
            False,
            "below_comfort_band",
        ),
        # A predicted dip beyond the lookahead does not count yet.
        (
            {},
            {"indoor_temps": [19.8, 19.8, 19.8, 19.0]},
            "0.10",
            False,
            None,
        ),
        # Without a measured indoor temperature only the plan counts.
        (
            {
                "indoor_temperature": 19.0,
                "indoor_temperature_source": "target_fallback",
            },
            {},
            "0.10",
            False,
            None,
        ),
    ],
)
async def test_heat_pump_first_policy(
    hass: HomeAssistant, heat, plan, gas_price, expected, reason
):
    hass.states.async_set("sensor.gas_price", gas_price)
    hass.states.async_set("sensor.elec_price", "0.40")
    coordinator = _make_coordinator(
        hass,
        heat_data={**COMFORTABLE_HEAT, **heat},
        optimization_data={**COMFORTABLE_PLAN, **plan},
    )

    data = await coordinator._async_update_data()

    assert data["prefer_gas_boiler"] is expected
    assert data["comfort_reason"] == reason


@pytest.mark.asyncio
async def test_gas_price_sensor_unavailable_creates_and_clears_repair_issue(
    hass: HomeAssistant,
):
    hass.states.async_set("sensor.elec_price", "0.35")
    coordinator = _make_coordinator(hass, entry_id="issue_entry")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    issue_id = "gas_price_sensor_unavailable_issue_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    hass.states.async_set("sensor.gas_price", "1.20")
    await coordinator._async_update_data()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.asyncio
async def test_electricity_price_unavailable_does_not_create_duplicate_issue(
    hass: HomeAssistant,
):
    """The main OptimizationCoordinator already raises its own repair
    issue for this same sensor - a second one here would be redundant."""
    hass.states.async_set("sensor.gas_price", "1.20")
    coordinator = _make_coordinator(hass, entry_id="issue_entry_2")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    # No gas-boiler-specific issue for the electricity sensor.
    for issue in ir.async_get(hass).issues.values():
        assert "elec_price" not in issue.issue_id


@pytest.mark.asyncio
async def test_missing_operating_point_self_heals(hass: HomeAssistant):
    """Other coordinators not yet refreshed -> soft UpdateFailed, no
    repair issue, must succeed once they publish real data."""
    hass.states.async_set("sensor.gas_price", "1.20")
    hass.states.async_set("sensor.elec_price", "0.35")
    coordinator = _make_coordinator(
        hass, heat_data={}, optimization_data={}, entry_id="self_heal_entry"
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, "gas_price_sensor_unavailable_self_heal_entry"
        )
        is None
    )

    coordinator.heat_coordinator.data = {
        "outdoor_temperature": -5.0,
        "heat_pump_on": True,
        "net_heat_loss": 1.0,
    }
    coordinator.optimization_coordinator.data = {
        "supply_temps": [40.0],
        "heat_pump_actively_running": True,
    }
    data = await coordinator._async_update_data()
    assert data["available"] is True


@pytest.mark.asyncio
async def test_async_setup_tracks_both_price_sensors(hass: HomeAssistant):
    coordinator = _make_coordinator(hass)
    await coordinator.async_setup()
    assert coordinator._unsub is not None
    coordinator._unsub()


@pytest.mark.asyncio
async def test_async_shutdown_unsubscribes(hass: HomeAssistant):
    coordinator = _make_coordinator(hass)
    await coordinator.async_setup()
    await coordinator.async_shutdown()
    assert coordinator._unsub is None


async def test_comfort_backup_can_be_switched_off(hass: HomeAssistant):
    """With the comfort backup off, a heat pump that cannot keep up only
    hands over to gas when gas is also cheaper."""
    hass.states.async_set("sensor.gas_price", "3.00")
    hass.states.async_set("sensor.elec_price", "0.40")
    coordinator = _make_coordinator(
        hass,
        config={**BASE_CONFIG, "gas_comfort_backup": False},
        heat_data=COMFORTABLE_HEAT,
        optimization_data={
            **COMFORTABLE_PLAN,
            "indoor_temps": [19.8, 19.6, 19.4, 19.2],
        },
    )

    data = await coordinator._async_update_data()

    assert data["comfort_reason"] == "heat_pump_cannot_keep_up"
    assert data["comfort_backup"] is False
    assert data["prefer_gas_boiler"] is False
