from __future__ import annotations

import copy
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant import config_entries

try:
    from homeassistant.config_entries import (
        ConfigFlowContext,
        ConfigFlowResult,
        SubentryFlowResult,
    )
except ImportError:
    # For older versions of Home Assistant that don't have these types
    ConfigFlowContext = dict
    ConfigFlowResult = dict[str, Any]
    SubentryFlowResult = dict[str, Any]
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import selector
import voluptuous as vol

# homeassistant.data_entry_flow.section() (single-screen collapsible field
# groups, used by battery_controller's config_flow.py) landed in HA well
# after this integration's floor version, and does not exist at all in the
# HA release this repo's test environment can install (2024.3.x - a
# package-index ceiling, not a real HA release date; confirmed by direct
# import attempt). Same graceful-degradation policy as _ConfigSubentryFlow
# below: the modern single-page sectioned form is only used when `section`
# is importable, otherwise async_step_user falls through to today's
# multi-step wizard, completely unchanged - see _build_sectioned_schema.
try:
    from homeassistant.data_entry_flow import section as _section
except ImportError:
    _section = None

from .companion_integrations import (
    DetectedPvArray,
    DetectedSensor,
    DetectedSensorList,
    FIELD_SOURCES_CONSUMPTION,
    FIELD_SOURCES_PRODUCTION,
    detect_gas_price_sensor,
    detect_main_flow_sensors,
    detect_pv_arrays,
    detect_source_sensors,
)
from .const import (
    CONF_AREA_M2,
    CONF_CONFIGS,
    CONF_ENERGY_LABEL,
    CONF_GLASS_EAST_M2,
    CONF_GLASS_SOUTH_M2,
    CONF_GLASS_U_VALUE,
    CONF_GLASS_WEST_M2,
    CONF_INDOOR_TEMPERATURE_SENSOR,
    CONF_INDOOR_TEMP_HYSTERESIS,
    CONF_OFFSET_DELTA_T,
    CONF_PLANNING_WINDOW,
    CONF_TARGET_INDOOR_TEMP,
    CONF_TIME_BASE,
    CONF_POWER_CONSUMPTION,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_K_FACTOR,
    CONF_BASE_COP,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEATING_CURVE_OFFSET,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MAX,
    CONF_PRICE_SENSOR,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_PRICE_SETTINGS,
    CONF_PV_PEAK_POWER_KWP,
    CONF_PV_ORIENTATION,
    CONF_PV_TILT,
    CONF_PV_EFFICIENCY_FACTOR,
    CONF_PV_DC_COUPLED,
    CONF_VENTILATION_TYPE,
    CONF_CEILING_HEIGHT,
    CONF_THERMAL_MASS_CLASS,
    CONF_EMITTER_TYPE,
    DEFAULT_INDOOR_TEMP_HYSTERESIS,
    DEFAULT_K_FACTOR,
    DEFAULT_OFFSET_DELTA_T,
    DEFAULT_PV_TILT,
    DEFAULT_PV_ORIENTATION_DEG,
    DEFAULT_PV_EFFICIENCY_FACTOR,
    DEFAULT_COP_AT_35,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_PLANNING_WINDOW,
    DEFAULT_TARGET_INDOOR_TEMP,
    DEFAULT_TIME_BASE,
    DEFAULT_HEATING_CURVE_OFFSET,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_VENTILATION_TYPE,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_THERMAL_MASS_CLASS,
    DEFAULT_EMITTER_TYPE,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    DOMAIN,
    ENERGY_LABELS,
    SOURCE_TYPES,
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
    VENTILATION_TYPES,
    THERMAL_MASS_WH_PER_M2_K,
    EMITTER_EXPONENT_MAP,
    ZONE_SUBENTRY_TYPE,
    GAS_SUBENTRY_TYPE,
    PV_SUBENTRY_TYPE,
    CONF_GAS_PRICE_SENSOR,
    CONF_GAS_BOILER_EFFICIENCY,
    CONF_GAS_CALORIFIC_VALUE,
    DEFAULT_GAS_BOILER_EFFICIENCY,
    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
)

STEP_SELECT_SOURCES = "select_sources"
STEP_PRICE_SETTINGS = "price_settings"
STEP_BASIC = "basic"
STEP_HEATING_CURVE_SETTINGS = "heating_curve_settings"


async def _test_api_connection(hass: HomeAssistant) -> str | None:
    """Test reachability of open-meteo.com before creating the entry.

    Every sensor this integration publishes ultimately depends on weather
    data from this API (WeatherDataCoordinator) - creating an entry that
    can never successfully refresh is a worse experience than catching it
    here, at config-flow time. Ported from battery_controller's
    config_flow.py (docs/redesign/REDESIGN.md), which checks the same API
    for the same reason. Returns an error key for async_show_form, or None
    on success.
    """
    session = async_get_clientsession(hass)
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(
        {
            "latitude": hass.config.latitude,
            "longitude": hass.config.longitude,
            "hourly": "shortwave_radiation",
            "forecast_days": "1",
        }
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return "cannot_connect"
    except (aiohttp.ClientError, TimeoutError):
        return "cannot_connect"
    return None


def _build_zone_subentry_schema(
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the schema for a heating-zone subentry (phase 5c, REDESIGN.md).

    Deliberately narrow: only what plausibly differs *between rooms* in the
    same home (floor area, insulation, its own thermostat, its own target
    temperature). Price sensor, heating curve limits and heat pump
    parameters are shared from the main entry - see const.py's
    ZONE_SUBENTRY_TYPE comment.
    """
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required("name", default=defaults.get("name")): str,
            vol.Required(CONF_AREA_M2, default=defaults.get(CONF_AREA_M2)): vol.Coerce(
                float
            ),
            vol.Required(
                CONF_ENERGY_LABEL, default=defaults.get(CONF_ENERGY_LABEL)
            ): selector(
                {
                    "select": {
                        "options": ENERGY_LABELS,
                        "mode": "dropdown",
                        "custom_value": False,
                    }
                }
            ),
            vol.Optional(
                CONF_TARGET_INDOOR_TEMP,
                default=defaults.get(
                    CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP
                ),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_INDOOR_TEMPERATURE_SENSOR,
                default=defaults.get(CONF_INDOOR_TEMPERATURE_SENSOR),
            ): selector(
                {"entity": {"domain": "sensor", "device_class": "temperature"}}
            ),
        }
    )


def _validate_zone_subentry(user_input: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a heating-zone subentry's input."""
    name = str(user_input["name"]).strip()
    if not name:
        raise vol.Invalid("name_required")
    area_m2 = float(user_input[CONF_AREA_M2])
    if area_m2 <= 0:
        raise vol.Invalid("area_must_be_positive")
    return {
        "name": name,
        CONF_AREA_M2: area_m2,
        CONF_ENERGY_LABEL: user_input[CONF_ENERGY_LABEL],
        CONF_TARGET_INDOOR_TEMP: float(
            user_input.get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP)
        ),
        CONF_INDOOR_TEMPERATURE_SENSOR: user_input.get(CONF_INDOOR_TEMPERATURE_SENSOR),
    }


# ConfigSubentryFlow (and ConfigEntry.subentries) landed in HA well after
# this integration's floor version, and is not available at all in the HA
# release this repo's test environment can install (2024.3.x - a package-
# index ceiling, not a real HA release date). Rather than hard-depend on
# it and break setup entirely on any HA old enough to lack it, the zone-
# subentry feature (phase 5c, REDESIGN.md) degrades to simply not being
# offered: HeatingZoneSubentryFlow is only defined, and only registered in
# async_get_supported_subentry_types below, when the base class exists.
_ConfigSubentryFlow = getattr(config_entries, "ConfigSubentryFlow", None)

if _ConfigSubentryFlow is not None:

    class HeatingZoneSubentryFlow(_ConfigSubentryFlow):  # type: ignore[misc, valid-type]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
        """Flow for adding or editing an additional heating-zone subentry.

        Modelled directly on battery_controller's
        BatteryControllerBatterySubentryFlow / BatteryControllerPVSubentryFlow
        (docs/redesign/REDESIGN.md phase 5c) - verified against a live,
        running battery_controller install's subentries (config_entry
        diagnostics shared 2026-09-22), since this integration's own test
        environment cannot install an HA release new enough to exercise
        this class at all (see the comment above).
        """

        async def async_step_user(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle adding a new heating zone."""
            errors: dict[str, str] = {}
            if user_input is not None:
                try:
                    data = _validate_zone_subentry(user_input)
                except vol.Invalid:
                    errors["base"] = "invalid_zone_input"
                else:
                    return self.async_create_entry(title=data["name"], data=data)
            return self.async_show_form(
                step_id="user",
                data_schema=_build_zone_subentry_schema(),
                errors=errors,
            )

        async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle editing an existing heating zone."""
            errors: dict[str, str] = {}
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            current_data = dict(subentry.data)

            if user_input is not None:
                try:
                    data = _validate_zone_subentry(user_input)
                except vol.Invalid:
                    errors["base"] = "invalid_zone_input"
                else:
                    return self.async_update_and_abort(
                        entry, subentry, title=data["name"], data=data
                    )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_build_zone_subentry_schema(current_data),
                errors=errors,
            )

else:
    HeatingZoneSubentryFlow = None  # type: ignore[assignment,misc]


def _build_gas_boiler_subentry_schema(
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the schema for the (singleton) hybrid gas-boiler subentry.

    A plain entity selector, not a dropdown filtered by device_class, for
    the gas price sensor: Dutch dynamic-gas-price sensors don't reliably
    carry device_class="monetary" the way electricity price sensors often
    do.
    """
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_GAS_PRICE_SENSOR, default=defaults.get(CONF_GAS_PRICE_SENSOR)
            ): selector({"entity": {"domain": "sensor"}}),
            vol.Optional(
                CONF_GAS_BOILER_EFFICIENCY,
                default=defaults.get(
                    CONF_GAS_BOILER_EFFICIENCY, DEFAULT_GAS_BOILER_EFFICIENCY
                ),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_GAS_CALORIFIC_VALUE,
                default=defaults.get(
                    CONF_GAS_CALORIFIC_VALUE,
                    DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3,
                ),
            ): vol.Coerce(float),
        }
    )


def _validate_gas_boiler_subentry(user_input: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the gas-boiler subentry's input."""
    gas_price_sensor = user_input.get(CONF_GAS_PRICE_SENSOR)
    if not gas_price_sensor:
        raise vol.Invalid("gas_price_sensor_required")
    efficiency = float(
        user_input.get(CONF_GAS_BOILER_EFFICIENCY, DEFAULT_GAS_BOILER_EFFICIENCY)
    )
    if not (0.5 <= efficiency <= 1.2):
        raise vol.Invalid("efficiency_out_of_range")
    calorific_value = float(
        user_input.get(CONF_GAS_CALORIFIC_VALUE, DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3)
    )
    if calorific_value <= 0:
        raise vol.Invalid("calorific_value_must_be_positive")
    return {
        CONF_GAS_PRICE_SENSOR: gas_price_sensor,
        CONF_GAS_BOILER_EFFICIENCY: efficiency,
        CONF_GAS_CALORIFIC_VALUE: calorific_value,
    }


if _ConfigSubentryFlow is not None:

    class HeatingGasBoilerSubentryFlow(_ConfigSubentryFlow):  # type: ignore[misc, valid-type]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
        """Flow for adding or editing the (singleton) hybrid gas-boiler subentry.

        Unlike HeatingZoneSubentryFlow (zero-to-many), a hybrid system has
        exactly one physical gas boiler - async_step_user aborts before
        showing the form if one is already configured, the same
        application-level singleton pattern HA's own single-instance
        integrations use for the main entry itself
        (_abort_if_unique_id_configured), just applied at the subentry
        level since ConfigSubentryFlow has no built-in cap.
        """

        async def async_step_user(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle adding the gas boiler subentry."""
            entry = self._get_entry()
            existing = getattr(entry, "subentries", {})
            if any(sub.subentry_type == GAS_SUBENTRY_TYPE for sub in existing.values()):
                return self.async_abort(reason="single_instance_allowed")

            errors: dict[str, str] = {}
            if user_input is not None:
                try:
                    data = _validate_gas_boiler_subentry(user_input)
                except vol.Invalid as err:
                    errors["base"] = (
                        str(err.error_message) or "invalid_gas_boiler_input"
                    )
                else:
                    return self.async_create_entry(title="Gas Boiler", data=data)

            defaults: dict[str, Any] = {}
            detected_gas_price = detect_gas_price_sensor(self.hass)
            if detected_gas_price is not None:
                defaults[CONF_GAS_PRICE_SENSOR] = detected_gas_price.entity_id
            return self.async_show_form(
                step_id="user",
                data_schema=_build_gas_boiler_subentry_schema(defaults),
                errors=errors,
            )

        async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle editing the gas boiler subentry."""
            errors: dict[str, str] = {}
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            current_data = dict(subentry.data)

            if user_input is not None:
                try:
                    data = _validate_gas_boiler_subentry(user_input)
                except vol.Invalid as err:
                    errors["base"] = (
                        str(err.error_message) or "invalid_gas_boiler_input"
                    )
                else:
                    return self.async_update_and_abort(
                        entry, subentry, title="Gas Boiler", data=data
                    )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_build_gas_boiler_subentry_schema(current_data),
                errors=errors,
            )

else:
    HeatingGasBoilerSubentryFlow = None  # type: ignore[assignment,misc]


def _build_pv_array_subentry_schema(
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the schema for a PV-array subentry.

    Field-for-field match with battery_controller's own
    `BatteryControllerPVSubentryFlow` (peak_power_kwp/orientation/tilt/
    efficiency_factor/dc_coupled) - see const.py's PV_SUBENTRY_TYPE
    comment for why - minus its two external-sensor-override fields
    (pv_forecast_sensors/pv_measured_production_sensor), a separate
    feature question this integration has no existing concept for.
    """
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional("name", default=defaults.get("name", "")): str,
            vol.Required(
                CONF_PV_PEAK_POWER_KWP,
                default=defaults.get(CONF_PV_PEAK_POWER_KWP, 1.0),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
            vol.Required(
                CONF_PV_ORIENTATION,
                default=defaults.get(CONF_PV_ORIENTATION, DEFAULT_PV_ORIENTATION_DEG),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=360)),
            vol.Required(
                CONF_PV_TILT,
                default=defaults.get(CONF_PV_TILT, DEFAULT_PV_TILT),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=90)),
            vol.Optional(
                CONF_PV_EFFICIENCY_FACTOR,
                default=defaults.get(
                    CONF_PV_EFFICIENCY_FACTOR, DEFAULT_PV_EFFICIENCY_FACTOR
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=1.0)),
            vol.Optional(
                CONF_PV_DC_COUPLED,
                default=defaults.get(CONF_PV_DC_COUPLED, False),
            ): bool,
        }
    )


def _validate_pv_array_subentry(user_input: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a PV-array subentry's input."""
    schema = _build_pv_array_subentry_schema()
    validated = schema(user_input)
    result: dict[str, Any] = {}
    if name := str(validated.get("name", "")).strip():
        result["name"] = name
    result.update(
        {
            CONF_PV_PEAK_POWER_KWP: float(validated[CONF_PV_PEAK_POWER_KWP]),
            CONF_PV_ORIENTATION: float(validated[CONF_PV_ORIENTATION]),
            CONF_PV_TILT: float(validated[CONF_PV_TILT]),
            CONF_PV_EFFICIENCY_FACTOR: float(
                validated.get(CONF_PV_EFFICIENCY_FACTOR, DEFAULT_PV_EFFICIENCY_FACTOR)
            ),
            CONF_PV_DC_COUPLED: bool(validated.get(CONF_PV_DC_COUPLED, False)),
        }
    )
    return result


def _pv_array_subentry_title(data: dict[str, Any]) -> str:
    """Generate a display title for a PV-array subentry."""
    if name := str(data.get("name", "")).strip():
        return name
    kwp = data[CONF_PV_PEAK_POWER_KWP]
    coupling = "DC" if data.get(CONF_PV_DC_COUPLED) else "AC"
    return f"{kwp} kWp {coupling}"


_PV_IMPORT_CHOICE_MANUAL = "manual"


def _pv_array_import_choice_label(index: int, array: DetectedPvArray) -> str:
    """Label for one detected battery_controller array in the import picker."""
    name = array.name or f"Array {index + 1}"
    return f"{name} ({array.peak_power_kwp:g} kWp, {array.source})"


def _build_pv_array_import_schema(detected: list[DetectedPvArray]) -> vol.Schema:
    """Build the schema for the import-choice picker shown before the
    actual PV-array form, when battery_controller has arrays configured.

    Only reached when `detected` is non-empty - see
    HeatingPvArraySubentryFlow.async_step_user, which skips straight to
    the manual form otherwise.
    """
    options = [{"value": _PV_IMPORT_CHOICE_MANUAL, "label": "Enter manually"}] + [
        {"value": str(i), "label": _pv_array_import_choice_label(i, array)}
        for i, array in enumerate(detected)
    ]
    return vol.Schema(
        {
            vol.Required("import_choice", default=_PV_IMPORT_CHOICE_MANUAL): selector(
                {"select": {"options": options, "mode": "list"}}
            ),
        }
    )


def _detected_pv_array_to_defaults(array: DetectedPvArray) -> dict[str, Any]:
    """Map a detected battery_controller array onto this integration's own
    PV-array subentry field names - a direct pass-through (see
    DetectedPvArray's docstring), except its `name` is deliberately not
    copied: the array is being added as a new, separate config here, and a
    battery_controller subentry title copied verbatim could read strangely
    once it's this integration's own title too (e.g. re-editing it later
    via async_step_reconfigure, which has no import step). The user can
    still type a name in the form the picker leads into."""
    return {
        CONF_PV_PEAK_POWER_KWP: array.peak_power_kwp,
        CONF_PV_ORIENTATION: array.orientation,
        CONF_PV_TILT: array.tilt,
        CONF_PV_EFFICIENCY_FACTOR: array.efficiency_factor,
        CONF_PV_DC_COUPLED: array.dc_coupled,
    }


if _ConfigSubentryFlow is not None:

    class HeatingPvArraySubentryFlow(_ConfigSubentryFlow):  # type: ignore[misc, valid-type]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
        """Flow for adding or editing a PV-array subentry.

        Zero-to-many, like HeatingZoneSubentryFlow - a home can have
        several arrays at different orientations, each its own subentry
        added via the integration page after setup, matching
        battery_controller's own flow exactly (see const.py's
        PV_SUBENTRY_TYPE comment).

        When battery_controller already has one or more PV-array
        subentries configured, async_step_user offers to import one
        instead of asking the user to re-enter peak power/orientation/tilt
        for an array that's already configured elsewhere - see
        detect_pv_arrays in companion_integrations.py. Import defaults
        still land in the same editable form async_step_configure shows
        for manual entry, so a user can tweak values before creating the
        subentry either way.
        """

        _pv_import_defaults: dict[str, Any] | None = None

        async def async_step_user(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Offer to import from battery_controller, or go straight to
            manual entry when nothing is available to import."""
            detected = detect_pv_arrays(self.hass)
            if not detected:
                return await self.async_step_configure()

            if user_input is not None:
                choice = user_input.get("import_choice", _PV_IMPORT_CHOICE_MANUAL)
                if choice == _PV_IMPORT_CHOICE_MANUAL:
                    self._pv_import_defaults = None
                else:
                    self._pv_import_defaults = _detected_pv_array_to_defaults(
                        detected[int(choice)]
                    )
                return await self.async_step_configure()

            return self.async_show_form(
                step_id="user",
                data_schema=_build_pv_array_import_schema(detected),
            )

        async def async_step_configure(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle adding a new PV array, possibly pre-filled from an
            import choice made in async_step_user."""
            errors: dict[str, str] = {}
            if user_input is not None:
                try:
                    data = _validate_pv_array_subentry(user_input)
                except vol.Invalid:
                    errors["base"] = "invalid_pv_array_input"
                else:
                    return self.async_create_entry(
                        title=_pv_array_subentry_title(data), data=data
                    )
            return self.async_show_form(
                step_id="configure",
                data_schema=_build_pv_array_subentry_schema(self._pv_import_defaults),
                errors=errors,
            )

        async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
        ) -> SubentryFlowResult:
            """Handle editing an existing PV array."""
            errors: dict[str, str] = {}
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            current_data = dict(subentry.data)

            if user_input is not None:
                try:
                    data = _validate_pv_array_subentry(user_input)
                except vol.Invalid:
                    errors["base"] = "invalid_pv_array_input"
                else:
                    return self.async_update_and_abort(
                        entry,
                        subentry,
                        title=_pv_array_subentry_title(data),
                        data=data,
                    )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_build_pv_array_subentry_schema(current_data),
                errors=errors,
            )

else:
    HeatingPvArraySubentryFlow = None  # type: ignore[assignment,misc]


def _extract_sectioned_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Flatten a single-page sectioned form submission (see
    `_build_sectioned_schema`) back into a flat dict keyed by the same
    strings as both the CONF_* constants and this flow's own `self.*`
    attribute names (a 1:1 naming convention already used throughout this
    file, e.g. `CONF_AREA_M2 == "area_m2" == self.area_m2`) - the caller
    applies it with `for key, value in flat.items(): setattr(self, key,
    value)`.

    Mirrors `_apply_basic_input`/`_apply_heating_curve_input`'s coercion
    and defaulting exactly, just reading from nested per-section dicts
    instead of one flat `user_input`. Pure function - no dependency on
    `_section`/HA version, so it's fully unit-testable regardless of
    whether `section` itself is importable on this environment's HA.

    `CONF_SOURCES`/`self.configs` is deliberately not included here - see
    `_build_configs_from_sources` - it needs both flattened source lists
    at once and has no single corresponding `self` attribute.
    """
    building = user_input.get("building", {})
    envelope = user_input.get("envelope", {})
    sensors = user_input.get("sensors", {})
    curve = user_input.get("heat_pump_and_curve", {})
    advanced = user_input.get("advanced", {})

    return {
        CONF_AREA_M2: float(building[CONF_AREA_M2]),
        CONF_ENERGY_LABEL: building[CONF_ENERGY_LABEL],
        CONF_CONSUMPTION_PRICE_SENSOR: building[CONF_CONSUMPTION_PRICE_SENSOR],
        CONF_PRODUCTION_PRICE_SENSOR: building[CONF_PRODUCTION_PRICE_SENSOR],
        CONF_GLASS_EAST_M2: float(envelope.get(CONF_GLASS_EAST_M2, 0)),
        CONF_GLASS_WEST_M2: float(envelope.get(CONF_GLASS_WEST_M2, 0)),
        CONF_GLASS_SOUTH_M2: float(envelope.get(CONF_GLASS_SOUTH_M2, 0)),
        CONF_GLASS_U_VALUE: float(envelope.get(CONF_GLASS_U_VALUE, 1.2)),
        CONF_VENTILATION_TYPE: envelope.get(
            CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE
        ),
        CONF_CEILING_HEIGHT: float(
            envelope.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)
        ),
        CONF_THERMAL_MASS_CLASS: envelope.get(
            CONF_THERMAL_MASS_CLASS, DEFAULT_THERMAL_MASS_CLASS
        ),
        CONF_EMITTER_TYPE: envelope.get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE),
        CONF_INDOOR_TEMPERATURE_SENSOR: sensors.get(CONF_INDOOR_TEMPERATURE_SENSOR),
        CONF_POWER_CONSUMPTION: sensors.get(CONF_POWER_CONSUMPTION),
        CONF_SUPPLY_TEMPERATURE_SENSOR: sensors.get(CONF_SUPPLY_TEMPERATURE_SENSOR),
        CONF_GRID_IMPORT_SENSOR: sensors.get(CONF_GRID_IMPORT_SENSOR),
        CONF_GRID_EXPORT_SENSOR: sensors.get(CONF_GRID_EXPORT_SENSOR),
        CONF_K_FACTOR: float(curve.get(CONF_K_FACTOR, DEFAULT_K_FACTOR)),
        CONF_BASE_COP: float(curve.get(CONF_BASE_COP, DEFAULT_COP_AT_35)),
        CONF_OUTDOOR_TEMP_COEFFICIENT: float(
            curve.get(CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT)
        ),
        CONF_COP_COMPENSATION_FACTOR: float(
            curve.get(CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR)
        ),
        CONF_HEAT_CURVE_MIN_OUTDOOR: float(
            curve.get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0)
        ),
        CONF_HEAT_CURVE_MAX_OUTDOOR: float(
            curve.get(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0)
        ),
        CONF_HEATING_CURVE_OFFSET: float(
            curve.get(CONF_HEATING_CURVE_OFFSET, DEFAULT_HEATING_CURVE_OFFSET)
        ),
        CONF_HEAT_CURVE_MIN: float(
            curve.get(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN)
        ),
        CONF_HEAT_CURVE_MAX: float(
            curve.get(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX)
        ),
        CONF_OFFSET_DELTA_T: int(
            curve.get(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T)
        ),
        CONF_PLANNING_WINDOW: int(
            advanced.get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW)
        ),
        CONF_TIME_BASE: int(advanced.get(CONF_TIME_BASE, DEFAULT_TIME_BASE)),
        CONF_TARGET_INDOOR_TEMP: float(
            advanced.get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP)
        ),
        CONF_INDOOR_TEMP_HYSTERESIS: float(
            advanced.get(CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS)
        ),
    }


def _build_configs_from_sources(
    consumption_entities: list[str], production_entities: list[str]
) -> list[dict[str, Any]]:
    """Build the `self.configs` list directly from the two flattened
    consumption/production multi-select fields - the one-shot equivalent
    of `_update_source_config`'s incremental, step-based building used by
    the legacy multi-step wizard.

    Only includes a block for a source_type that actually has entities,
    matching `_update_source_config`'s own behaviour of never storing an
    empty block - the "at least one source configured" validation still
    happens in the caller, same as the legacy flow's own "no_blocks" check.
    """
    configs: list[dict[str, Any]] = []
    if consumption_entities:
        configs.append(
            {
                CONF_SOURCE_TYPE: SOURCE_TYPE_CONSUMPTION,
                CONF_SOURCES: consumption_entities,
            }
        )
    if production_entities:
        configs.append(
            {
                CONF_SOURCE_TYPE: SOURCE_TYPE_PRODUCTION,
                CONF_SOURCES: production_entities,
            }
        )
    return configs


def _build_sectioned_schema(
    defaults: dict[str, Any],
    power_sensors: list[str],
    temp_sensors: list[str],
    price_sensors: list[str],
    energy_sensors: list[str],
) -> vol.Schema:
    """Build the single-page sectioned form (only called when `_section is
    not None`). Mirrors battery_controller's own `_build_main_schema`: one
    `vol.Schema` of `section(...)` groups, each built from a small per-group
    schema. Every field uses `description={"suggested_value": ...}` for
    prefill rather than `default=` - a pure UI hint that never silently
    substitutes a value into validation, unlike `default=` (which the
    legacy multi-step schemas below still use).
    """

    def sv(key: str, fallback: Any = None) -> dict[str, Any]:
        return {"suggested_value": defaults.get(key, fallback)}

    building_schema = vol.Schema(
        {
            vol.Required(CONF_AREA_M2, description=sv(CONF_AREA_M2)): vol.Coerce(float),
            vol.Required(
                CONF_ENERGY_LABEL, description=sv(CONF_ENERGY_LABEL)
            ): selector(
                {
                    "select": {
                        "options": ENERGY_LABELS,
                        "mode": "dropdown",
                        "custom_value": False,
                    }
                }
            ),
            vol.Required(
                CONF_CONSUMPTION_PRICE_SENSOR,
                description=sv(CONF_CONSUMPTION_PRICE_SENSOR),
            ): selector(
                {
                    "select": {
                        "options": price_sensors,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Required(
                CONF_PRODUCTION_PRICE_SENSOR,
                description=sv(CONF_PRODUCTION_PRICE_SENSOR),
            ): selector(
                {
                    "select": {
                        "options": price_sensors,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
        }
    )

    envelope_schema = vol.Schema(
        {
            vol.Optional(
                CONF_GLASS_EAST_M2, description=sv(CONF_GLASS_EAST_M2, 0.0)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_GLASS_WEST_M2, description=sv(CONF_GLASS_WEST_M2, 0.0)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_GLASS_SOUTH_M2, description=sv(CONF_GLASS_SOUTH_M2, 0.0)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_GLASS_U_VALUE, description=sv(CONF_GLASS_U_VALUE, 1.2)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_VENTILATION_TYPE,
                description=sv(CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE),
            ): selector(
                {
                    "select": {
                        "options": list(VENTILATION_TYPES.keys()),
                        "mode": "dropdown",
                        "translation_key": "ventilation_type",
                    }
                }
            ),
            vol.Optional(
                CONF_CEILING_HEIGHT,
                description=sv(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_THERMAL_MASS_CLASS,
                description=sv(CONF_THERMAL_MASS_CLASS, DEFAULT_THERMAL_MASS_CLASS),
            ): selector(
                {
                    "select": {
                        "options": list(THERMAL_MASS_WH_PER_M2_K.keys()),
                        "mode": "dropdown",
                        "translation_key": "thermal_mass_class",
                    }
                }
            ),
            vol.Optional(
                CONF_EMITTER_TYPE,
                description=sv(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE),
            ): selector(
                {
                    "select": {
                        "options": list(EMITTER_EXPONENT_MAP.keys()),
                        "mode": "dropdown",
                        "translation_key": "emitter_type",
                    }
                }
            ),
        }
    )

    sensors_schema = vol.Schema(
        {
            vol.Optional(
                FIELD_SOURCES_CONSUMPTION,
                description=sv(FIELD_SOURCES_CONSUMPTION, []),
            ): selector(
                {
                    "select": {
                        "options": energy_sensors,
                        "multiple": True,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                FIELD_SOURCES_PRODUCTION,
                description=sv(FIELD_SOURCES_PRODUCTION, []),
            ): selector(
                {
                    "select": {
                        "options": energy_sensors,
                        "multiple": True,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_INDOOR_TEMPERATURE_SENSOR,
                description=sv(CONF_INDOOR_TEMPERATURE_SENSOR),
            ): selector(
                {
                    "select": {
                        "options": temp_sensors,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_POWER_CONSUMPTION, description=sv(CONF_POWER_CONSUMPTION)
            ): selector(
                {
                    "select": {
                        "options": power_sensors,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_SUPPLY_TEMPERATURE_SENSOR,
                description=sv(CONF_SUPPLY_TEMPERATURE_SENSOR),
            ): selector(
                {
                    "select": {
                        "options": temp_sensors,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_GRID_IMPORT_SENSOR, description=sv(CONF_GRID_IMPORT_SENSOR)
            ): selector(
                {
                    "select": {
                        "options": power_sensors,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_GRID_EXPORT_SENSOR, description=sv(CONF_GRID_EXPORT_SENSOR)
            ): selector(
                {
                    "select": {
                        "options": power_sensors,
                        "multiple": False,
                        "mode": "dropdown",
                    }
                }
            ),
        }
    )

    curve_schema = vol.Schema(
        {
            vol.Optional(
                CONF_K_FACTOR, description=sv(CONF_K_FACTOR, DEFAULT_K_FACTOR)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_BASE_COP, description=sv(CONF_BASE_COP, DEFAULT_COP_AT_35)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_OUTDOOR_TEMP_COEFFICIENT,
                description=sv(
                    CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
                ),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_COP_COMPENSATION_FACTOR,
                description=sv(
                    CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
                ),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_HEAT_CURVE_MIN_OUTDOOR,
                description=sv(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_HEAT_CURVE_MAX_OUTDOOR,
                description=sv(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_HEATING_CURVE_OFFSET,
                description=sv(CONF_HEATING_CURVE_OFFSET, DEFAULT_HEATING_CURVE_OFFSET),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_HEAT_CURVE_MIN,
                description=sv(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_HEAT_CURVE_MAX,
                description=sv(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_OFFSET_DELTA_T,
                description=sv(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T),
            ): vol.Coerce(int),
        }
    )

    advanced_schema = vol.Schema(
        {
            vol.Optional(
                CONF_PLANNING_WINDOW,
                description=sv(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW),
            ): vol.Coerce(int),
            vol.Optional(
                CONF_TIME_BASE, description=sv(CONF_TIME_BASE, DEFAULT_TIME_BASE)
            ): vol.Coerce(int),
            vol.Optional(
                CONF_TARGET_INDOOR_TEMP,
                description=sv(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_INDOOR_TEMP_HYSTERESIS,
                description=sv(
                    CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS
                ),
            ): vol.Coerce(float),
        }
    )

    assert _section is not None  # only ever called behind that guard
    return vol.Schema(
        {
            vol.Required("building"): _section(building_schema, {"collapsed": False}),
            vol.Optional("envelope"): _section(envelope_schema, {"collapsed": True}),
            vol.Optional("sensors"): _section(sensors_schema, {"collapsed": True}),
            vol.Optional("heat_pump_and_curve"): _section(
                curve_schema, {"collapsed": True}
            ),
            vol.Optional("advanced"): _section(advanced_schema, {"collapsed": True}),
        }
    )


class HeatingCurveOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg, misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Handle a config flow for Heating Curve Optimizer."""

    VERSION = 1

    @classmethod
    @callback  # type: ignore[untyped-decorator]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type]:
        """Return supported subentry types (phase 5c, REDESIGN.md; hybrid
        gas-boiler comparison; PV arrays).

        Empty on an HA release too old to have ConfigSubentryFlow at all -
        see HeatingZoneSubentryFlow's/HeatingGasBoilerSubentryFlow's/
        HeatingPvArraySubentryFlow's definitions above. Each type is
        independently None-guarded (all three share the same HA-version
        gate today, but this stays correct if that ever changes).
        """
        types: dict[str, type] = {}
        if HeatingZoneSubentryFlow is not None:
            types[ZONE_SUBENTRY_TYPE] = HeatingZoneSubentryFlow
        if HeatingGasBoilerSubentryFlow is not None:
            types[GAS_SUBENTRY_TYPE] = HeatingGasBoilerSubentryFlow
        if HeatingPvArraySubentryFlow is not None:
            types[PV_SUBENTRY_TYPE] = HeatingPvArraySubentryFlow
        return types

    def __init__(self) -> None:
        super().__init__()

        self.context: ConfigFlowContext = {}
        self.configs: list[dict[str, Any]] = []
        self.source_type: str | None = None
        self.sources: list[str] | None = None
        self.price_settings: dict[str, Any] = {}
        self.consumption_price_sensor: str | None = None
        self.production_price_sensor: str | None = None
        self.area_m2: float | None = None
        self.energy_label: str | None = None
        self.glass_east_m2: float | None = None
        self.glass_west_m2: float | None = None
        self.glass_south_m2: float | None = None
        self.glass_u_value: float | None = None
        self.ventilation_type: str = DEFAULT_VENTILATION_TYPE
        self.ceiling_height: float = DEFAULT_CEILING_HEIGHT
        self.thermal_mass_class: str = DEFAULT_THERMAL_MASS_CLASS
        self.emitter_type: str = DEFAULT_EMITTER_TYPE
        self.power_consumption: str | None = None
        self.indoor_temperature_sensor: str | None = None
        self.supply_temperature_sensor: str | None = None
        self.grid_import_sensor: str | None = None
        self.grid_export_sensor: str | None = None
        self.k_factor: float | None = None
        self.base_cop: float = DEFAULT_COP_AT_35
        self.outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT
        self.cop_compensation_factor: float = DEFAULT_COP_COMPENSATION_FACTOR
        self.planning_window: int = DEFAULT_PLANNING_WINDOW
        self.time_base: int = DEFAULT_TIME_BASE
        self.target_indoor_temp: float = DEFAULT_TARGET_INDOOR_TEMP
        self.indoor_temp_hysteresis: float = DEFAULT_INDOOR_TEMP_HYSTERESIS
        self.offset_delta_t: int = DEFAULT_OFFSET_DELTA_T
        self.heat_curve_min_outdoor: float = -20.0
        self.heat_curve_max_outdoor: float = 15.0
        self.heating_curve_offset: float = DEFAULT_HEATING_CURVE_OFFSET
        self.heat_curve_min: float = DEFAULT_HEAT_CURVE_MIN
        self.heat_curve_max: float = DEFAULT_HEAT_CURVE_MAX
        self._detection_offered: bool = False
        self._detected_sensors: dict[str, DetectedSensor] = {}
        self._detected_sensor_lists: dict[str, DetectedSensorList] = {}

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is None and not self._detection_offered:
            self._detection_offered = True
            self._detected_sensors = detect_main_flow_sensors(self.hass)
            self._detected_sensor_lists = detect_source_sensors(self.hass)
            if self._detected_sensors or self._detected_sensor_lists:
                return await self.async_step_detected_integrations()
        if _section is not None:
            return await self._async_step_sectioned(user_input)
        if user_input is not None:
            choice = user_input[CONF_SOURCE_TYPE]
            if choice == STEP_BASIC:
                return await self.async_step_basic_options()
            if choice == STEP_HEATING_CURVE_SETTINGS:
                return await self.async_step_heating_curve_settings()
            if choice == STEP_PRICE_SETTINGS:
                return await self.async_step_price_settings()
            if choice == "finish":
                if self.area_m2 is None:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": "missing_basic"},
                    )
                if not self.configs:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": "no_blocks"},
                    )
                connection_error = await _test_api_connection(self.hass)
                if connection_error:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": connection_error},
                    )
                consumption_price_sensor = (
                    self.consumption_price_sensor
                    or self.price_settings.get(CONF_CONSUMPTION_PRICE_SENSOR)
                    or self.price_settings.get(CONF_PRICE_SENSOR)
                )
                production_price_sensor = (
                    self.production_price_sensor
                    or self.price_settings.get(CONF_PRODUCTION_PRICE_SENSOR)
                    or consumption_price_sensor
                )
                return self.async_create_entry(
                    title="Heating Curve Optimizer",
                    data=self._build_entry_data(
                        consumption_price_sensor, production_price_sensor
                    ),
                )
            self.source_type = choice
            return await self.async_step_select_sources()

        return self.async_show_form(step_id="user", data_schema=self._schema_user())

    async def async_step_detected_integrations(
        self, user_input: dict[str, bool] | None = None
    ) -> ConfigFlowResult:
        """Offer to prefill sensors already configured in Battery Controller
        and/or Dynamic Energy Contract Calculator (see
        companion_integrations.py). Only reached when at least one
        candidate was found - shown once, then falls through to the normal
        setup menu either way."""
        if user_input is not None:
            for field, use_detected in user_input.items():
                if not use_detected:
                    continue
                if field in self._detected_sensors:
                    setattr(self, field, self._detected_sensors[field].entity_id)
                elif field in self._detected_sensor_lists:
                    entity_ids = self._detected_sensor_lists[field].entity_ids
                    source_type = (
                        SOURCE_TYPE_CONSUMPTION
                        if field == FIELD_SOURCES_CONSUMPTION
                        else SOURCE_TYPE_PRODUCTION
                    )
                    self.configs = [
                        cfg
                        for cfg in self.configs
                        if cfg.get(CONF_SOURCE_TYPE) != source_type
                    ]
                    self.configs.append(
                        {CONF_SOURCE_TYPE: source_type, CONF_SOURCES: entity_ids}
                    )
            return await self.async_step_user()

        schema = vol.Schema(
            {
                vol.Optional(field, default=True): bool
                for field in (*self._detected_sensors, *self._detected_sensor_lists)
            }
        )
        detected_list = "\n".join(
            f"- {field}: `{detected.entity_id}` ({detected.source})"
            for field, detected in self._detected_sensors.items()
        )
        detected_list += "".join(
            f"\n- {field}: `{', '.join(detected.entity_ids)}` ({detected.source})"
            for field, detected in self._detected_sensor_lists.items()
        )
        return self.async_show_form(
            step_id="detected_integrations",
            data_schema=schema,
            description_placeholders={"detected_list": detected_list},
        )

    def _build_entry_data(
        self, consumption_price_sensor: str | None, production_price_sensor: str | None
    ) -> dict[str, Any]:
        """Build entry data dictionary from instance attributes."""
        return {
            CONF_CONFIGS: self.configs,
            CONF_PRICE_SENSOR: consumption_price_sensor,
            CONF_CONSUMPTION_PRICE_SENSOR: consumption_price_sensor,
            CONF_PRODUCTION_PRICE_SENSOR: production_price_sensor,
            CONF_AREA_M2: self.area_m2,
            CONF_ENERGY_LABEL: self.energy_label,
            CONF_GLASS_EAST_M2: self.glass_east_m2,
            CONF_GLASS_WEST_M2: self.glass_west_m2,
            CONF_GLASS_SOUTH_M2: self.glass_south_m2,
            CONF_GLASS_U_VALUE: self.glass_u_value,
            CONF_VENTILATION_TYPE: self.ventilation_type,
            CONF_CEILING_HEIGHT: self.ceiling_height,
            CONF_THERMAL_MASS_CLASS: self.thermal_mass_class,
            CONF_EMITTER_TYPE: self.emitter_type,
            CONF_INDOOR_TEMPERATURE_SENSOR: self.indoor_temperature_sensor,
            CONF_POWER_CONSUMPTION: self.power_consumption,
            CONF_SUPPLY_TEMPERATURE_SENSOR: self.supply_temperature_sensor,
            CONF_GRID_IMPORT_SENSOR: self.grid_import_sensor,
            CONF_GRID_EXPORT_SENSOR: self.grid_export_sensor,
            CONF_K_FACTOR: self.k_factor,
            CONF_BASE_COP: self.base_cop,
            CONF_OUTDOOR_TEMP_COEFFICIENT: self.outdoor_temp_coefficient,
            CONF_COP_COMPENSATION_FACTOR: self.cop_compensation_factor,
            CONF_PLANNING_WINDOW: self.planning_window,
            CONF_TIME_BASE: self.time_base,
            CONF_TARGET_INDOOR_TEMP: self.target_indoor_temp,
            CONF_INDOOR_TEMP_HYSTERESIS: self.indoor_temp_hysteresis,
            CONF_OFFSET_DELTA_T: self.offset_delta_t,
            CONF_HEAT_CURVE_MIN_OUTDOOR: self.heat_curve_min_outdoor,
            CONF_HEAT_CURVE_MAX_OUTDOOR: self.heat_curve_max_outdoor,
            CONF_HEATING_CURVE_OFFSET: self.heating_curve_offset,
            CONF_HEAT_CURVE_MIN: self.heat_curve_min,
            CONF_HEAT_CURVE_MAX: self.heat_curve_max,
        }

    def _schema_user(self) -> vol.Schema:
        options = [{"value": STEP_BASIC, "label": "Basic Settings"}]
        options.extend({"value": t, "label": t.title()} for t in SOURCE_TYPES)
        options.append(
            {"value": STEP_HEATING_CURVE_SETTINGS, "label": "Heating Curve Settings"}
        )
        options.append({"value": STEP_PRICE_SETTINGS, "label": "Price Settings"})
        options.append({"value": "finish", "label": "Finish"})

        return vol.Schema(
            {
                vol.Required(CONF_SOURCE_TYPE): selector(
                    {
                        "select": {
                            "options": options,
                            "mode": "dropdown",
                            "custom_value": False,
                        }
                    }
                )
            }
        )

    def _sectioned_defaults(self) -> dict[str, Any]:
        """Prefill values for `_build_sectioned_schema` - reuses
        `_build_entry_data`'s own dict (keyed by the same CONF_ constants
        the sectioned schema's fields use) rather than re-listing every
        field a second time, plus the two flattened source-list fields
        `_build_entry_data` doesn't know about."""
        defaults = self._build_entry_data(
            self.consumption_price_sensor, self.production_price_sensor
        )
        defaults[FIELD_SOURCES_CONSUMPTION] = next(
            (
                cfg[CONF_SOURCES]
                for cfg in self.configs
                if cfg.get(CONF_SOURCE_TYPE) == SOURCE_TYPE_CONSUMPTION
            ),
            [],
        )
        defaults[FIELD_SOURCES_PRODUCTION] = next(
            (
                cfg[CONF_SOURCES]
                for cfg in self.configs
                if cfg.get(CONF_SOURCE_TYPE) == SOURCE_TYPE_PRODUCTION
            ),
            [],
        )
        return defaults

    async def _async_step_sectioned(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Single-page sectioned form - the modern replacement for the
        legacy multi-step wizard below, used whenever `_section is not
        None`. Always shown/resubmitted as step_id="user", so this is
        reached both for the form's first display and every resubmission.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = _extract_sectioned_data(user_input)
            sensors_section = user_input.get("sensors", {})
            configs = _build_configs_from_sources(
                sensors_section.get(FIELD_SOURCES_CONSUMPTION, []),
                sensors_section.get(FIELD_SOURCES_PRODUCTION, []),
            )

            if not configs:
                errors["base"] = "no_blocks"
            else:
                connection_error = await _test_api_connection(self.hass)
                if connection_error:
                    errors["base"] = connection_error

            for key, value in flat.items():
                setattr(self, key, value)
            self.configs = configs

            if not errors:
                return self.async_create_entry(
                    title="Heating Curve Optimizer",
                    data=self._build_entry_data(
                        flat[CONF_CONSUMPTION_PRICE_SENSOR],
                        flat[CONF_PRODUCTION_PRICE_SENSOR],
                    ),
                )

        schema = _build_sectioned_schema(
            self._sectioned_defaults(),
            self._get_power_sensors(),
            self._get_temperature_sensors(),
            self._get_price_sensors(),
            self._get_energy_sensors(),
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    def _get_energy_sensors(self) -> list[str]:
        """Get sorted list of energy sensors."""
        return sorted(
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") in ("energy", "gas")
        )

    def _get_power_sensors(self) -> list[str]:
        """Get sorted list of power sensors.

        Also unions in whatever power_consumption/grid_import_sensor/
        grid_export_sensor is currently selected, even if it doesn't carry
        a matching device_class - otherwise a sensor prefilled from a
        companion integration (companion_integrations.py) could silently
        vanish from its own dropdown instead of showing as selected.
        """
        discovered = {
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") in ("power", "energy")
        }
        for current in (
            self.power_consumption,
            self.grid_import_sensor,
            self.grid_export_sensor,
        ):
            if current:
                discovered.add(current)
        return sorted(discovered)

    def _get_temperature_sensors(self) -> list[str]:
        """Get sorted list of temperature sensors."""
        return sorted(
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "temperature"
            or state.attributes.get("unit_of_measurement") in ["°C", "°F", "K"]
        )

    def _get_price_sensors(self) -> list[str]:
        """Get list of price sensors.

        Also unions in whatever consumption_price_sensor/
        production_price_sensor is currently selected, for the same reason
        _get_power_sensors does - a companion-integration-detected sensor
        must never vanish from its own dropdown.
        """
        discovered = {
            state.entity_id
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("device_class") == "monetary"
            or state.attributes.get("unit_of_measurement") == "€/kWh"
        }
        for current in (self.consumption_price_sensor, self.production_price_sensor):
            if current:
                discovered.add(current)
        return sorted(discovered)

    async def async_step_basic_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Redirect to async_step_basic for backward compatibility."""
        return await self.async_step_basic(user_input)

    def _apply_heating_curve_input(self, user_input: dict[str, Any]) -> None:
        """Apply heating curve settings from user input."""
        self.supply_temperature_sensor = user_input.get(CONF_SUPPLY_TEMPERATURE_SENSOR)
        self.k_factor = float(user_input.get(CONF_K_FACTOR, DEFAULT_K_FACTOR))
        self.base_cop = float(user_input.get(CONF_BASE_COP, DEFAULT_COP_AT_35))
        self.outdoor_temp_coefficient = float(
            user_input.get(
                CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
            )
        )
        self.cop_compensation_factor = float(
            user_input.get(
                CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
            )
        )
        self.planning_window = int(
            user_input.get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW)
        )
        self.time_base = int(user_input.get(CONF_TIME_BASE, DEFAULT_TIME_BASE))
        self.target_indoor_temp = float(
            user_input.get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP)
        )
        self.indoor_temp_hysteresis = float(
            user_input.get(CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS)
        )
        self.offset_delta_t = int(
            user_input.get(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T)
        )
        self.heat_curve_min_outdoor = float(
            user_input.get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0)
        )
        self.heat_curve_max_outdoor = float(
            user_input.get(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0)
        )
        self.heating_curve_offset = float(
            user_input.get(CONF_HEATING_CURVE_OFFSET, DEFAULT_HEATING_CURVE_OFFSET)
        )
        self.heat_curve_min = float(
            user_input.get(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN)
        )
        self.heat_curve_max = float(
            user_input.get(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX)
        )

    def _build_heating_curve_schema(self, temp_sensors: list[str]) -> vol.Schema:
        """Build schema for heating curve settings."""
        return vol.Schema(
            {
                vol.Optional(
                    CONF_SUPPLY_TEMPERATURE_SENSOR,
                    default=self.supply_temperature_sensor,
                ): selector(
                    {
                        "select": {
                            "options": temp_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Optional(
                    CONF_K_FACTOR, default=self.k_factor or DEFAULT_K_FACTOR
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_BASE_COP, default=self.base_cop or DEFAULT_COP_AT_35
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_OUTDOOR_TEMP_COEFFICIENT,
                    default=self.outdoor_temp_coefficient
                    or DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_COP_COMPENSATION_FACTOR,
                    default=self.cop_compensation_factor
                    or DEFAULT_COP_COMPENSATION_FACTOR,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_PLANNING_WINDOW,
                    default=self.planning_window or DEFAULT_PLANNING_WINDOW,
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_TIME_BASE,
                    default=self.time_base or DEFAULT_TIME_BASE,
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_TARGET_INDOOR_TEMP,
                    default=self.target_indoor_temp or DEFAULT_TARGET_INDOOR_TEMP,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_INDOOR_TEMP_HYSTERESIS,
                    default=self.indoor_temp_hysteresis
                    or DEFAULT_INDOOR_TEMP_HYSTERESIS,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_OFFSET_DELTA_T,
                    default=self.offset_delta_t or DEFAULT_OFFSET_DELTA_T,
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_HEAT_CURVE_MIN_OUTDOOR,
                    default=self.heat_curve_min_outdoor,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HEAT_CURVE_MAX_OUTDOOR,
                    default=self.heat_curve_max_outdoor,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HEATING_CURVE_OFFSET,
                    default=self.heating_curve_offset,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HEAT_CURVE_MIN,
                    default=self.heat_curve_min,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_HEAT_CURVE_MAX,
                    default=self.heat_curve_max,
                ): vol.Coerce(float),
            }
        )

    async def async_step_heating_curve_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_heating_curve_input(user_input)
            return await self.async_step_user()

        temp_sensors = self._get_temperature_sensors()
        schema = self._build_heating_curve_schema(temp_sensors)

        return self.async_show_form(
            step_id=STEP_HEATING_CURVE_SETTINGS, data_schema=schema
        )

    def _apply_basic_input(self, user_input: dict[str, Any]) -> None:
        """Apply basic settings from user input."""
        self.area_m2 = float(user_input[CONF_AREA_M2])
        self.energy_label = user_input[CONF_ENERGY_LABEL]
        self.glass_east_m2 = float(user_input.get(CONF_GLASS_EAST_M2, 0))
        self.glass_west_m2 = float(user_input.get(CONF_GLASS_WEST_M2, 0))
        self.glass_south_m2 = float(user_input.get(CONF_GLASS_SOUTH_M2, 0))
        self.glass_u_value = float(user_input.get(CONF_GLASS_U_VALUE, 1.2))
        self.ventilation_type = user_input.get(
            CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE
        )
        self.ceiling_height = float(
            user_input.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)
        )
        self.thermal_mass_class = user_input.get(
            CONF_THERMAL_MASS_CLASS, DEFAULT_THERMAL_MASS_CLASS
        )
        self.emitter_type = user_input.get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE)
        self.indoor_temperature_sensor = user_input.get(CONF_INDOOR_TEMPERATURE_SENSOR)
        self.power_consumption = user_input.get(CONF_POWER_CONSUMPTION)
        self.grid_import_sensor = user_input.get(CONF_GRID_IMPORT_SENSOR)
        self.grid_export_sensor = user_input.get(CONF_GRID_EXPORT_SENSOR)

    def _build_basic_schema(
        self,
        power_sensors: list[str],
        temp_sensors: list[str],
        *,
        with_defaults: bool = False,
    ) -> vol.Schema:
        """Build schema for basic settings."""
        area_field = (
            vol.Required(CONF_AREA_M2, default=self.area_m2)
            if with_defaults
            else vol.Required(CONF_AREA_M2)
        )
        label_field = (
            vol.Required(CONF_ENERGY_LABEL, default=self.energy_label)
            if with_defaults
            else vol.Required(CONF_ENERGY_LABEL)
        )

        return vol.Schema(
            {
                area_field: vol.Coerce(float),
                label_field: selector(
                    {
                        "select": {
                            "options": ENERGY_LABELS,
                            "mode": "dropdown",
                            "custom_value": False,
                        }
                    }
                ),
                vol.Optional(
                    CONF_GLASS_EAST_M2, default=self.glass_east_m2 or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_GLASS_WEST_M2, default=self.glass_west_m2 or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_GLASS_SOUTH_M2, default=self.glass_south_m2 or 0.0
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_GLASS_U_VALUE, default=self.glass_u_value or 1.2
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_VENTILATION_TYPE,
                    default=self.ventilation_type or DEFAULT_VENTILATION_TYPE,
                ): selector(
                    {
                        "select": {
                            "options": list(VENTILATION_TYPES.keys()),
                            "mode": "dropdown",
                            "translation_key": "ventilation_type",
                        }
                    }
                ),
                vol.Optional(
                    CONF_CEILING_HEIGHT,
                    default=self.ceiling_height or DEFAULT_CEILING_HEIGHT,
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_THERMAL_MASS_CLASS,
                    default=self.thermal_mass_class or DEFAULT_THERMAL_MASS_CLASS,
                ): selector(
                    {
                        "select": {
                            "options": list(THERMAL_MASS_WH_PER_M2_K.keys()),
                            "mode": "dropdown",
                            "translation_key": "thermal_mass_class",
                        }
                    }
                ),
                vol.Optional(
                    CONF_EMITTER_TYPE,
                    default=self.emitter_type or DEFAULT_EMITTER_TYPE,
                ): selector(
                    {
                        "select": {
                            "options": list(EMITTER_EXPONENT_MAP.keys()),
                            "mode": "dropdown",
                            "translation_key": "emitter_type",
                        }
                    }
                ),
                vol.Optional(
                    CONF_INDOOR_TEMPERATURE_SENSOR,
                    default=self.indoor_temperature_sensor,
                ): selector(
                    {
                        "select": {
                            "options": temp_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Optional(
                    CONF_POWER_CONSUMPTION, default=self.power_consumption
                ): selector(
                    {
                        "select": {
                            "options": power_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                # Real-time PV-surplus controller (phase 5b, REDESIGN.md) -
                # optional.
                vol.Optional(
                    CONF_GRID_IMPORT_SENSOR, default=self.grid_import_sensor
                ): selector(
                    {
                        "select": {
                            "options": power_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Optional(
                    CONF_GRID_EXPORT_SENSOR, default=self.grid_export_sensor
                ): selector(
                    {
                        "select": {
                            "options": power_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
            }
        )

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_basic_input(user_input)
            return await self.async_step_user()

        power_sensors = self._get_power_sensors()
        temp_sensors = self._get_temperature_sensors()
        schema = self._build_basic_schema(power_sensors, temp_sensors)

        return self.async_show_form(step_id=STEP_BASIC, data_schema=schema)

    def _update_source_config(self, sources: list[str]) -> None:
        """Update or add source configuration."""
        self.sources = sources
        new_config = {
            CONF_SOURCE_TYPE: self.source_type,
            CONF_SOURCES: self.sources,
        }

        # Find existing config with same source_type
        for i, cfg in enumerate(self.configs):
            if cfg.get(CONF_SOURCE_TYPE) == self.source_type:
                self.configs[i] = new_config
                return

        # Add new config if not found
        self.configs.append(new_config)

    def _get_default_sources(self) -> list[str]:
        """Get default sources for current source type."""
        for block in reversed(self.configs):
            if block[CONF_SOURCE_TYPE] == self.source_type:
                return list(block[CONF_SOURCES])
        return []

    async def async_step_select_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._update_source_config(user_input[CONF_SOURCES])
            return await self.async_step_user()

        all_sensors = self._get_energy_sensors()
        default_sources = self._get_default_sources()

        return self.async_show_form(
            step_id=STEP_SELECT_SOURCES,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCES, default=default_sources): selector(
                        {
                            "select": {
                                "options": all_sensors,
                                "multiple": True,
                                "mode": "dropdown",
                            }
                        }
                    )
                }
            ),
        )

    def _get_current_price_sensors(self) -> tuple[str, str]:
        """Get current consumption and production price sensors."""
        consumption = (
            self.consumption_price_sensor
            or self.price_settings.get(CONF_CONSUMPTION_PRICE_SENSOR)
            or self.price_settings.get(CONF_PRICE_SENSOR, "")
        )
        production = (
            self.production_price_sensor
            or self.price_settings.get(CONF_PRODUCTION_PRICE_SENSOR)
            or consumption
        )
        return consumption, production

    def _build_price_schema(self, price_sensors: list[str]) -> vol.Schema:
        """Build schema for price settings."""
        consumption, production = self._get_current_price_sensors()

        return vol.Schema(
            {
                vol.Required(
                    CONF_CONSUMPTION_PRICE_SENSOR, default=consumption
                ): selector(
                    {
                        "select": {
                            "options": price_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Required(
                    CONF_PRODUCTION_PRICE_SENSOR, default=production
                ): selector(
                    {
                        "select": {
                            "options": price_sensors,
                            "multiple": False,
                            "mode": "dropdown",
                        }
                    }
                ),
            }
        )

    async def async_step_price_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.consumption_price_sensor = user_input[CONF_CONSUMPTION_PRICE_SENSOR]
            self.production_price_sensor = user_input[CONF_PRODUCTION_PRICE_SENSOR]
            self.price_settings = dict(user_input)
            return await self.async_step_user()

        all_prices = self._get_price_sensors()
        schema = self._build_price_schema(all_prices)

        return self.async_show_form(
            step_id=STEP_PRICE_SETTINGS,
            data_schema=schema,
        )

    @staticmethod
    @callback  # type: ignore[untyped-decorator]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return HeatingCurveOptimizerOptionsFlowHandler(config_entry)


class HeatingCurveOptimizerOptionsFlowHandler(config_entries.OptionsFlow):  # type: ignore[misc]  # HA base class untyped: no py.typed in this env's pinned HA 2024.3.3
    """Handle updates to a config entry (options)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.configs: list[dict[str, Any]] = list(
            config_entry.options.get(
                CONF_CONFIGS, config_entry.data.get(CONF_CONFIGS, [])
            )
        )

        def _get(key: str, default: Any = None) -> Any:
            return config_entry.options.get(key, config_entry.data.get(key, default))

        self.area_m2 = _get(CONF_AREA_M2)
        self.energy_label = _get(CONF_ENERGY_LABEL)
        self.glass_east_m2 = _get(CONF_GLASS_EAST_M2)
        self.glass_west_m2 = _get(CONF_GLASS_WEST_M2)
        self.glass_south_m2 = _get(CONF_GLASS_SOUTH_M2)
        self.glass_u_value = _get(CONF_GLASS_U_VALUE, 1.2)
        self.ventilation_type = _get(CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE)
        self.ceiling_height = _get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)
        self.thermal_mass_class = _get(
            CONF_THERMAL_MASS_CLASS, DEFAULT_THERMAL_MASS_CLASS
        )
        self.emitter_type = _get(CONF_EMITTER_TYPE, DEFAULT_EMITTER_TYPE)
        self.indoor_temperature_sensor = _get(CONF_INDOOR_TEMPERATURE_SENSOR)
        self.power_consumption = _get(CONF_POWER_CONSUMPTION)
        self.supply_temperature_sensor = _get(CONF_SUPPLY_TEMPERATURE_SENSOR)
        self.grid_import_sensor = _get(CONF_GRID_IMPORT_SENSOR)
        self.grid_export_sensor = _get(CONF_GRID_EXPORT_SENSOR)
        self.k_factor = _get(CONF_K_FACTOR)
        self.base_cop = _get(CONF_BASE_COP, DEFAULT_COP_AT_35)
        self.outdoor_temp_coefficient = _get(
            CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
        )
        self.cop_compensation_factor = _get(
            CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
        )
        self.planning_window = _get(CONF_PLANNING_WINDOW, DEFAULT_PLANNING_WINDOW)
        self.time_base = _get(CONF_TIME_BASE, DEFAULT_TIME_BASE)
        self.target_indoor_temp = _get(
            CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP
        )
        self.indoor_temp_hysteresis = _get(
            CONF_INDOOR_TEMP_HYSTERESIS, DEFAULT_INDOOR_TEMP_HYSTERESIS
        )
        self.offset_delta_t = _get(CONF_OFFSET_DELTA_T, DEFAULT_OFFSET_DELTA_T)
        self.heat_curve_min_outdoor = _get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0)
        self.heat_curve_max_outdoor = _get(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0)
        self.heating_curve_offset = _get(
            CONF_HEATING_CURVE_OFFSET, DEFAULT_HEATING_CURVE_OFFSET
        )
        self.heat_curve_min = _get(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN)
        self.heat_curve_max = _get(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX)
        self.price_settings = copy.deepcopy(
            config_entry.options.get(
                CONF_PRICE_SETTINGS,
                {},
            )
        )
        self.consumption_price_sensor = _get(CONF_CONSUMPTION_PRICE_SENSOR)
        self.production_price_sensor = _get(CONF_PRODUCTION_PRICE_SENSOR)
        price_sensor = _get(CONF_PRICE_SENSOR)
        if self.consumption_price_sensor is None:
            self.consumption_price_sensor = price_sensor
        if self.production_price_sensor is None:
            self.production_price_sensor = price_sensor
        if (
            self.consumption_price_sensor
            and CONF_CONSUMPTION_PRICE_SENSOR not in self.price_settings
        ):
            self.price_settings[CONF_CONSUMPTION_PRICE_SENSOR] = (
                self.consumption_price_sensor
            )
        if (
            self.production_price_sensor
            and CONF_PRODUCTION_PRICE_SENSOR not in self.price_settings
        ):
            self.price_settings[CONF_PRODUCTION_PRICE_SENSOR] = (
                self.production_price_sensor
            )
        if price_sensor and CONF_PRICE_SENSOR not in self.price_settings:
            self.price_settings[CONF_PRICE_SENSOR] = price_sensor
        self.source_type: str | None = None
        self.sources: list[str] | None = None

    # Reuse helper methods from ConfigFlow
    _get_energy_sensors = HeatingCurveOptimizerConfigFlow._get_energy_sensors
    _get_power_sensors = HeatingCurveOptimizerConfigFlow._get_power_sensors
    _get_temperature_sensors = HeatingCurveOptimizerConfigFlow._get_temperature_sensors
    _get_price_sensors = HeatingCurveOptimizerConfigFlow._get_price_sensors
    _apply_basic_input = HeatingCurveOptimizerConfigFlow._apply_basic_input
    _apply_heating_curve_input = (
        HeatingCurveOptimizerConfigFlow._apply_heating_curve_input
    )
    _build_heating_curve_schema = (
        HeatingCurveOptimizerConfigFlow._build_heating_curve_schema
    )
    _build_basic_schema = HeatingCurveOptimizerConfigFlow._build_basic_schema
    _build_price_schema = HeatingCurveOptimizerConfigFlow._build_price_schema
    _get_current_price_sensors = (
        HeatingCurveOptimizerConfigFlow._get_current_price_sensors
    )
    _update_source_config = HeatingCurveOptimizerConfigFlow._update_source_config
    _get_default_sources = HeatingCurveOptimizerConfigFlow._get_default_sources
    _build_entry_data = HeatingCurveOptimizerConfigFlow._build_entry_data
    _sectioned_defaults = HeatingCurveOptimizerConfigFlow._sectioned_defaults

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_user()

    async def _async_step_sectioned(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Single-page sectioned form for the options flow - same shape as
        the initial ConfigFlow's own `_async_step_sectioned`, but without
        the API-connection check and with `title=""`, matching the legacy
        options flow's own "finish" branch below exactly."""
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = _extract_sectioned_data(user_input)
            sensors_section = user_input.get("sensors", {})
            configs = _build_configs_from_sources(
                sensors_section.get(FIELD_SOURCES_CONSUMPTION, []),
                sensors_section.get(FIELD_SOURCES_PRODUCTION, []),
            )

            if not configs:
                errors["base"] = "no_blocks"

            for key, value in flat.items():
                setattr(self, key, value)
            self.configs = configs

            if not errors:
                return self.async_create_entry(
                    title="",
                    data=self._build_entry_data(
                        flat[CONF_CONSUMPTION_PRICE_SENSOR],
                        flat[CONF_PRODUCTION_PRICE_SENSOR],
                    ),
                )

        schema = _build_sectioned_schema(
            self._sectioned_defaults(),
            self._get_power_sensors(),
            self._get_temperature_sensors(),
            self._get_price_sensors(),
            self._get_energy_sensors(),
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if _section is not None:
            return await self._async_step_sectioned(user_input)
        if user_input and CONF_SOURCE_TYPE in user_input:
            choice = user_input[CONF_SOURCE_TYPE]
            if choice == STEP_BASIC:
                return await self.async_step_basic()
            if choice == STEP_HEATING_CURVE_SETTINGS:
                return await self.async_step_heating_curve_settings()
            if choice == STEP_PRICE_SETTINGS:
                return await self.async_step_price_settings()
            if choice == "finish":
                if self.area_m2 is None:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": "missing_basic"},
                    )
                if not self.configs:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._schema_user(),
                        errors={"base": "no_blocks"},
                    )
                consumption_price_sensor = (
                    self.consumption_price_sensor
                    or self.price_settings.get(CONF_CONSUMPTION_PRICE_SENSOR)
                    or self.price_settings.get(CONF_PRICE_SENSOR)
                )
                production_price_sensor = (
                    self.production_price_sensor
                    or self.price_settings.get(CONF_PRODUCTION_PRICE_SENSOR)
                    or consumption_price_sensor
                )
                return self.async_create_entry(
                    title="",
                    data=self._build_entry_data(
                        consumption_price_sensor, production_price_sensor
                    ),
                )
            self.source_type = choice
            return await self.async_step_select_sources()

        return self.async_show_form(step_id="user", data_schema=self._schema_user())

    def _schema_user(self) -> vol.Schema:
        options = [{"value": STEP_BASIC, "label": "Basic Settings"}]
        options.extend({"value": t, "label": t.title()} for t in SOURCE_TYPES)
        options.append(
            {"value": STEP_HEATING_CURVE_SETTINGS, "label": "Heating Curve Settings"}
        )
        options.append({"value": STEP_PRICE_SETTINGS, "label": "Price Settings"})
        options.append({"value": "finish", "label": "Finish"})

        return vol.Schema(
            {
                vol.Required(CONF_SOURCE_TYPE): selector(
                    {
                        "select": {
                            "options": options,
                            "mode": "dropdown",
                            "custom_value": False,
                        }
                    }
                )
            }
        )

    async def async_step_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_basic_input(user_input)
            return await self.async_step_user()

        power_sensors = self._get_power_sensors()
        temp_sensors = self._get_temperature_sensors()
        schema = self._build_basic_schema(
            power_sensors, temp_sensors, with_defaults=True
        )

        return self.async_show_form(step_id=STEP_BASIC, data_schema=schema)

    async def async_step_heating_curve_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._apply_heating_curve_input(user_input)
            return await self.async_step_user()

        temp_sensors = self._get_temperature_sensors()
        schema = self._build_heating_curve_schema(temp_sensors)

        return self.async_show_form(
            step_id=STEP_HEATING_CURVE_SETTINGS, data_schema=schema
        )

    async def async_step_select_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input and CONF_SOURCES in user_input:
            self._update_source_config(user_input[CONF_SOURCES])
            return await self.async_step_user()

        all_sensors = self._get_energy_sensors()
        default_sources = self._get_default_sources()

        return self.async_show_form(
            step_id=STEP_SELECT_SOURCES,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCES, default=default_sources): selector(
                        {
                            "select": {
                                "options": all_sensors,
                                "multiple": True,
                                "mode": "dropdown",
                            }
                        }
                    )
                }
            ),
        )

    async def async_step_price_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.consumption_price_sensor = user_input[CONF_CONSUMPTION_PRICE_SENSOR]
            self.production_price_sensor = user_input[CONF_PRODUCTION_PRICE_SENSOR]
            self.price_settings = dict(user_input)
            return await self.async_step_user()

        all_prices = self._get_price_sensors()
        schema = self._build_price_schema(all_prices)

        return self.async_show_form(
            step_id=STEP_PRICE_SETTINGS,
            data_schema=schema,
        )
