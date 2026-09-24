"""Shared pytest fixtures for Heating Curve Optimizer tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_curve_optimizer.const import (
    DOMAIN,
    GAS_SUBENTRY_TYPE,
    PV_SUBENTRY_TYPE,
    ZONE_SUBENTRY_TYPE,
)

PRICE_SENSOR = "sensor.electricity_price"
INDOOR_SENSOR = "sensor.living_room_temperature"
POWER_SENSOR = "sensor.heat_pump_power"
GAS_PRICE_SENSOR = "sensor.gas_price"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    return enable_custom_integrations


def main_config(**overrides: Any) -> dict[str, Any]:
    """A complete main-entry config as the config flow stores it."""
    return {
        "consumption_price_sensor": PRICE_SENSOR,
        "production_price_sensor": None,
        "power_consumption": None,
        "supply_temperature_sensor": None,
        "grid_import_sensor": None,
        "grid_export_sensor": None,
        "base_cop": 4.2,
        "k_factor": 0.11,
        "outdoor_temp_coefficient": 0.08,
        "cop_compensation_factor": 1.0,
        "heat_pump_max_thermal_power_kw": None,
        "heat_curve_min": 25.0,
        "heat_curve_max": 45.0,
        "heat_curve_min_outdoor": -10.0,
        "heat_curve_max_outdoor": 15.0,
        "offset_delta_t": 30,
        "planning_window": 24,
        **overrides,
    }


def zone_data(name: str = "Living room", **overrides: Any) -> dict[str, Any]:
    """Heating-zone subentry data as the zone flow stores it."""
    return {
        "name": name,
        "area_m2": 150.0,
        "energy_label": "C",
        "ventilation_type": "natural_standard",
        "ceiling_height": 2.5,
        "thermal_mass_class": "medium",
        "emitter_type": "radiator",
        "internal_gains_w_per_m2": 3.0,
        "glass_south_m2": 8.0,
        "glass_east_m2": 2.0,
        "glass_west_m2": 2.0,
        "glass_u_value": 1.2,
        "target_indoor_temp": 20.0,
        "indoor_temp_hysteresis_lower": 0.3,
        "indoor_temp_hysteresis_upper": 0.5,
        "indoor_temperature_sensor": INDOOR_SENSOR,
        **overrides,
    }


def subentry(
    subentry_type: str, title: str, data: dict[str, Any]
) -> ConfigSubentryData:
    """ConfigSubentryData for MockConfigEntry(subentries_data=...)."""
    return ConfigSubentryData(
        data=data, subentry_type=subentry_type, title=title, unique_id=None
    )


def make_entry(
    *,
    zones: int = 1,
    pv: bool = False,
    gas: bool = False,
    options: dict[str, Any] | None = None,
    **config: Any,
) -> MockConfigEntry:
    """A config entry with the requested subentries."""
    subentries = [
        subentry(ZONE_SUBENTRY_TYPE, f"Zone {i + 1}", zone_data(f"Zone {i + 1}"))
        for i in range(zones)
    ]
    if pv:
        subentries.append(
            subentry(
                PV_SUBENTRY_TYPE,
                "Roof",
                {
                    "peak_power_kwp": 4.0,
                    "orientation": 180.0,
                    "tilt": 35.0,
                    "efficiency_factor": 0.85,
                    "dc_coupled": False,
                },
            )
        )
    if gas:
        subentries.append(
            subentry(
                GAS_SUBENTRY_TYPE,
                "Gas Boiler",
                {
                    "gas_price_sensor": GAS_PRICE_SENSOR,
                    "gas_boiler_efficiency": 0.9,
                    "gas_calorific_value_kwh_per_m3": 9.77,
                },
            )
        )
    return MockConfigEntry(
        domain=DOMAIN,
        title="Heating Curve Optimizer",
        data=main_config(**config),
        options=options or {},
        subentries_data=subentries,
        unique_id=DOMAIN,
    )


def open_meteo_payload(outdoor: float = 3.0, hours: int = 72) -> dict[str, Any]:
    """An open-meteo response starting at today's 00:00 UTC."""
    start = dt_util.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    times = [
        (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)
    ]
    radiation = [
        max(0.0, 400.0 - abs(((start + timedelta(hours=i)).hour - 12) * 80.0))
        for i in range(hours)
    ]
    return {
        "current": {"temperature_2m": outdoor},
        "hourly": {
            "time": times,
            "temperature_2m": [outdoor] * hours,
            "relative_humidity_2m": [85.0] * hours,
            "shortwave_radiation": radiation,
            "direct_normal_irradiance": [r * 1.2 for r in radiation],
            "diffuse_radiation": [r * 0.3 for r in radiation],
        },
    }


def price_attributes(hours: int = 36) -> dict[str, Any]:
    """Hourly timestamped prices from the current hour, cheap then expensive."""
    start = dt_util.now().replace(minute=0, second=0, microsecond=0)
    entries = [
        {
            "start": (start + timedelta(hours=i)).isoformat(),
            "end": (start + timedelta(hours=i + 1)).isoformat(),
            "value": 0.10 if (i // 4) % 2 == 0 else 0.40,
        }
        for i in range(hours)
    ]
    return {"raw_today": entries, "unit_of_measurement": "EUR/kWh"}


@pytest.fixture
def mock_open_meteo(aioclient_mock):
    """Serve a fixed open-meteo forecast."""
    aioclient_mock.get(OPEN_METEO_URL, json=open_meteo_payload())
    return aioclient_mock


@pytest.fixture
def price_state(hass: HomeAssistant) -> None:
    """A price sensor with a 36 h hourly forecast and an indoor sensor."""
    hass.states.async_set(PRICE_SENSOR, "0.10", price_attributes())
    hass.states.async_set(
        INDOOR_SENSOR,
        "20.0",
        {"unit_of_measurement": "°C", "device_class": "temperature"},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add, set up, and run the first optimization of an entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    coordinators = [runtime.optimization_coordinator] + [
        zone["optimization_coordinator"] for zone in runtime.zones.values()
    ]
    for coordinator in coordinators:
        if coordinator is not None:
            await coordinator.async_refresh()
    if runtime.gas_boiler_coordinator is not None:
        await runtime.gas_boiler_coordinator.async_refresh()
    await hass.async_block_till_done()
