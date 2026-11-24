"""Tests for PeriodicSave: changing state is written, not debounced forever."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.heating_curve_optimizer.delayed_save import PeriodicSave
from custom_components.heating_curve_optimizer.mass_model import MassModelState


def test_a_pending_write_is_not_postponed() -> None:
    store = MagicMock()
    save = PeriodicSave(store, 900)
    start = dt_util.utcnow()
    for minute in range(60):
        save.request(start + timedelta(minutes=minute), dict)
    # Once per 900 s + slack (16 minutes), not 60 times.
    assert store.async_delay_save.call_count == 4


def test_an_important_change_is_written_soon() -> None:
    store = MagicMock()
    save = PeriodicSave(store, 900)
    now = dt_util.utcnow()
    save.request(now, dict)
    save.request_soon(now + timedelta(minutes=1), dict)
    assert store.async_delay_save.call_args.args[1] == 10


async def test_minute_updates_reach_the_disk(
    hass: HomeAssistant, hass_storage, freezer
) -> None:
    """The real Store: with a plain async_delay_save this is never written."""
    store: storage.Store = storage.Store(hass, 1, "periodic_test")
    save = PeriodicSave(store, 900)
    for minute in range(20):
        save.request(dt_util.utcnow(), lambda minute=minute: {"minute": minute})
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert "periodic_test" in hass_storage


async def test_an_unloaded_mass_model_does_not_overwrite_the_store() -> None:
    store = MagicMock()
    store.async_save = AsyncMock()
    store.async_load = AsyncMock(return_value=None)
    state = MassModelState(store)
    await state.async_flush()
    store.async_save.assert_not_called()
    await state.async_load()
    await state.async_flush()
    store.async_save.assert_called_once()
