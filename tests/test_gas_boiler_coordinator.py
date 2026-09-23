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
            "future_supply_temperatures": [40.0],
            "heat_pump_actively_running": True,
        }
    )
    return GasBoilerCoordinator(
        hass,
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


@pytest.mark.asyncio
async def test_prefer_gas_boiler_true_when_needed_and_cheaper(hass: HomeAssistant):
    """A high electricity price with a low gas price and heat pump
    confirmed actively running -> prefer_gas_boiler must be True."""
    hass.states.async_set("sensor.gas_price", "0.50")
    hass.states.async_set("sensor.elec_price", "1.00")
    coordinator = _make_coordinator(hass)

    data = await coordinator._async_update_data()

    assert data["heat_currently_needed"] is True
    assert data["heat_currently_needed_source"] == "power_sensor"
    assert data["prefer_gas_boiler"] is True


@pytest.mark.asyncio
async def test_prefer_gas_boiler_false_when_heat_not_needed_even_if_gas_cheaper(
    hass: HomeAssistant,
):
    """Critical regression: gas being cheaper must never recommend
    switching when no heat is needed at all (buffer/solar gain covers
    demand) - coasting costs nothing and always wins."""
    hass.states.async_set("sensor.gas_price", "0.10")
    hass.states.async_set("sensor.elec_price", "1.00")
    coordinator = _make_coordinator(
        hass,
        optimization_data={
            "future_supply_temperatures": [40.0],
            "heat_pump_actively_running": False,
        },
    )

    data = await coordinator._async_update_data()

    assert data["heat_currently_needed"] is False
    assert data["prefer_gas_boiler"] is False


@pytest.mark.asyncio
async def test_prefer_gas_boiler_false_when_heat_pump_cheaper(hass: HomeAssistant):
    hass.states.async_set("sensor.gas_price", "2.00")
    hass.states.async_set("sensor.elec_price", "0.20")
    coordinator = _make_coordinator(hass)

    data = await coordinator._async_update_data()

    assert data["heat_currently_needed"] is True
    assert data["prefer_gas_boiler"] is False


@pytest.mark.asyncio
async def test_falls_back_to_modeled_demand_when_no_power_sensor(hass: HomeAssistant):
    """heat_pump_actively_running is None (no power sensor configured) ->
    fall back to the modeled heat_pump_on demand signal, not False."""
    hass.states.async_set("sensor.gas_price", "0.10")
    hass.states.async_set("sensor.elec_price", "1.00")
    coordinator = _make_coordinator(
        hass,
        heat_data={
            "outdoor_temperature": -5.0,
            "heat_pump_on": True,
            "net_heat_loss": 1.0,
        },
        optimization_data={
            "future_supply_temperatures": [40.0],
            "heat_pump_actively_running": None,
        },
    )

    data = await coordinator._async_update_data()

    assert data["heat_currently_needed"] is True
    assert data["heat_currently_needed_source"] == "modeled_demand"
    assert data["prefer_gas_boiler"] is True


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
        "future_supply_temperatures": [40.0],
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
