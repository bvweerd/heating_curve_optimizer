"""Weather data coordinator for the Heating Curve Optimizer integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FORECAST_HOURS = 48


def _update_failed(
    translation_key: str, translation_placeholders: dict[str, str] | None = None
) -> UpdateFailed:
    """Build a translated UpdateFailed (messages live in strings.json)."""
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=translation_placeholders,
    )


def _parse_utc(timestamp: str) -> datetime | None:
    """Parse an open-meteo timestamp (requested with timezone=UTC)."""
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


class WeatherDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for weather and radiation data from open-meteo.com.

    Every hourly series in ``data`` is aligned so that index ``i`` describes
    the hour ``[forecast_start_utc + i h, forecast_start_utc + (i+1) h)``,
    where ``forecast_start_utc`` is the start of the current hour.

    open-meteo reports temperature/humidity/wind as instantaneous values at
    the timestamp, but radiation as the *mean over the preceding hour*. The
    radiation series are therefore taken one slot later, so that both kinds
    of series describe the same hour.
    """

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the weather data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Weather Data",
            update_interval=timedelta(minutes=30),
        )
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = async_get_clientsession(hass)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch weather and radiation data from open-meteo.com."""
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m,relative_humidity_2m,shortwave_radiation"
            ",direct_normal_irradiance,diffuse_radiation"
            "&current=temperature_2m&timezone=UTC&forecast_days=3"
        )

        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    raise _update_failed(
                        "api_error",
                        translation_placeholders={"status": str(resp.status)},
                    )
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _update_failed(
                "connection_error",
                translation_placeholders={"error": str(err)},
            ) from err

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        if not times or not temps:
            raise _update_failed("no_forecast_data")

        hour_start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        start_idx = 0
        for i, raw in enumerate(times):
            parsed = _parse_utc(raw)
            if parsed is not None and parsed >= hour_start:
                start_idx = i
                break

        def _series(key: str, offset: int = 0) -> list[float]:
            raw = hourly.get(key) or []
            first = start_idx + offset
            return [
                round(float(v), 2) if v is not None else 0.0
                for v in raw[first : first + FORECAST_HOURS]
            ]

        temp_forecast = _series("temperature_2m")
        current = data.get("current", {})
        current_temp = current.get("temperature_2m")
        if current_temp is None:
            current_temp = temp_forecast[0]

        result = {
            "current_temperature": round(float(current_temp), 2),
            "temperature_forecast": temp_forecast,
            "humidity_forecast": _series("relative_humidity_2m"),
            # Radiation is a preceding-hour mean: slot i+1 covers hour i.
            "radiation_forecast": _series("shortwave_radiation", 1),
            "dni_forecast": _series("direct_normal_irradiance", 1),
            "diffuse_forecast": _series("diffuse_radiation", 1),
            "forecast_start_utc": hour_start,
            "timestamp": dt_util.utcnow(),
        }

        _LOGGER.debug(
            "Weather data updated: current=%.1f°C, %d forecast hours",
            result["current_temperature"],
            len(temp_forecast),
        )
        return result
