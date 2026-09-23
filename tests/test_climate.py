"""Tests for climate.py (phase 5, docs/redesign/REDESIGN.md)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.climate import (
    HeatingOptimizerClimate,
    async_setup_entry,
)


def _make_climate(coordinator, hass=None, entry=None) -> HeatingOptimizerClimate:
    return HeatingOptimizerClimate(
        hass or MagicMock(), entry or MagicMock(), MagicMock(), coordinator
    )


def test_disabled_by_default():
    coordinator = MagicMock()
    coordinator.data = {}
    climate = _make_climate(coordinator)
    assert climate.entity_registry_enabled_default is False


def test_hvac_mode_is_always_heat():
    coordinator = MagicMock()
    coordinator.data = {}
    climate = _make_climate(coordinator)
    assert climate.hvac_mode == HVACMode.HEAT


def test_current_temperature_none_without_real_sensor():
    coordinator = MagicMock()
    coordinator.data = {"indoor_temperature": 21.0}
    coordinator.has_real_indoor_sensor = False
    climate = _make_climate(coordinator)
    assert climate.current_temperature is None


def test_current_temperature_reported_with_real_sensor():
    coordinator = MagicMock()
    coordinator.data = {"indoor_temperature": 21.0}
    coordinator.has_real_indoor_sensor = True
    climate = _make_climate(coordinator)
    assert climate.current_temperature == 21.0


def test_target_temperature_reflects_coordinator_data():
    coordinator = MagicMock()
    coordinator.data = {"target_temperature": 20.5}
    climate = _make_climate(coordinator)
    assert climate.target_temperature == 20.5


def test_hvac_action_reflects_heat_pump_on():
    coordinator = MagicMock()
    coordinator.data = {"heat_pump_on": True}
    climate = _make_climate(coordinator)
    assert climate.hvac_action == HVACAction.HEATING

    coordinator.data = {"heat_pump_on": False}
    assert climate.hvac_action == HVACAction.IDLE


def test_available_reflects_coordinator_success():
    coordinator = MagicMock()
    coordinator.last_update_success = False
    coordinator.data = {"target_temperature": 20.0}
    climate = _make_climate(coordinator)
    assert climate.available is False


@pytest.mark.asyncio
async def test_set_temperature_routes_through_number_entity(hass: HomeAssistant):
    coordinator = MagicMock()
    coordinator.data = {}
    entry = MagicMock()
    entry.entry_id = "test_entry"
    climate = HeatingOptimizerClimate(hass, entry, MagicMock(), coordinator)

    # Register the number entity so the entity-registry lookup succeeds.
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "number",
        "heating_curve_optimizer",
        "test_entry_target_indoor_temp",
        suggested_object_id="target_indoor_temperature",
    )

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await climate.async_set_temperature(temperature=21.5)

    mock_call.assert_awaited_once()
    call_args = mock_call.call_args
    assert call_args[0][0] == "number"
    assert call_args[0][1] == "set_value"
    assert call_args[0][2]["value"] == 21.5


@pytest.mark.asyncio
async def test_set_temperature_warns_when_number_entity_missing(
    hass: HomeAssistant,
):
    coordinator = MagicMock()
    coordinator.data = {}
    entry = MagicMock()
    entry.entry_id = "entry_with_no_number_entity"
    climate = HeatingOptimizerClimate(hass, entry, MagicMock(), coordinator)

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await climate.async_set_temperature(temperature=21.5)

    mock_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_temperature_without_value_is_noop(hass: HomeAssistant):
    coordinator = MagicMock()
    coordinator.data = {}
    climate = _make_climate(coordinator, hass=hass)

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await climate.async_set_temperature()

    mock_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_setup_entry_skips_when_no_primary_zone(hass: HomeAssistant):
    """No primary heating zone configured yet (see __init__.py's
    _find_primary_zone_subentry) - there is no target temperature to
    reflect/adjust, so no climate entity is created."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.runtime_data.heat_coordinator = None

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_creates_entity_with_primary_zone(
    hass: HomeAssistant,
):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    heat_coordinator = MagicMock()
    entry.runtime_data.heat_coordinator = heat_coordinator
    entry.runtime_data.device = MagicMock()

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    (entities,) = async_add_entities.call_args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], HeatingOptimizerClimate)
