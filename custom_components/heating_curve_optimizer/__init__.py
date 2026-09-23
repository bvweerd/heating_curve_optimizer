# custom_components/heating_curve_optimizer/__init__.py

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    GAS_SUBENTRY_TYPE,
    PLATFORMS,
    PV_SUBENTRY_TYPE,
    ZONE_SUBENTRY_TYPE,
)
from .coordinator import (
    WeatherDataCoordinator,
    HeatCalculationCoordinator,
    OptimizationCoordinator,
)
from .gas_boiler_coordinator import GasBoilerCoordinator

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Load version once at module import time (blocking I/O at module level is
# fine - manifest.json is small and local). Single source of truth: before
# this, DeviceInfo.sw_version was a hardcoded "2.0.0" that had drifted from
# manifest.json's actual "1.0.2" - phase 6 (docs/redesign/REDESIGN.md).
_MANIFEST: dict[str, Any] = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)

SERVICE_RESET_THERMAL_CALIBRATION = "reset_thermal_calibration"
SERVICE_ENTRY_ID = "entry_id"
_SERVICE_RESET_SCHEMA = vol.Schema({vol.Optional(SERVICE_ENTRY_ID): cv.string})


@dataclass
class HeatingCurveOptimizerData:
    """Runtime data stored on the config entry (`entry.runtime_data`).

    Mirrors battery_controller's `BatteryControllerData` - a typed
    dataclass on the entry itself instead of a loosely-typed dict nested
    under `hass.data[DOMAIN][entry.entry_id]` (quality_scale's
    `runtime-data` rule). `ConfigEntry` has no `__slots__` in every HA
    release this integration has been tested against, so assigning this
    attribute dynamically is safe even where `runtime_data` predates the
    installed HA's own `ConfigEntry` class.
    """

    weather_coordinator: WeatherDataCoordinator
    heat_coordinator: HeatCalculationCoordinator
    optimization_coordinator: OptimizationCoordinator
    config: dict[str, Any]
    device: DeviceInfo
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Hybrid gas-boiler cost comparison (optional, GAS_SUBENTRY_TYPE-gated -
    # see gas_boiler_coordinator.py). None for every installation that
    # hasn't configured the subentry - the common case.
    gas_boiler_coordinator: GasBoilerCoordinator | None = None
    gas_boiler_device: DeviceInfo | None = None
    gas_boiler_subentry_id: str | None = None
    # entry.options exactly as they stood at setup - _update_listener compares
    # the live entry.options against this to tell whether anything actually
    # changed. Never mutated after setup: a reload rebuilds this dataclass
    # from scratch with a fresh snapshot, so there is nothing to keep in
    # sync in between.
    options: dict[str, Any] = field(default_factory=dict)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the base integration (no YAML)."""
    hass.data.setdefault(DOMAIN, {})
    _LOGGER.info("Initialized Heating Curve Optimizer")
    return True


async def _async_handle_reset_thermal_calibration(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Reset thermal calibration (calibration.py) for one or all entries.

    The escape hatch a bad fit needs: without it, a wrong learned UA/
    thermal-mass could otherwise only be cleared by editing `.storage` by
    hand. Mirrors battery_controller's per-direction reset services.
    """
    requested_entry_id = call.data.get(SERVICE_ENTRY_ID)
    entries = hass.config_entries.async_entries(DOMAIN)
    matched = [
        entry
        for entry in entries
        if requested_entry_id is None or entry.entry_id == requested_entry_id
    ]
    if not matched:
        _LOGGER.warning(
            "Thermal calibration reset requested for unknown entry_id=%s",
            requested_entry_id,
        )
        return

    for entry in matched:
        runtime_data: HeatingCurveOptimizerData | None = getattr(
            entry, "runtime_data", None
        )
        if runtime_data is None:
            _LOGGER.warning(
                "Skipping thermal calibration reset for entry %s: "
                "runtime_data missing",
                entry.entry_id,
            )
            continue
        await runtime_data.optimization_coordinator.async_reset_thermal_calibration()


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    if hass.services.has_service(DOMAIN, SERVICE_RESET_THERMAL_CALIBRATION):
        return

    async def _handle(call: ServiceCall) -> None:
        await _async_handle_reset_thermal_calibration(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_THERMAL_CALIBRATION,
        _handle,
        schema=_SERVICE_RESET_SCHEMA,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry by forwarding to sensor & number platforms."""
    _LOGGER.info("Setting up entry %s", entry.entry_id)

    # Ensure DOMAIN exists in hass.data
    hass.data.setdefault(DOMAIN, {})

    # Merge options and data for configuration
    config = {**entry.data, **entry.options}

    # PV arrays, as config subentries (see const.py's PV_SUBENTRY_TYPE
    # comment) - each array's own peak_power_kwp/orientation/tilt/
    # efficiency_factor, read by HeatCalculationCoordinator._calculate_pv_
    # production. getattr guards HA releases old enough to predate
    # ConfigEntry.subentries entirely; such an install simply has no PV
    # arrays (config_flow.py's HeatingPvArraySubentryFlow isn't offered
    # there either), not a crash.
    config["pv_arrays"] = [
        dict(subentry.data)
        for subentry in getattr(entry, "subentries", {}).values()
        if getattr(subentry, "subentry_type", None) == PV_SUBENTRY_TYPE
    ]

    # Initialize coordinators
    _LOGGER.debug("Initializing coordinators for entry %s", entry.entry_id)

    # 1. Weather data coordinator (API calls to open-meteo)
    weather_coordinator = WeatherDataCoordinator(hass)
    await weather_coordinator.async_config_entry_first_refresh()

    # 2. Heat calculation coordinator (depends on weather coordinator)
    heat_coordinator = HeatCalculationCoordinator(
        hass, weather_coordinator, config, entry.entry_id
    )
    await heat_coordinator.async_setup()
    await heat_coordinator.async_config_entry_first_refresh()

    # 3. Optimization coordinator (depends on heat coordinator)
    optimization_coordinator = OptimizationCoordinator(
        hass, heat_coordinator, config, entry.entry_id
    )
    await optimization_coordinator.async_setup()

    # Trigger first optimization async (don't block startup)
    # This allows sensors to be available immediately while optimization runs in background
    async def _trigger_first_optimization() -> None:
        """Trigger first optimization after a short delay."""
        await asyncio.sleep(5)  # Give sensors time to initialize
        _LOGGER.info("Triggering first optimization run")
        await optimization_coordinator.async_request_refresh()

    hass.async_create_task(_trigger_first_optimization())

    # Create device info for all entities
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heating Curve Optimizer",
        manufacturer="Custom",
        model="Dynamic Heating Optimizer",
        sw_version=_MANIFEST.get("version", "unknown"),
    )

    # 4. Additional heating-zone subentries (phase 5c, REDESIGN.md), modelled
    # on battery_controller's per-battery/per-PV-array subentry devices. Each
    # zone gets its own HeatCalculationCoordinator/OptimizationCoordinator
    # pair and device, sharing the main entry's price sensor, heating curve
    # limits and heat pump parameters (only area/energy label/indoor sensor/
    # target temperature are per-zone - see ZONE_SUBENTRY_TYPE's comment).
    zones: dict[str, dict[str, Any]] = {}
    # getattr guards HA releases old enough to predate ConfigEntry.subentries
    # entirely (see config_flow.py's HeatingZoneSubentryFlow comment) -
    # setup must not fail for installations with no zones configured just
    # because their HA is too old to have the attribute at all.
    for subentry_id, subentry in getattr(entry, "subentries", {}).items():
        if subentry.subentry_type != ZONE_SUBENTRY_TYPE:
            continue

        zone_config = {**config, **subentry.data}
        zone_entry_id = f"{entry.entry_id}_{subentry_id}"

        zone_heat_coordinator = HeatCalculationCoordinator(
            hass, weather_coordinator, zone_config, zone_entry_id
        )
        await zone_heat_coordinator.async_setup()
        await zone_heat_coordinator.async_config_entry_first_refresh()

        zone_optimization_coordinator = OptimizationCoordinator(
            hass, zone_heat_coordinator, zone_config, zone_entry_id
        )
        await zone_optimization_coordinator.async_setup()

        zone_device = DeviceInfo(
            identifiers={(DOMAIN, zone_entry_id)},
            name=subentry.title,
            manufacturer="Custom",
            model="Heating Zone",
            sw_version=_MANIFEST.get("version", "unknown"),
            via_device=(DOMAIN, entry.entry_id),
        )

        zones[subentry_id] = {
            "heat_coordinator": zone_heat_coordinator,
            "optimization_coordinator": zone_optimization_coordinator,
            "config": zone_config,
            "device": zone_device,
            "name": subentry.title,
        }

        async def _trigger_zone_first_optimization(
            coordinator: OptimizationCoordinator = zone_optimization_coordinator,
            zone_name: str = subentry.title,
        ) -> None:
            """Trigger a zone's first optimization after a short delay."""
            await asyncio.sleep(5)
            _LOGGER.info("Triggering first optimization run for zone %s", zone_name)
            await coordinator.async_request_refresh()

        hass.async_create_task(_trigger_zone_first_optimization())

    # 5. Hybrid gas-boiler cost comparison (optional, singleton subentry -
    # see config_flow.py's HeatingGasBoilerSubentryFlow). Whole-house scope
    # (not per zone - one physical boiler), fully additive: only reads the
    # main entry's heat/optimization coordinators, never instantiated at
    # all without the subentry, so this is zero-impact for every
    # installation that hasn't configured it.
    gas_boiler_coordinator: GasBoilerCoordinator | None = None
    gas_boiler_device: DeviceInfo | None = None
    gas_boiler_subentry_id: str | None = None
    gas_boiler_subentries = [
        (subentry_id, subentry)
        for subentry_id, subentry in getattr(entry, "subentries", {}).items()
        if subentry.subentry_type == GAS_SUBENTRY_TYPE
    ]
    if gas_boiler_subentries:
        if len(gas_boiler_subentries) > 1:
            # The config_flow singleton guard should prevent this, but
            # never crash setup over a bypassed guarantee - just use the
            # first one and ignore the rest.
            _LOGGER.warning(
                "Multiple gas_boiler subentries found on entry %s; using the "
                "first one",
                entry.entry_id,
            )
        gas_boiler_subentry_id, gas_boiler_subentry = gas_boiler_subentries[0]
        gas_boiler_config = {**config, **gas_boiler_subentry.data}

        new_gas_boiler_coordinator = GasBoilerCoordinator(
            hass,
            heat_coordinator,
            optimization_coordinator,
            gas_boiler_config,
            entry.entry_id,
        )
        await new_gas_boiler_coordinator.async_setup()
        gas_boiler_coordinator = new_gas_boiler_coordinator

        gas_boiler_device = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_gas_boiler")},
            name=gas_boiler_subentry.title,
            manufacturer="Custom",
            model="Hybrid Gas Boiler",
            sw_version=_MANIFEST.get("version", "unknown"),
            via_device=(DOMAIN, entry.entry_id),
        )

        async def _trigger_gas_boiler_first_refresh(
            coordinator: GasBoilerCoordinator = new_gas_boiler_coordinator,
        ) -> None:
            """Trigger the first comparison after a short delay.

            Deliberately not async_config_entry_first_refresh(): a broken
            or missing gas price sensor must never block the whole
            integration's setup via ConfigEntryNotReady - this is an
            optional add-on, not a required dependency.
            """
            await asyncio.sleep(5)
            _LOGGER.info("Triggering first gas boiler comparison run")
            await coordinator.async_request_refresh()

        hass.async_create_task(_trigger_gas_boiler_first_refresh())

    # Store coordinators and config on the entry itself (quality_scale's
    # runtime-data rule) rather than in hass.data[DOMAIN][entry.entry_id].
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

    _LOGGER.debug("Coordinators initialized successfully")

    _async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    # Forward entry to ALL our platforms in one call:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Forwarded entry %s to platforms %s", entry.entry_id, PLATFORMS)
    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle an options update by reloading the config entry.

    `entry.runtime_data.options` is the snapshot taken at setup, never
    mutated afterwards: a reload always rebuilds it fresh, so there is
    nothing to keep in sync between reloads. `entry.runtime_data` is only
    unset for an entry whose setup never finished (e.g. it errored before
    reaching the end of `async_setup_entry`) - nothing to reload-guard for
    in that case either.
    """
    runtime_data: HeatingCurveOptimizerData | None = getattr(
        entry, "runtime_data", None
    )
    if runtime_data is None:
        return

    if runtime_data.options == dict(entry.options):
        _LOGGER.debug("Entry %s options unchanged - no reload needed", entry.entry_id)
        return

    _LOGGER.debug("Reloading config entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and its platforms."""
    _LOGGER.info("Unloading entry %s", entry.entry_id)

    # Shutdown coordinators
    runtime_data: HeatingCurveOptimizerData | None = getattr(
        entry, "runtime_data", None
    )
    if runtime_data is not None:
        await runtime_data.heat_coordinator.async_shutdown()
        await runtime_data.optimization_coordinator.async_shutdown()

        for zone_data in runtime_data.zones.values():
            zone_heat_coordinator = zone_data.get("heat_coordinator")
            if zone_heat_coordinator:
                await zone_heat_coordinator.async_shutdown()
            zone_optimization_coordinator = zone_data.get("optimization_coordinator")
            if zone_optimization_coordinator:
                await zone_optimization_coordinator.async_shutdown()

        if runtime_data.gas_boiler_coordinator is not None:
            await runtime_data.gas_boiler_coordinator.async_shutdown()

    unload_ok = bool(await hass.config_entries.async_unload_platforms(entry, PLATFORMS))
    if unload_ok:
        runtime = hass.data.get(DOMAIN, {}).get("runtime")
        if runtime and entry.entry_id in runtime:
            runtime.pop(entry.entry_id, None)
            if not runtime:
                hass.data[DOMAIN].pop("runtime")
        _LOGGER.debug("Successfully unloaded entry %s", entry.entry_id)
        if DOMAIN in hass.data and not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    else:
        _LOGGER.warning("Failed to unload entry %s", entry.entry_id)
    return unload_ok
