"""Tests for number.py's async_setup_entry.

Target temperature/hysteresis are zone-specific settings now (every zone,
including the first, is a subentry - see __init__.py's
_find_primary_zone_subentry) - read from the primary zone's own
coordinator config (`heat_coordinator.config`), not `entry.data`/
`.options` directly, which no longer carry them at all.

Values written by the entities are persisted to `entry.options` via
`hass.config_entries.async_update_entry`, so they survive reloads without
requiring RestoreEntity / the recorder.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heating_curve_optimizer.const import (
    CONF_INDOOR_TEMP_HYSTERESIS,
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_TARGET_INDOOR_TEMP,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
    DEFAULT_TARGET_INDOOR_TEMP,
)
from custom_components.heating_curve_optimizer.number import (
    IndoorTempHysteresisLowerNumber,
    IndoorTempHysteresisUpperNumber,
    TargetIndoorTemperatureNumber,
    async_setup_entry,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_entry(
    options: dict | None = None,
    coordinator_config: dict | None = None,
) -> MagicMock:
    """Build a minimal mock ConfigEntry."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {}
    entry.options = options or {}
    heat_coordinator = MagicMock()
    heat_coordinator.config = coordinator_config or {}
    entry.runtime_data.heat_coordinator = heat_coordinator
    return entry


# ---------------------------------------------------------------------------
# async_setup_entry - skip when no primary zone
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# async_setup_entry - initial value priority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_setup_entry_reads_defaults_from_coordinator_config(
    hass: HomeAssistant,
):
    """Initial values must come from the primary zone's own coordinator
    config, not a fresh {**entry.data, **entry.options} merge (which no
    longer carries these zone-specific fields at all)."""
    entry = _make_entry(
        options={},
        coordinator_config={
            CONF_TARGET_INDOOR_TEMP: 19.5,
            CONF_INDOOR_TEMP_HYSTERESIS_LOWER: 0.2,
            CONF_INDOOR_TEMP_HYSTERESIS_UPPER: 0.6,
        },
    )

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


@pytest.mark.asyncio
async def test_entry_options_take_priority_over_coordinator_config(
    hass: HomeAssistant,
):
    """entry.options (written by the entities on previous runs) must override
    coordinator config so user adjustments survive a reload."""
    entry = _make_entry(
        options={
            CONF_TARGET_INDOOR_TEMP: 21.0,
            CONF_INDOOR_TEMP_HYSTERESIS_LOWER: 0.4,
            CONF_INDOOR_TEMP_HYSTERESIS_UPPER: 0.8,
        },
        coordinator_config={
            CONF_TARGET_INDOOR_TEMP: 19.0,
            CONF_INDOOR_TEMP_HYSTERESIS_LOWER: 0.1,
            CONF_INDOOR_TEMP_HYSTERESIS_UPPER: 0.1,
        },
    )

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    (entities,) = async_add_entities.call_args[0]
    target = next(e for e in entities if isinstance(e, TargetIndoorTemperatureNumber))
    lower = next(e for e in entities if isinstance(e, IndoorTempHysteresisLowerNumber))
    upper = next(e for e in entities if isinstance(e, IndoorTempHysteresisUpperNumber))

    # Values from entry.options win
    assert target.native_value == 21.0
    assert lower.native_value == 0.4
    assert upper.native_value == 0.8


@pytest.mark.asyncio
async def test_legacy_symmetric_hysteresis_fallback(hass: HomeAssistant):
    """If only the old CONF_INDOOR_TEMP_HYSTERESIS key is present in
    coordinator config, it is used as a fallback for both lower and upper
    unless they are overridden by entry.options."""
    entry = _make_entry(
        options={},
        coordinator_config={
            CONF_TARGET_INDOOR_TEMP: 20.0,
            CONF_INDOOR_TEMP_HYSTERESIS: 0.5,
            # no lower/upper keys
        },
    )

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    (entities,) = async_add_entities.call_args[0]
    lower = next(e for e in entities if isinstance(e, IndoorTempHysteresisLowerNumber))
    upper = next(e for e in entities if isinstance(e, IndoorTempHysteresisUpperNumber))

    # Legacy value should be forwarded to both halves
    assert lower.native_value == 0.5
    assert upper.native_value == 0.5


@pytest.mark.asyncio
async def test_defaults_when_no_config_at_all(hass: HomeAssistant):
    """Empty coordinator config and empty entry.options - built-in defaults
    are used and the asymmetric defaults kick in."""
    entry = _make_entry(options={}, coordinator_config={})

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    (entities,) = async_add_entities.call_args[0]
    target = next(e for e in entities if isinstance(e, TargetIndoorTemperatureNumber))
    lower = next(e for e in entities if isinstance(e, IndoorTempHysteresisLowerNumber))
    upper = next(e for e in entities if isinstance(e, IndoorTempHysteresisUpperNumber))

    assert target.native_value == DEFAULT_TARGET_INDOOR_TEMP
    assert lower.native_value == DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER
    assert upper.native_value == DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER


# ---------------------------------------------------------------------------
# BaseTemperatureNumber.native_value reads from entry.options
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_value_reads_from_entry_options_live(hass: HomeAssistant):
    """native_value must return whatever is currently in entry.options,
    even if the entity's internal _attr_native_value has a different value
    (e.g. from setup time)."""
    entry = _make_entry(
        options={CONF_TARGET_INDOOR_TEMP: 19.0},
        coordinator_config={CONF_TARGET_INDOOR_TEMP: 19.0},
    )

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    (entities,) = async_add_entities.call_args[0]
    target = next(e for e in entities if isinstance(e, TargetIndoorTemperatureNumber))

    assert target.native_value == 19.0

    # Simulate another process updating entry.options externally
    entry.options = {CONF_TARGET_INDOOR_TEMP: 22.0}
    assert target.native_value == 22.0


@pytest.mark.asyncio
async def test_native_value_falls_back_to_attr_when_options_empty(
    hass: HomeAssistant,
):
    """When the key is absent from entry.options, the initial value stored in
    _attr_native_value is returned instead."""
    entry = _make_entry(
        options={},
        coordinator_config={CONF_TARGET_INDOOR_TEMP: 20.5},
    )

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    (entities,) = async_add_entities.call_args[0]
    target = next(e for e in entities if isinstance(e, TargetIndoorTemperatureNumber))

    assert target.native_value == 20.5


# ---------------------------------------------------------------------------
# BaseTemperatureNumber.async_set_native_value persists to entry.options
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_native_value_persists_to_entry_options(hass: HomeAssistant):
    """Setting a new value must call async_update_entry with the updated
    options dict, not just mutate the internal attribute."""
    entry = _make_entry(
        options={CONF_TARGET_INDOOR_TEMP: 20.0},
        coordinator_config={CONF_TARGET_INDOOR_TEMP: 20.0},
    )

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    (entities,) = async_add_entities.call_args[0]
    target = next(e for e in entities if isinstance(e, TargetIndoorTemperatureNumber))
    target.hass = hass

    with patch.object(
        hass.config_entries, "async_update_entry"
    ) as mock_update_entry, patch.object(target, "async_write_ha_state"):
        await target.async_set_native_value(21.5)

    mock_update_entry.assert_called_once()
    call_kwargs = mock_update_entry.call_args
    # First positional arg is the entry; second is options kwarg
    assert call_kwargs[0][0] is entry
    new_options = call_kwargs[1]["options"]
    assert new_options[CONF_TARGET_INDOOR_TEMP] == 21.5


@pytest.mark.asyncio
async def test_set_native_value_preserves_existing_options_keys(hass: HomeAssistant):
    """async_set_native_value must merge with the existing options dict,
    not replace it wholesale."""
    existing_options = {
        CONF_TARGET_INDOOR_TEMP: 20.0,
        CONF_INDOOR_TEMP_HYSTERESIS_LOWER: 0.3,
        CONF_INDOOR_TEMP_HYSTERESIS_UPPER: 0.5,
        "some_other_key": "preserved",
    }
    entry = _make_entry(
        options=existing_options,
        coordinator_config={CONF_TARGET_INDOOR_TEMP: 20.0},
    )

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    (entities,) = async_add_entities.call_args[0]
    lower = next(e for e in entities if isinstance(e, IndoorTempHysteresisLowerNumber))
    lower.hass = hass

    with patch.object(
        hass.config_entries, "async_update_entry"
    ) as mock_update_entry, patch.object(lower, "async_write_ha_state"):
        await lower.async_set_native_value(0.7)

    call_kwargs = mock_update_entry.call_args
    new_options = call_kwargs[1]["options"]

    assert new_options[CONF_INDOOR_TEMP_HYSTERESIS_LOWER] == 0.7
    # Other keys must be preserved
    assert new_options[CONF_TARGET_INDOOR_TEMP] == 20.0
    assert new_options[CONF_INDOOR_TEMP_HYSTERESIS_UPPER] == 0.5
    assert new_options["some_other_key"] == "preserved"
