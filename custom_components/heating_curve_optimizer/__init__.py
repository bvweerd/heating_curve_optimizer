# custom_components/heating_curve_optimizer/__init__.py

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_TARGET_INDOOR_TEMP,
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

# Keys stored in entry.options by number entities that do NOT require a full
# reload of the integration when they change.  Everything else (sensor IDs,
# building parameters, timing parameters) triggers a reload so the
# coordinators are re-initialised with the new structural configuration.
_NO_RELOAD_KEYS = frozenset(
    {
        CONF_TARGET_INDOOR_TEMP,
        CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
        CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    }
)


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
    # None when no heating-zone subentry exists yet (see
    # _find_primary_zone_subentry) - a valid, if useless, state matching
    # battery_controller's own "zero batteries configured" precedent. Every
    # zone, including the first, is now a subentry; the primary (first,
    # oldest) one drives these two coordinators and keeps this entry's own
    # identity (entry.entry_id) rather than a zone-suffixed one, so nothing
    # downstream (unique_ids, calibration storage keys, number.py, climate.py)
    # needs to change just because that data now comes from a subentry.
    heat_coordinator: HeatCalculationCoordinator | None
    optimization_coordinator: OptimizationCoordinator | None
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
        if runtime_data.optimization_coordinator is None:
            _LOGGER.warning(
                "Skipping thermal calibration reset for entry %s: "
                "no primary heating zone configured yet",
                entry.entry_id,
            )
            continue
        await runtime_data.optimization_coordinator.async_reset_thermal_calibration()


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


def _find_primary_zone_subentry(entry: ConfigEntry) -> tuple[str, Any] | None:
    """The first (oldest, iteration-order) heating-zone subentry, or None.

    Every heating zone, including what used to be the implicit "zone 1"
    baked into the main entry's own data, is now a ZONE_SUBENTRY_TYPE
    subentry - see config_flow.py's HeatingZoneSubentryFlow. The primary
    zone is the one whose data feeds the entry's own (non-zone-suffixed)
    HeatCalculationCoordinator/OptimizationCoordinator pair; any other
    zone subentry goes through the ordinary "extra zone" loop below,
    unchanged. If a user later deletes the primary zone, whichever
    subentry is now first silently becomes primary on the next reload -
    an accepted limitation, not solved by a migration (none exists; see
    docs/redesign/REDESIGN.md and this integration's "no deployment yet"
    development stance).
    """
    for subentry_id, subentry in getattr(entry, "subentries", {}).items():
        if subentry.subentry_type == ZONE_SUBENTRY_TYPE:
            return subentry_id, subentry
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry by forwarding to sensor & number platforms."""
    _LOGGER.info("Setting up entry %s", entry.entry_id)

    # Register services early, before any coordinator setup, so they are
    # available even if setup subsequently fails.
    _async_register_services(hass)

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

    # 1. Weather data coordinator (API calls to open-meteo) - shared by
    # every zone, including the primary one, regardless of whether any
    # zone is configured yet.
    weather_coordinator = WeatherDataCoordinator(hass, config_entry=entry)
    await weather_coordinator.async_config_entry_first_refresh()

    # Create device info for all entities. Built unconditionally, even with
    # zero zones configured yet, so the integration has a device page the
    # user can add their first heating zone from.
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Heating Curve Optimizer",
        manufacturer="Custom",
        model="Dynamic Heating Optimizer",
        sw_version=_MANIFEST.get("version", "unknown"),
    )

    # 2/3. Primary heating-zone coordinators (see _find_primary_zone_subentry).
    # Every zone, including this one, is a ZONE_SUBENTRY_TYPE subentry now -
    # the primary zone's data is merged in on top of the shared config, the
    # same way every "extra" zone's is, but it keeps the entry's own
    # (non-zone-suffixed) identity so unique_ids/calibration storage/device
    # stay exactly as they were before this zone lived in a subentry.
    # None/None when no zone subentry exists yet - a valid, if useless,
    # state (matches battery_controller's own "zero batteries" precedent):
    # every platform that depends on these must handle None gracefully.
    heat_coordinator: HeatCalculationCoordinator | None = None
    optimization_coordinator: OptimizationCoordinator | None = None
    primary = _find_primary_zone_subentry(entry)
    primary_subentry_id: str | None = None
    if primary is not None:
        primary_subentry_id, primary_subentry = primary
        primary_config = {**config, **primary_subentry.data}

        heat_coordinator = HeatCalculationCoordinator(
            hass,
            weather_coordinator,
            primary_config,
            entry.entry_id,
            config_entry=entry,
        )
        await heat_coordinator.async_setup()
        await heat_coordinator.async_config_entry_first_refresh()

        optimization_coordinator = OptimizationCoordinator(
            hass,
            heat_coordinator,
            primary_config,
            entry.entry_id,
            config_entry=entry,
        )
        await optimization_coordinator.async_setup()
        primary_optimization_coordinator: OptimizationCoordinator = (
            optimization_coordinator
        )

        # Trigger first optimization async (don't block startup) - allows
        # sensors to be available immediately while optimization runs in
        # background.
        async def _trigger_first_optimization(
            coordinator: OptimizationCoordinator = primary_optimization_coordinator,
        ) -> None:
            """Trigger first optimization after a short delay."""
            await asyncio.sleep(5)  # Give sensors time to initialize
            _LOGGER.info("Triggering first optimization run")
            await coordinator.async_request_refresh()

        hass.async_create_task(_trigger_first_optimization())

    # 4. Additional heating-zone subentries (phase 5c, REDESIGN.md), modelled
    # on battery_controller's per-battery/per-PV-array subentry devices. Each
    # zone gets its own HeatCalculationCoordinator/OptimizationCoordinator
    # pair and device, sharing the main entry's price sensor, heating curve
    # limits and heat pump parameters (only area/energy label/envelope/
    # indoor sensor/target temperature are per-zone - see
    # ZONE_SUBENTRY_TYPE's comment). The primary zone (above) is excluded
    # here - it already has its own coordinator pair under the entry's own
    # identity, not a zone-suffixed one.
    zones: dict[str, dict[str, Any]] = {}
    # Look up the parent device's registry ID for via_device_id on child
    # devices (zones, gas boiler). The parent device was registered above
    # via its DeviceInfo identifiers; dr.async_get resolves the tuple to
    # the actual device-registry ID string that via_device_id expects.
    dev_reg = dr.async_get(hass)
    parent_device = dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    parent_device_id = parent_device.id if parent_device else None
    # getattr guards HA releases old enough to predate ConfigEntry.subentries
    # entirely (see config_flow.py's HeatingZoneSubentryFlow comment) -
    # setup must not fail for installations with no zones configured just
    # because their HA is too old to have the attribute at all.
    for subentry_id, subentry in getattr(entry, "subentries", {}).items():
        if subentry.subentry_type != ZONE_SUBENTRY_TYPE:
            continue
        if subentry_id == primary_subentry_id:
            continue

        zone_config = {**config, **subentry.data}
        zone_entry_id = f"{entry.entry_id}_{subentry_id}"

        zone_heat_coordinator = HeatCalculationCoordinator(
            hass,
            weather_coordinator,
            zone_config,
            zone_entry_id,
            config_entry=entry,
        )
        await zone_heat_coordinator.async_setup()
        await zone_heat_coordinator.async_config_entry_first_refresh()

        zone_optimization_coordinator = OptimizationCoordinator(
            hass,
            zone_heat_coordinator,
            zone_config,
            zone_entry_id,
            config_entry=entry,
        )
        await zone_optimization_coordinator.async_setup()

        zone_device = DeviceInfo(
            identifiers={(DOMAIN, zone_entry_id)},
            name=subentry.title,
            manufacturer="Custom",
            model="Heating Zone",
            sw_version=_MANIFEST.get("version", "unknown"),
            **({"via_device_id": parent_device_id} if parent_device_id else {}),
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
    # primary zone's heat/optimization coordinators, never instantiated at
    # all without the subentry, so this is zero-impact for every
    # installation that hasn't configured it. Also skipped entirely when no
    # primary zone exists yet - there is nothing for it to compare against.
    gas_boiler_coordinator: GasBoilerCoordinator | None = None
    gas_boiler_device: DeviceInfo | None = None
    gas_boiler_subentry_id: str | None = None
    gas_boiler_subentries = [
        (subentry_id, subentry)
        for subentry_id, subentry in getattr(entry, "subentries", {}).items()
        if subentry.subentry_type == GAS_SUBENTRY_TYPE
    ]
    if (
        gas_boiler_subentries
        and heat_coordinator is not None
        and (optimization_coordinator is not None)
    ):
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
            config_entry=entry,
        )
        await new_gas_boiler_coordinator.async_setup()
        gas_boiler_coordinator = new_gas_boiler_coordinator

        gas_boiler_device = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_gas_boiler")},
            name=gas_boiler_subentry.title,
            manufacturer="Custom",
            model="Hybrid Gas Boiler",
            sw_version=_MANIFEST.get("version", "unknown"),
            **({"via_device_id": parent_device_id} if parent_device_id else {}),
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

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    # Forward entry to ALL our platforms in one call:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Forwarded entry %s to platforms %s", entry.entry_id, PLATFORMS)
    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle an options update, reloading only when structural keys change.

    `entry.runtime_data.options` is the snapshot taken at setup, never
    mutated afterwards: a reload always rebuilds it fresh, so there is
    nothing to keep in sync between reloads. `entry.runtime_data` is only
    unset for an entry whose setup never finished (e.g. it errored before
    reaching the end of `async_setup_entry`) - nothing to reload-guard for
    in that case either.

    Keys in ``_NO_RELOAD_KEYS`` (target temperature, hysteresis) are
    written live by number entities and don't need a coordinator restart.
    """
    runtime_data: HeatingCurveOptimizerData | None = getattr(
        entry, "runtime_data", None
    )
    if runtime_data is None:
        return

    old_options = runtime_data.options
    new_options = dict(entry.options)

    if old_options == new_options:
        _LOGGER.debug("Entry %s options unchanged - no reload needed", entry.entry_id)
        return

    changed_keys = {
        k
        for k in set(old_options) | set(new_options)
        if old_options.get(k) != new_options.get(k)
    }
    if changed_keys and changed_keys.issubset(_NO_RELOAD_KEYS):
        _LOGGER.debug(
            "Entry %s: only no-reload keys changed (%s) - skipping reload",
            entry.entry_id,
            changed_keys,
        )
        return

    _LOGGER.debug("Reloading config entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing stale subentry devices from the device registry.

    Mirrors battery_controller's implementation: returns False for the main
    device and for devices whose subentry is still active, True for stale
    devices left behind by deleted subentries.
    """
    for identifier in device_entry.identifiers:
        if identifier[0] != DOMAIN:
            continue
        device_id = identifier[1]
        if device_id == config_entry.entry_id:
            return False  # Main device — cannot remove while integration is active
        # Strip zone-entry-id suffix (f"{entry_id}_{subentry_id}") back to
        # subentry_id by checking all active subentries.
        for subentry_id in getattr(config_entry, "subentries", {}):
            if device_id == f"{config_entry.entry_id}_{subentry_id}":
                return False  # Subentry device still active
            if device_id == subentry_id:
                return False
        # Gas boiler device uses f"{entry_id}_gas_boiler" as identifier.
        if device_id == f"{config_entry.entry_id}_gas_boiler":
            for subentry in getattr(config_entry, "subentries", {}).values():
                if getattr(subentry, "subentry_type", None) == GAS_SUBENTRY_TYPE:
                    return False
    return True  # Stale device, allow removal


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and its platforms."""
    _LOGGER.info("Unloading entry %s", entry.entry_id)

    # Shutdown coordinators
    runtime_data: HeatingCurveOptimizerData | None = getattr(
        entry, "runtime_data", None
    )
    if runtime_data is not None:
        if runtime_data.heat_coordinator is not None:
            try:
                await runtime_data.heat_coordinator.async_shutdown()
            except Exception:
                _LOGGER.exception(
                    "Error shutting down heat coordinator for %s", entry.entry_id
                )
        if runtime_data.optimization_coordinator is not None:
            try:
                await runtime_data.optimization_coordinator.async_shutdown()
            except Exception:
                _LOGGER.exception(
                    "Error shutting down optimization coordinator for %s",
                    entry.entry_id,
                )

        for zone_data in runtime_data.zones.values():
            zone_heat_coordinator = zone_data.get("heat_coordinator")
            if zone_heat_coordinator:
                try:
                    await zone_heat_coordinator.async_shutdown()
                except Exception:
                    _LOGGER.exception(
                        "Error shutting down zone heat coordinator for %s",
                        entry.entry_id,
                    )
            zone_optimization_coordinator = zone_data.get("optimization_coordinator")
            if zone_optimization_coordinator:
                try:
                    await zone_optimization_coordinator.async_shutdown()
                except Exception:
                    _LOGGER.exception(
                        "Error shutting down zone optimization coordinator for %s",
                        entry.entry_id,
                    )

        if runtime_data.gas_boiler_coordinator is not None:
            try:
                await runtime_data.gas_boiler_coordinator.async_shutdown()
            except Exception:
                _LOGGER.exception(
                    "Error shutting down gas boiler coordinator for %s",
                    entry.entry_id,
                )

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

        # Remove services when the last entry for this domain is unloaded.
        remaining = hass.config_entries.async_entries(DOMAIN)
        if not remaining:
            if hass.services.has_service(DOMAIN, SERVICE_RESET_THERMAL_CALIBRATION):
                hass.services.async_remove(DOMAIN, SERVICE_RESET_THERMAL_CALIBRATION)
    else:
        _LOGGER.warning("Failed to unload entry %s", entry.entry_id)
    return unload_ok
