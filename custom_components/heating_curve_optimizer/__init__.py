"""Heating Curve Optimizer integration."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, cast

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DOMAIN,
    ENTITY_MANAGED_OPTIONS,
    GAS_SUBENTRY_TYPE,
    PLATFORMS,
    PV_SUBENTRY_TYPE,
    ZONE_SUBENTRY_TYPE,
)
from .coordinator_heat import HeatCalculationCoordinator
from .coordinator_optimization import OptimizationCoordinator
from .coordinator_weather import WeatherDataCoordinator
from .gas_boiler_coordinator import GasBoilerCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_MANIFEST: dict[str, Any] = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)

SERVICE_RESET_THERMAL_CALIBRATION = "reset_thermal_calibration"
SERVICE_ENTRY_ID = "entry_id"
_SERVICE_RESET_SCHEMA = vol.Schema({vol.Optional(SERVICE_ENTRY_ID): cv.string})

# Delay before the first optimization run, so entities exist before the
# (comparatively slow) DP result arrives.
FIRST_REFRESH_DELAY_S = 5


@dataclass
class HeatingCurveOptimizerData:
    """Runtime data stored on the config entry (`entry.runtime_data`)."""

    weather_coordinator: WeatherDataCoordinator
    # None until the first heating-zone subentry is added.
    heat_coordinator: HeatCalculationCoordinator | None
    optimization_coordinator: OptimizationCoordinator | None
    config: dict[str, Any]
    device: DeviceInfo
    # Additional (non-primary) zones keyed by subentry id: heat_coordinator,
    # optimization_coordinator, config, device, name.
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    gas_boiler_coordinator: GasBoilerCoordinator | None = None
    gas_boiler_device: DeviceInfo | None = None
    gas_boiler_subentry_id: str | None = None
    # entry.options at setup, to tell structural changes from setpoint edits.
    options: dict[str, Any] = field(default_factory=dict)


async def _async_handle_reset_thermal_calibration(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Reset thermal calibration for one or all entries (all zones)."""
    requested_entry_id = call.data.get(SERVICE_ENTRY_ID)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if requested_entry_id is not None and entry.entry_id != requested_entry_id:
            continue
        runtime_data: HeatingCurveOptimizerData | None = getattr(
            entry, "runtime_data", None
        )
        if runtime_data is None:
            continue
        coordinators = [runtime_data.optimization_coordinator] + [
            zone["optimization_coordinator"] for zone in runtime_data.zones.values()
        ]
        for coordinator in coordinators:
            if coordinator is not None:
                await coordinator.async_reset_thermal_calibration()


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    if hass.services.has_service(DOMAIN, SERVICE_RESET_THERMAL_CALIBRATION):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_THERMAL_CALIBRATION,
        partial(_async_handle_reset_thermal_calibration, hass),
        schema=_SERVICE_RESET_SCHEMA,
    )


def _zone_subentries(entry: ConfigEntry) -> list[tuple[str, Any]]:
    """Heating-zone subentries in creation order; the first is the primary zone."""
    return [
        (subentry_id, subentry)
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == ZONE_SUBENTRY_TYPE
    ]


def _child_device(
    identifier: str,
    name: str,
    model: str,
    sw_version: str,
    entry: ConfigEntry,
    parent_device_id: str,
) -> DeviceInfo:
    """DeviceInfo for a subentry device, linked to the main device.

    Newer Home Assistant releases link by registry id (``via_device_id``);
    older ones only accept the parent's identifier tuple (``via_device``).
    """
    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, identifier)},
        "name": name,
        "manufacturer": "Heating Curve Optimizer",
        "model": model,
        "sw_version": sw_version,
    }
    if "via_device_id" in DeviceInfo.__annotations__:
        info["via_device_id"] = parent_device_id
    else:
        info["via_device"] = (DOMAIN, entry.entry_id)
    return cast(DeviceInfo, info)


def _schedule_first_refresh(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: Any
) -> None:
    """Run a coordinator's first refresh shortly after setup, off the setup path."""

    async def _refresh() -> None:
        await asyncio.sleep(FIRST_REFRESH_DELAY_S)
        await coordinator.async_request_refresh()

    entry.async_create_background_task(
        hass, _refresh(), f"{DOMAIN}_first_refresh_{coordinator.name}"
    )


async def _async_setup_zone(
    hass: HomeAssistant,
    entry: ConfigEntry,
    weather_coordinator: WeatherDataCoordinator,
    zone_config: dict[str, Any],
    zone_id: str,
    *,
    primary: bool,
) -> tuple[HeatCalculationCoordinator, OptimizationCoordinator]:
    """Create and start the coordinator pair of one heating zone."""
    heat_coordinator = HeatCalculationCoordinator(
        hass,
        entry,
        weather_coordinator,
        zone_config,
        zone_id,
        live_options=primary,
    )
    await heat_coordinator.async_setup()
    await heat_coordinator.async_refresh()

    optimization_coordinator = OptimizationCoordinator(
        hass, entry, heat_coordinator, zone_config, zone_id
    )
    await optimization_coordinator.async_setup()
    _schedule_first_refresh(hass, entry, optimization_coordinator)
    return heat_coordinator, optimization_coordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    _async_register_services(hass)

    config = {**entry.data, **entry.options}
    config["pv_arrays"] = [
        dict(subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == PV_SUBENTRY_TYPE
    ]

    # A temporary open-meteo outage must not block setup: entities show
    # unavailable until the next poll succeeds.
    weather_coordinator = WeatherDataCoordinator(hass, entry)
    await weather_coordinator.async_refresh()

    sw_version = _MANIFEST.get("version", "unknown")
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heating Curve Optimizer",
        manufacturer="Heating Curve Optimizer",
        model="Heat pump optimizer",
        sw_version=sw_version,
    )
    dev_reg = dr.async_get(hass)
    parent = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, **device)

    zone_subentries = _zone_subentries(entry)
    heat_coordinator: HeatCalculationCoordinator | None = None
    optimization_coordinator: OptimizationCoordinator | None = None
    zones: dict[str, dict[str, Any]] = {}

    for index, (subentry_id, subentry) in enumerate(zone_subentries):
        zone_config = {**config, **subentry.data}
        if index == 0:
            # The primary zone keeps the entry's own identity (unique_ids,
            # calibration storage) and its setpoints are live-adjustable.
            heat_coordinator, optimization_coordinator = await _async_setup_zone(
                hass,
                entry,
                weather_coordinator,
                zone_config,
                entry.entry_id,
                primary=True,
            )
            continue
        zone_id = f"{entry.entry_id}_{subentry_id}"
        zone_heat, zone_optimization = await _async_setup_zone(
            hass, entry, weather_coordinator, zone_config, zone_id, primary=False
        )
        zones[subentry_id] = {
            "heat_coordinator": zone_heat,
            "optimization_coordinator": zone_optimization,
            "config": zone_config,
            "device": _child_device(
                zone_id, subentry.title, "Heating zone", sw_version, entry, parent.id
            ),
            "name": subentry.title,
        }

    gas_boiler_coordinator: GasBoilerCoordinator | None = None
    gas_boiler_device: DeviceInfo | None = None
    gas_boiler_subentry_id: str | None = None
    gas_subentries = [
        (subentry_id, subentry)
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == GAS_SUBENTRY_TYPE
    ]
    if gas_subentries and heat_coordinator and optimization_coordinator:
        gas_boiler_subentry_id, gas_subentry = gas_subentries[0]
        gas_boiler_coordinator = GasBoilerCoordinator(
            hass,
            entry,
            heat_coordinator,
            optimization_coordinator,
            {**config, **gas_subentry.data},
            entry.entry_id,
        )
        await gas_boiler_coordinator.async_setup()
        _schedule_first_refresh(hass, entry, gas_boiler_coordinator)
        gas_boiler_device = _child_device(
            f"{entry.entry_id}_gas_boiler",
            gas_subentry.title,
            "Hybrid gas boiler",
            sw_version,
            entry,
            parent.id,
        )

    entry.runtime_data = HeatingCurveOptimizerData(
        weather_coordinator=weather_coordinator,
        heat_coordinator=heat_coordinator,
        optimization_coordinator=optimization_coordinator,
        config=config,
        device=device,
        zones=zones,
        gas_boiler_coordinator=gas_boiler_coordinator,
        gas_boiler_device=gas_boiler_device,
        gas_boiler_subentry_id=gas_boiler_subentry_id,
        options=dict(entry.options),
    )
    _LOGGER.debug(
        "Set up entry %s (device %s) with %d zone(s)",
        entry.entry_id,
        parent.id,
        len(zone_subentries),
    )

    entry.async_on_unload(entry.add_update_listener(_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload on structural changes; re-optimize on setpoint changes.

    The number/climate entities store the primary zone's target temperature
    and hysteresis in ``entry.options``. Such an edit needs no reload - the
    heat coordinator reads those options live - but it must reach the
    optimizer right away, so the primary zone is refreshed in dependency
    order instead.
    """
    runtime_data: HeatingCurveOptimizerData | None = getattr(
        entry, "runtime_data", None
    )
    if runtime_data is None:
        return

    old_options = runtime_data.options
    new_options = dict(entry.options)
    changed = {
        key
        for key in set(old_options) | set(new_options)
        if old_options.get(key) != new_options.get(key)
    }
    if changed and changed <= ENTITY_MANAGED_OPTIONS:
        runtime_data.options = new_options
        if runtime_data.heat_coordinator is not None:
            await runtime_data.heat_coordinator.async_refresh()
        if runtime_data.optimization_coordinator is not None:
            await runtime_data.optimization_coordinator.async_request_refresh()
        return

    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing devices left behind by deleted subentries."""
    active = {config_entry.entry_id}
    for subentry_id, subentry in config_entry.subentries.items():
        if subentry.subentry_type == ZONE_SUBENTRY_TYPE:
            active.add(f"{config_entry.entry_id}_{subentry_id}")
        elif subentry.subentry_type == GAS_SUBENTRY_TYPE:
            active.add(f"{config_entry.entry_id}_gas_boiler")
    return not any(
        domain == DOMAIN and identifier in active
        for domain, identifier in device_entry.identifiers
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (coordinators shut down with the entry)."""
    unload_ok = bool(await hass.config_entries.async_unload_platforms(entry, PLATFORMS))
    if unload_ok and not [
        other
        for other in hass.config_entries.async_loaded_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ]:
        hass.services.async_remove(DOMAIN, SERVICE_RESET_THERMAL_CALIBRATION)
    return unload_ok
