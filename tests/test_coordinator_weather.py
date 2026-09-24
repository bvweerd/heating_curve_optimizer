"""Tests for WeatherDataCoordinator (coordinator_weather.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.heating_curve_optimizer.coordinator import _update_failed


def test_update_failed_falls_back_on_ha_versions_without_translation_kwargs():
    """This repo's own test environment (HA 2024.3.3) is exactly such a
    release: UpdateFailed still extends plain Exception there, so
    UpdateFailed(translation_domain=...) raises TypeError
    ("takes no keyword arguments"). _update_failed() must degrade to a
    formatted plain-string message instead of crashing every coordinator
    update on that release - this is exercised for real (not mocked) by
    every other UpdateFailed-raising test in this file, since this HA
    release always takes the fallback branch."""
    # Patch UpdateFailed so translation kwargs raise TypeError, simulating
    # older HA versions.  On newer HA the real constructor calls
    # async_get_hass() which blows up outside the HA event-loop thread.
    _OrigUpdateFailed = UpdateFailed

    def _reject_kwargs(*args, **kwargs):
        if kwargs:
            raise TypeError("UpdateFailed() got unexpected keyword arguments")
        return _OrigUpdateFailed(*args)

    with patch(
        "custom_components.heating_curve_optimizer.coordinator_weather.UpdateFailed",
        side_effect=_reject_kwargs,
    ):
        err = _update_failed("price_sensor_unavailable", {"sensor": "sensor.price"})
    assert isinstance(err, UpdateFailed)
    assert str(err) == "Price sensor sensor.price is unavailable."


def test_update_failed_without_placeholders():
    _OrigUpdateFailed = UpdateFailed

    def _reject_kwargs(*args, **kwargs):
        if kwargs:
            raise TypeError("UpdateFailed() got unexpected keyword arguments")
        return _OrigUpdateFailed(*args)

    with patch(
        "custom_components.heating_curve_optimizer.coordinator_weather.UpdateFailed",
        side_effect=_reject_kwargs,
    ):
        err = _update_failed("no_price_sensor")
    assert isinstance(err, UpdateFailed)
    assert str(err) == "No electricity price sensor is configured."
