"""Tests for binary_sensor.py's async_setup_entry.

The early-exit guard used to check `entry.data.get(CONF_AREA_M2)`/
`CONF_ENERGY_LABEL` directly - now zone-specific and no longer present on
the main entry at all (every zone, including the first, is a subentry -
see __init__.py's _find_primary_zone_subentry). The guard now goes through
`runtime_data.heat_coordinator` instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.binary_sensor import (
    CoordinatorHeatDemandBinarySensor,
    HeatDemandBinarySensor,
    async_setup_entry,
)


@pytest.mark.asyncio
async def test_async_setup_entry_uses_legacy_fallback_without_primary_zone(
    hass: HomeAssistant,
):
    """No primary heating zone configured yet - falls back to the legacy
    polling-based sensor rather than crashing on entry.data fields that no
    longer exist."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.runtime_data.heat_coordinator = None

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities, _poll = async_add_entities.call_args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], HeatDemandBinarySensor)


@pytest.mark.asyncio
async def test_async_setup_entry_creates_coordinator_sensor_with_primary_zone(
    hass: HomeAssistant,
):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.runtime_data.heat_coordinator = MagicMock()
    entry.runtime_data.device = MagicMock()
    entry.runtime_data.zones = {}
    entry.runtime_data.gas_boiler_coordinator = None
    entry.runtime_data.gas_boiler_device = None

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities, _poll = async_add_entities.call_args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], CoordinatorHeatDemandBinarySensor)
