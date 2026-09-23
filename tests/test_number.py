"""Tests for number.py's async_setup_entry.

Target temperature/hysteresis are zone-specific settings now (every zone,
including the first, is a subentry - see __init__.py's
_find_primary_zone_subentry) - read from the primary zone's own
coordinator config (`heat_coordinator.config`), not `entry.data`/
`.options` directly, which no longer carry them at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.const import (
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_TARGET_INDOOR_TEMP,
)
from custom_components.heating_curve_optimizer.number import (
    IndoorTempHysteresisLowerNumber,
    IndoorTempHysteresisUpperNumber,
    TargetIndoorTemperatureNumber,
    async_setup_entry,
)


@pytest.mark.asyncio
async def test_async_setup_entry_skips_when_no_primary_zone(hass: HomeAssistant):
    """No primary heating zone configured yet - nothing to control, so no
    number entities are created."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.runtime_data.heat_coordinator = None

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_reads_defaults_from_coordinator_config(
    hass: HomeAssistant,
):
    """Initial values must come from the primary zone's own coordinator
    config, not a fresh {**entry.data, **entry.options} merge (which no
    longer carries these zone-specific fields at all)."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {}
    entry.options = {}
    heat_coordinator = MagicMock()
    heat_coordinator.config = {
        CONF_TARGET_INDOOR_TEMP: 19.5,
        CONF_INDOOR_TEMP_HYSTERESIS_LOWER: 0.2,
        CONF_INDOOR_TEMP_HYSTERESIS_UPPER: 0.6,
    }
    entry.runtime_data.heat_coordinator = heat_coordinator

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    (entities,) = async_add_entities.call_args[0]
    assert len(entities) == 3

    target = next(e for e in entities if isinstance(e, TargetIndoorTemperatureNumber))
    lower = next(e for e in entities if isinstance(e, IndoorTempHysteresisLowerNumber))
    upper = next(e for e in entities if isinstance(e, IndoorTempHysteresisUpperNumber))
    assert target.native_value == 19.5
    assert lower.native_value == 0.2
    assert upper.native_value == 0.6
