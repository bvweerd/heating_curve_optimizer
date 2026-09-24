"""Weather data coordinator for the Heating Curve Optimizer integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Plain-English fallback for each UpdateFailed translation_key, mirroring
# strings.json's "exceptions" messages - used only on an HA release old
# enough that UpdateFailed still extends plain Exception rather than
# HomeAssistantError (confirmed the case for this repo's own test
# environment, HA 2024.3.3: UpdateFailed(translation_domain=...) raises
# TypeError there, "takes no keyword arguments"). On such a release the
# translation_domain/translation_key/translation_placeholders kwargs
# cannot be passed at all, so _update_failed() below falls back to a
# formatted message instead of crashing setup with a TypeError.
_UPDATE_FAILED_MESSAGES: dict[str, str] = {
    "api_error": "Open-Meteo API returned status {status}.",
    "connection_error": "Error fetching weather data from open-meteo.com: {error}.",
    "no_forecast_data": "No forecast data in the open-Meteo API response.",
    "no_weather_data": "Weather coordinator has no data yet.",
    "missing_building_config": "Missing area or energy label configuration.",
    "no_heat_data": "Heat calculation coordinator has no data yet.",
    "no_weather_data_for_optimization": "No weather data available for optimization.",
    "no_price_sensor": "No electricity price sensor is configured.",
    "price_sensor_unavailable": "Price sensor {sensor} is unavailable.",
    "price_data_extraction_failed": "Cannot extract price data from sensor {sensor}.",
    "no_gas_price_sensor": "No gas price sensor is configured.",
    "gas_price_sensor_unavailable": "Gas price sensor {sensor} is unavailable.",
    "gas_boiler_operating_point_unavailable": (
        "No operating point (outdoor/supply temperature) available yet for "
        "the gas boiler comparison."
    ),
    "gas_boiler_electricity_price_unavailable": (
        "Electricity price sensor {sensor} is unavailable "
        "(see the main price_sensor_unavailable repair issue)."
    ),
    "thermal_optimizer_failed": "The thermal optimizer failed this cycle: {error}.",
}


def _update_failed(
    translation_key: str, translation_placeholders: dict[str, str] | None = None
) -> UpdateFailed:
    """Build an UpdateFailed with translation support where the installed
    HA's UpdateFailed accepts it, falling back to a formatted plain-string
    message on an HA release old enough that it doesn't (see the module-
    level comment above _UPDATE_FAILED_MESSAGES)."""
    try:
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders=translation_placeholders,
        )
    except TypeError:
        message = _UPDATE_FAILED_MESSAGES[translation_key].format(
            **(translation_placeholders or {})
        )
        return UpdateFailed(message)


class WeatherDataCoordinator(DataUpdateCoordinator):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Coordinator for weather and radiation data from open-meteo.com."""

    def __init__(
        self,
        hass: HomeAssistant,
    ):
        """Initialize the weather data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Weather Data",
            update_interval=timedelta(minutes=30),
        )
        self.latitude = hass.config.latitude
        self.longitude = hass.config.longitude
        self.session = async_get_clientsession(hass)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch weather and radiation data from open-meteo.com."""
        _LOGGER.debug(
            "Fetching weather data for %.4f, %.4f", self.latitude, self.longitude
        )

        # Combine temperature, humidity, and radiation in one API call
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            "&hourly=temperature_2m,relative_humidity_2m,shortwave_radiation"
            "&current_weather=true&timezone=UTC&forecast_days=2"
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
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise _update_failed(
                "connection_error",
                translation_placeholders={"error": str(err)},
            ) from err

        # Extract current weather
        current_weather = data.get("current_weather", {})
        current_temp = float(current_weather.get("temperature", 0))

        # Extract hourly forecasts
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        humidity = hourly.get("relative_humidity_2m", [])
        radiation = hourly.get("shortwave_radiation", [])

        if not times or not temps:
            raise _update_failed("no_forecast_data")

        # Find current hour index
        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        start_idx = 0
        for i, ts in enumerate(times):
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if t >= now:
                start_idx = i
                break

        # Extract next 48 hours (2 days)
        temp_forecast = [float(v) for v in temps[start_idx : start_idx + 48]]
        humidity_forecast = (
            [float(v) for v in humidity[start_idx : start_idx + 48]] if humidity else []
        )
        radiation_forecast = (
            [float(v) for v in radiation[start_idx : start_idx + 48]]
            if radiation
            else []
        )

        result = {
            "current_temperature": round(current_temp, 2),
            "temperature_forecast": [round(v, 2) for v in temp_forecast],
            "humidity_forecast": [round(v, 1) for v in humidity_forecast],
            "radiation_forecast": [round(v, 1) for v in radiation_forecast],
            "timestamp": dt_util.utcnow(),
        }

        _LOGGER.debug(
            "Weather data updated: current=%.1f°C, forecast=%d hours",
            current_temp,
            len(temp_forecast),
        )

        return result
