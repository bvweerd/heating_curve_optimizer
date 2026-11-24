"""Periodic saving for state that changes all the time.

`Store.async_delay_save` is a debounce: every call moves the pending write
to its own delay. State that changes every minute with a delay of fifteen
minutes is then never written, except on a clean stop of Home Assistant.
`PeriodicSave` only asks for a write when none is pending, so the state is
written once per interval - and, because a write stays pending, also on a
clean stop.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.helpers import storage

# A request close to the pending write would postpone it.
_SLACK = timedelta(seconds=60)


class PeriodicSave:
    """Save a store at most once per `interval`, whatever the call rate."""

    def __init__(self, store: storage.Store[dict[str, Any]], interval: float) -> None:
        """`interval` in seconds."""
        self._store = store
        self._interval = interval
        self._pending_until: datetime | None = None

    def request(self, now: datetime, data_func: Callable[[], dict[str, Any]]) -> None:
        """Make sure a write is pending."""
        if self._pending_until is not None and now < self._pending_until:
            return
        self._schedule(now, data_func, self._interval)

    def request_soon(
        self, now: datetime, data_func: Callable[[], dict[str, Any]]
    ) -> None:
        """Write within seconds (rare, important changes)."""
        self._schedule(now, data_func, 10)

    def _schedule(
        self, now: datetime, data_func: Callable[[], dict[str, Any]], delay: float
    ) -> None:
        self._store.async_delay_save(data_func, delay)
        self._pending_until = now + timedelta(seconds=delay) + _SLACK

    async def flush(self, data: dict[str, Any]) -> None:
        """Write now (unload, reset); replaces a pending write."""
        await self._store.async_save(data)
        self._pending_until = None
