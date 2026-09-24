"""Sensor platform for Heating Curve Optimizer.

Most sensors are thin views on a coordinator and are declared as
`HcoSensorEntityDescription`s: a value function and an attribute function
over the coordinator's data. The few that do their own bookkeeping
(measured thermal power/energy, cumulative savings) are classes below.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util

from .calibration import CALIBRATION_STATUSES
from .const import (
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_POWER_CONSUMPTION,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    DEFAULT_HEAT_CURVE_MAX,
    DEFAULT_HEAT_CURVE_MAX_OUTDOOR,
    DEFAULT_HEAT_CURVE_MIN,
    DEFAULT_HEAT_CURVE_MIN_OUTDOOR,
)
from .heatpump_model import HeatPumpConfig
from .helpers import calculate_supply_temperature, get_sensor_value, read_power_kw

PARALLEL_UPDATES = 0

CURRENCY_EUR = "EUR"
EUR_PER_KWH = "EUR/kWh"

# Forecast attributes are large and change every run: keep them out of the
# recorder database.
_FORECAST_ATTRIBUTES = frozenset(
    {
        "forecast",
        "offsets",
        "supply_temps",
        "baseline_supply_temps",
        "indoor_temps",
        "baseline_indoor_temps",
        "prices",
        "outdoor_forecast",
        "step_start_times",
        "cop",
        "baseline_cop",
        "cost_eur",
        "baseline_cost_eur",
        "humidity_forecast",
        "errors_k",
    }
)


def _first(data: dict[str, Any], key: str) -> Any:
    values = data.get(key) or []
    return values[0] if values else None


def _step_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_start_times": data.get("step_start_times", []),
        "step_minutes": data.get("step_minutes"),
    }


@dataclass(frozen=True, kw_only=True)
class HcoSensorEntityDescription(SensorEntityDescription):
    """Describes a coordinator-backed sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


# --- Weather coordinator -----------------------------------------------------

OUTDOOR_TEMPERATURE = HcoSensorEntityDescription(
    key="outdoor_temperature",
    translation_key="outdoor_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d: d.get("current_temperature"),
    attrs_fn=lambda d: {
        "forecast": d.get("temperature_forecast", []),
        "humidity_forecast": d.get("humidity_forecast", []),
        "forecast_start": str(d.get("forecast_start_utc")),
    },
)

# --- Heat coordinator --------------------------------------------------------

HEAT_LOSS = HcoSensorEntityDescription(
    key="heat_loss",
    translation_key="heat_loss",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d: d.get("heat_loss"),
    attrs_fn=lambda d: {
        "forecast": d.get("heat_loss_forecast", []),
        "htc_w_per_k": d.get("htc_w_per_k"),
    },
)
WINDOW_SOLAR_GAIN = HcoSensorEntityDescription(
    key="window_solar_gain",
    translation_key="window_solar_gain",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d: d.get("solar_gain"),
    attrs_fn=lambda d: {"forecast": d.get("solar_gain_forecast", [])},
)
PV_PRODUCTION_FORECAST = HcoSensorEntityDescription(
    key="pv_production_forecast",
    translation_key="pv_production_forecast",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d: _first(d, "pv_production_forecast"),
    attrs_fn=lambda d: {"forecast": d.get("pv_production_forecast", [])},
)
NET_HEAT_LOSS = HcoSensorEntityDescription(
    key="net_heat_loss",
    translation_key="net_heat_loss",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d: d.get("net_heat_loss"),
    attrs_fn=lambda d: {
        "forecast": d.get("net_heat_loss_forecast", []),
        "internal_gain_kw": d.get("internal_gain"),
        "indoor_temperature": d.get("indoor_temperature"),
        "indoor_temperature_source": d.get("indoor_temperature_source"),
    },
)

# --- Optimization coordinator ------------------------------------------------

HEATING_CURVE_OFFSET = HcoSensorEntityDescription(
    key="heating_curve_offset",
    translation_key="heating_curve_offset",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    value_fn=lambda d: d.get("offset"),
    attrs_fn=lambda d: {
        "offsets": d.get("offsets", []),
        "prices": d.get("prices", []),
        "outdoor_forecast": d.get("outdoor_forecast", []),
        **_step_attrs(d),
    },
)
OPTIMIZED_SUPPLY_TEMPERATURE = HcoSensorEntityDescription(
    key="optimized_supply_temperature",
    translation_key="optimized_supply_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d: _first(d, "supply_temps"),
    attrs_fn=lambda d: {
        "supply_temps": d.get("supply_temps", []),
        "baseline_supply_temps": d.get("baseline_supply_temps", []),
        **_step_attrs(d),
    },
)
PLANNED_INDOOR_TEMPERATURE = HcoSensorEntityDescription(
    key="planned_indoor_temperature",
    translation_key="planned_indoor_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
    value_fn=lambda d: _first(d, "indoor_temps"),
    attrs_fn=lambda d: {
        "initial_indoor_temp": d.get("initial_indoor_temp"),
        "comfort_min": d.get("comfort_min"),
        "comfort_max": d.get("comfort_max"),
        "indoor_temps": d.get("indoor_temps", []),
        "baseline_indoor_temps": d.get("baseline_indoor_temps", []),
        **_step_attrs(d),
    },
)
HEAT_BUFFER = HcoSensorEntityDescription(
    key="heat_buffer",
    translation_key="heat_buffer",
    device_class=SensorDeviceClass.ENERGY_STORAGE,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d: d.get("buffer_kwh"),
    attrs_fn=lambda d: {
        "forecast": d.get("buffer_forecast_kwh", []),
        "comfort_min": d.get("comfort_min"),
        "thermal_mass_kwh_per_k": d.get("building_thermal_mass_kwh_per_k"),
        **_step_attrs(d),
    },
)
PLANNED_COP = HcoSensorEntityDescription(
    key="planned_cop",
    translation_key="planned_cop",
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=2,
    value_fn=lambda d: _first(d, "cop"),
    attrs_fn=lambda d: {
        "baseline_cop_now": _first(d, "baseline_cop"),
        "cop_delta_now": (
            round(_first(d, "cop") - _first(d, "baseline_cop"), 3)
            if d.get("cop") and d.get("baseline_cop")
            else None
        ),
        "cop": d.get("cop", []),
        "baseline_cop": d.get("baseline_cop", []),
        **_step_attrs(d),
    },
)
COST_SAVINGS_FORECAST = HcoSensorEntityDescription(
    key="cost_savings_forecast",
    translation_key="cost_savings_forecast",
    device_class=SensorDeviceClass.MONETARY,
    native_unit_of_measurement=CURRENCY_EUR,
    suggested_display_precision=2,
    value_fn=lambda d: d.get("cost_savings_eur"),
    attrs_fn=lambda d: {
        "optimized_cost_eur": d.get("total_cost_eur"),
        "baseline_cost_eur": d.get("baseline_total_cost_eur"),
        "horizon_hours": round(sum(d.get("step_durations_hours", [])), 2),
        "cost_eur": d.get("cost_eur", []),
        "baseline_cost_eur_per_step": d.get("baseline_cost_eur", []),
    },
)
SHADOW_PRICE = HcoSensorEntityDescription(
    key="shadow_price",
    translation_key="shadow_price",
    native_unit_of_measurement=EUR_PER_KWH,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    suggested_display_precision=3,
    value_fn=lambda d: d.get("shadow_price_eur_per_kwh"),
)
CALIBRATION = HcoSensorEntityDescription(
    key="calibration",
    translation_key="calibration",
    device_class=SensorDeviceClass.ENUM,
    options=CALIBRATION_STATUSES,
    value_fn=lambda d: (d.get("calibration") or {}).get("status"),
    attrs_fn=lambda d: (
        {k: v for k, v in (d.get("calibration") or {}).items() if k != "status"}
        | {
            "ua_w_per_k_in_use": d.get("building_ua_w_per_k"),
            "thermal_mass_kwh_per_k_in_use": d.get("building_thermal_mass_kwh_per_k"),
            "internal_gain_kw_in_use": d.get("internal_gain_kw"),
            "solar_factor_in_use": d.get("solar_factor"),
        }
    ),
)
MODEL_ACCURACY = HcoSensorEntityDescription(
    key="model_accuracy",
    translation_key="model_accuracy",
    native_unit_of_measurement=UnitOfTemperature.KELVIN,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=2,
    value_fn=lambda d: (d.get("model_accuracy") or {}).get("mae_k"),
    attrs_fn=lambda d: {
        k: v for k, v in (d.get("model_accuracy") or {}).items() if k != "mae_k"
    },
)
REALTIME_OFFSET = HcoSensorEntityDescription(
    key="realtime_offset",
    translation_key="realtime_offset",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    value_fn=lambda d: (d.get("realtime") or {}).get(
        "effective_offset", d.get("offset")
    ),
    attrs_fn=lambda d: {
        "planned_offset": d.get("offset"),
        "adjustment": (d.get("realtime") or {}).get("adjustment", 0),
        "current_grid_w": (d.get("realtime") or {}).get("current_grid_w"),
        "shadow_price_eur_per_kwh": d.get("shadow_price_eur_per_kwh"),
    },
)

# --- Gas boiler coordinator --------------------------------------------------

GAS_BOILER_HEAT_PUMP_COST = HcoSensorEntityDescription(
    key="heat_pump_cost",
    translation_key="gas_boiler_heat_pump_cost",
    native_unit_of_measurement=EUR_PER_KWH,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=3,
    value_fn=lambda d: d.get("heat_pump_cost_eur_per_kwh"),
    attrs_fn=lambda d: {
        "electricity_price_eur_per_kwh": d.get("electricity_price_eur_per_kwh"),
        "heat_pump_cop": d.get("heat_pump_cop"),
        "supply_temp": d.get("supply_temp"),
        "outdoor_temp": d.get("outdoor_temp"),
    },
)
GAS_BOILER_GAS_COST = HcoSensorEntityDescription(
    key="gas_cost",
    translation_key="gas_boiler_gas_cost",
    native_unit_of_measurement=EUR_PER_KWH,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=3,
    value_fn=lambda d: d.get("gas_cost_eur_per_kwh"),
    attrs_fn=lambda d: {"gas_price_eur_per_m3": d.get("gas_price_eur_per_m3")},
)
GAS_BOILER_COST_SAVINGS = HcoSensorEntityDescription(
    key="cost_savings",
    translation_key="gas_boiler_cost_savings",
    native_unit_of_measurement=EUR_PER_KWH,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=3,
    value_fn=lambda d: d.get("savings_eur_per_kwh"),
    attrs_fn=lambda d: {
        "savings_pct": d.get("savings_pct"),
        "prefer_gas_boiler": d.get("prefer_gas_boiler"),
        "gas_cheaper": d.get("gas_cheaper"),
        "comfort_at_risk": d.get("comfort_at_risk"),
    },
)


class HcoCoordinatorSensor(
    CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity
):
    """A sensor whose value and attributes are functions of coordinator data."""

    entity_description: HcoSensorEntityDescription
    _attr_has_entity_name = True
    _unrecorded_attributes = _FORECAST_ATTRIBUTES

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        description: HcoSensorEntityDescription,
        unique_id_prefix: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{unique_id_prefix}_{description.key}"
        self._attr_device_info = device

    @property
    def available(self) -> bool:
        """Available while the coordinator has data and a value exists."""
        data = self.coordinator.data
        if not self.coordinator.last_update_success or not data:
            return False
        if data.get("available") is False:
            return False
        return self.entity_description.value_fn(data) is not None

    @property
    def native_value(self) -> Any:
        """Return the value extracted from coordinator data."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the attributes extracted from coordinator data."""
        if not self.coordinator.data or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class CalculatedSupplyTemperatureSensor(
    CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity
):
    """Supply temperature of the plain heating curve at the current outdoor temperature."""

    _attr_has_entity_name = True
    _attr_translation_key = "calculated_supply_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        weather_coordinator: DataUpdateCoordinator[dict[str, Any]],
        config: dict[str, Any],
        unique_id_prefix: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(weather_coordinator)
        self._config = config
        self._attr_unique_id = f"{unique_id_prefix}_calculated_supply_temperature"
        self._attr_device_info = device

    @property
    def native_value(self) -> float | None:
        """Return the curve's supply temperature for the current outdoor temperature."""
        data = self.coordinator.data
        if not data or data.get("current_temperature") is None:
            return None
        return round(
            calculate_supply_temperature(
                float(data["current_temperature"]),
                water_min=float(
                    self._config.get(CONF_HEAT_CURVE_MIN, DEFAULT_HEAT_CURVE_MIN)
                ),
                water_max=float(
                    self._config.get(CONF_HEAT_CURVE_MAX, DEFAULT_HEAT_CURVE_MAX)
                ),
                outdoor_min=float(
                    self._config.get(
                        CONF_HEAT_CURVE_MIN_OUTDOOR, DEFAULT_HEAT_CURVE_MIN_OUTDOOR
                    )
                ),
                outdoor_max=float(
                    self._config.get(
                        CONF_HEAT_CURVE_MAX_OUTDOOR, DEFAULT_HEAT_CURVE_MAX_OUTDOOR
                    )
                ),
            ),
            1,
        )


class DiagnosticsSensor(SensorEntity):
    """Overall status of the coordinator chain."""

    _attr_has_entity_name = True
    _attr_translation_key = "diagnostics"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ok", "partial", "initializing", "error"]  # noqa: RUF012
    _attr_should_poll = False

    def __init__(
        self,
        coordinators: dict[str, DataUpdateCoordinator[dict[str, Any]]],
        unique_id_prefix: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        self._coordinators = coordinators
        self._attr_unique_id = f"{unique_id_prefix}_diagnostics"
        self._attr_device_info = device

    async def async_added_to_hass(self) -> None:
        """Follow every coordinator's updates."""
        for coordinator in self._coordinators.values():
            self.async_on_remove(
                coordinator.async_add_listener(self.async_write_ha_state)
            )

    @staticmethod
    def _ok(coordinator: DataUpdateCoordinator[dict[str, Any]]) -> bool:
        return bool(coordinator.last_update_success and coordinator.data)

    @property
    def native_value(self) -> str:
        """Return ok/partial/initializing/error."""
        healthy = sum(self._ok(c) for c in self._coordinators.values())
        if healthy == len(self._coordinators):
            return "ok"
        if any(
            c.data is None and c.last_update_success
            for c in self._coordinators.values()
        ):
            return "initializing" if healthy == 0 else "partial"
        return "error" if healthy == 0 else "partial"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-coordinator status and last update time."""
        attrs: dict[str, Any] = {}
        for name, coordinator in self._coordinators.items():
            attrs[f"{name}_ok"] = self._ok(coordinator)
            attrs[f"{name}_last_update"] = str(
                (coordinator.data or {}).get("timestamp")
            )
            if coordinator.last_exception is not None:
                attrs[f"{name}_error"] = str(coordinator.last_exception)
        return attrs


def _heat_pump_thermal_power(
    hass: HomeAssistant,
    config: dict[str, Any],
    weather_coordinator: DataUpdateCoordinator[dict[str, Any]],
    optimization_coordinator: DataUpdateCoordinator[dict[str, Any]],
) -> tuple[float, float, float] | None:
    """(thermal kW, COP, supply °C) from the heat pump's electricity meter.

    The supply temperature is the measured one when a supply sensor is
    configured, otherwise the one the optimizer planned for this step.
    """
    power_kw = read_power_kw(hass, config.get(CONF_POWER_CONSUMPTION))
    if power_kw is None or not weather_coordinator.data:
        return None
    supply = get_sensor_value(hass, config.get(CONF_SUPPLY_TEMPERATURE_SENSOR), None)
    if supply is None and optimization_coordinator.data:
        supply = _first(optimization_coordinator.data, "supply_temps")
    if supply is None:
        return None
    cop = HeatPumpConfig.from_config(config).cop_at(
        supply_temp=float(supply),
        outdoor_temp=float(weather_coordinator.data["current_temperature"]),
    )
    return max(0.0, power_kw) * cop, cop, float(supply)


class HeatPumpThermalPowerSensor(SensorEntity):
    """Thermal output of the heat pump: metered electrical power x modelled COP."""

    _attr_has_entity_name = True
    _attr_translation_key = "heat_pump_thermal_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_should_poll = False

    def __init__(
        self,
        config: dict[str, Any],
        weather_coordinator: DataUpdateCoordinator[dict[str, Any]],
        optimization_coordinator: DataUpdateCoordinator[dict[str, Any]],
        unique_id_prefix: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        self._config = config
        self._weather = weather_coordinator
        self._optimization = optimization_coordinator
        self._attr_unique_id = f"{unique_id_prefix}_heat_pump_thermal_power"
        self._attr_device_info = device
        self._attr_available = False

    async def async_added_to_hass(self) -> None:
        """Recompute on meter/supply sensor changes and coordinator updates."""
        sources = [
            s
            for s in (
                self._config.get(CONF_POWER_CONSUMPTION),
                self._config.get(CONF_SUPPLY_TEMPERATURE_SENSOR),
            )
            if s
        ]
        self.async_on_remove(
            async_track_state_change_event(self.hass, sources, self._handle_event)
        )
        for coordinator in (self._weather, self._optimization):
            self.async_on_remove(coordinator.async_add_listener(self._recompute))
        self._recompute()

    @callback
    def _handle_event(self, event: Event[EventStateChangedData]) -> None:
        self._recompute()

    @callback
    def _recompute(self) -> None:
        result = _heat_pump_thermal_power(
            self.hass, self._config, self._weather, self._optimization
        )
        if result is None:
            self._attr_available = False
        else:
            thermal_kw, cop, supply = result
            self._attr_available = True
            self._attr_native_value = round(thermal_kw, 3)
            self._attr_extra_state_attributes = {
                "cop": round(cop, 3),
                "supply_temperature": supply,
            }
        self.async_write_ha_state()


class HeatPumpThermalEnergySensor(RestoreSensor):
    """Cumulative thermal energy delivered by the heat pump (left Riemann sum)."""

    _attr_has_entity_name = True
    _attr_translation_key = "heat_pump_thermal_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_should_poll = False

    # Integration intervals longer than this are a data gap, not a reading.
    MAX_GAP_HOURS = 0.5

    def __init__(
        self,
        config: dict[str, Any],
        weather_coordinator: DataUpdateCoordinator[dict[str, Any]],
        optimization_coordinator: DataUpdateCoordinator[dict[str, Any]],
        unique_id_prefix: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        self._config = config
        self._weather = weather_coordinator
        self._optimization = optimization_coordinator
        self._attr_unique_id = f"{unique_id_prefix}_heat_pump_thermal_energy"
        self._attr_device_info = device
        self._total_kwh = 0.0
        self._last_time: datetime | None = None
        self._last_kw: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the total and start integrating meter updates."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._total_kwh = float(str(last.native_value))
            except (TypeError, ValueError):
                self._total_kwh = 0.0
        self._attr_native_value = round(self._total_kwh, 3)
        power_sensor = self._config.get(CONF_POWER_CONSUMPTION)
        if power_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [power_sensor], self._handle_event
                )
            )
        # A meter that holds a constant value emits no state changes; the
        # timer keeps the integral going between them.
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._handle_tick, timedelta(minutes=1)
            )
        )

    @callback
    def _handle_event(self, event: Event[EventStateChangedData]) -> None:
        self.integrate(dt_util.utcnow())

    @callback
    def _handle_tick(self, now: datetime) -> None:
        self.integrate(dt_util.utcnow())

    @callback
    def integrate(self, now: datetime) -> None:
        """Add the energy since the previous reading and store the new power."""
        result = _heat_pump_thermal_power(
            self.hass, self._config, self._weather, self._optimization
        )
        if self._last_time is not None and self._last_kw is not None:
            hours = (now - self._last_time).total_seconds() / 3600.0
            if 0 < hours <= self.MAX_GAP_HOURS:
                self._total_kwh += self._last_kw * hours
                self._attr_native_value = round(self._total_kwh, 3)
                self.async_write_ha_state()
        self._last_time = now
        self._last_kw = result[0] if result is not None else None


class TotalCostSavingsSensor(
    CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], RestoreSensor
):
    """Running total of the modelled savings of the executed plan.

    On every optimization result, the elapsed time since the previous one
    is booked against the previous plan's first step: its baseline cost
    minus its optimized cost, pro rata. Pre-heating shows up as negative
    savings when it happens and is paid back when the building coasts
    through expensive hours, so the total is not biased upwards.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "total_cost_savings"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    MAX_GAP_HOURS = 1.0

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        unique_id_prefix: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{unique_id_prefix}_total_cost_savings"
        self._attr_device_info = device
        self._total = 0.0
        self._last_time: datetime | None = None
        self._last_rate_eur_per_h: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the running total."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._total = float(str(last.native_value))
            except (TypeError, ValueError):
                self._total = 0.0
        self._attr_native_value = round(self._total, 4)

    @property
    def available(self) -> bool:
        """Always available: the total is meaningful without fresh data."""
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if not data or data.get("timestamp") is None:
            return
        now: datetime = data["timestamp"]
        if (
            self._last_time is not None
            and self._last_rate_eur_per_h is not None
            and now > self._last_time
        ):
            hours = (now - self._last_time).total_seconds() / 3600.0
            if hours <= self.MAX_GAP_HOURS:
                self._total += self._last_rate_eur_per_h * hours
                self._attr_native_value = round(self._total, 4)
        durations = data.get("step_durations_hours") or []
        costs = data.get("cost_eur") or []
        baseline = data.get("baseline_cost_eur") or []
        if durations and costs and baseline and durations[0] > 0:
            self._last_rate_eur_per_h = (baseline[0] - costs[0]) / durations[0]
            self._last_time = now
        super()._handle_coordinator_update()


def _optimization_entities(
    coordinator: DataUpdateCoordinator[dict[str, Any]],
    descriptions: list[HcoSensorEntityDescription],
    prefix: str,
    device: DeviceInfo,
) -> list[SensorEntity]:
    return [HcoCoordinatorSensor(coordinator, d, prefix, device) for d in descriptions]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    runtime_data = entry.runtime_data
    config = runtime_data.config
    device = runtime_data.device
    prefix = entry.entry_id
    weather = runtime_data.weather_coordinator
    heat = runtime_data.heat_coordinator
    optimization = runtime_data.optimization_coordinator

    entities: list[SensorEntity] = [
        HcoCoordinatorSensor(weather, OUTDOOR_TEMPERATURE, prefix, device),
        CalculatedSupplyTemperatureSensor(weather, config, prefix, device),
    ]

    if heat is not None and optimization is not None:
        heat_descriptions = [HEAT_LOSS, WINDOW_SOLAR_GAIN, NET_HEAT_LOSS]
        if config.get("pv_arrays"):
            heat_descriptions.append(PV_PRODUCTION_FORECAST)
        entities += _optimization_entities(heat, heat_descriptions, prefix, device)
        optimization_descriptions = [
            HEATING_CURVE_OFFSET,
            OPTIMIZED_SUPPLY_TEMPERATURE,
            PLANNED_INDOOR_TEMPERATURE,
            HEAT_BUFFER,
            PLANNED_COP,
            COST_SAVINGS_FORECAST,
            SHADOW_PRICE,
            CALIBRATION,
            MODEL_ACCURACY,
        ]
        if config.get(CONF_GRID_IMPORT_SENSOR) or config.get(CONF_GRID_EXPORT_SENSOR):
            optimization_descriptions.append(REALTIME_OFFSET)
        entities += _optimization_entities(
            optimization, optimization_descriptions, prefix, device
        )
        entities.append(TotalCostSavingsSensor(optimization, prefix, device))
        entities.append(
            DiagnosticsSensor(
                {"weather": weather, "heat": heat, "optimization": optimization},
                prefix,
                device,
            )
        )
        if config.get(CONF_POWER_CONSUMPTION):
            entities.append(
                HeatPumpThermalPowerSensor(
                    config, weather, optimization, prefix, device
                )
            )
            entities.append(
                HeatPumpThermalEnergySensor(
                    config, weather, optimization, prefix, device
                )
            )
    async_add_entities(entities)

    for subentry_id, zone in runtime_data.zones.items():
        zone_prefix = f"{entry.entry_id}_{subentry_id}"
        zone_device = zone["device"]
        zone_entities: list[SensorEntity] = [
            HcoCoordinatorSensor(
                zone["heat_coordinator"], NET_HEAT_LOSS, zone_prefix, zone_device
            )
        ]
        zone_entities += _optimization_entities(
            zone["optimization_coordinator"],
            [
                HEATING_CURVE_OFFSET,
                OPTIMIZED_SUPPLY_TEMPERATURE,
                PLANNED_INDOOR_TEMPERATURE,
                COST_SAVINGS_FORECAST,
            ],
            zone_prefix,
            zone_device,
        )
        async_add_entities(zone_entities, config_subentry_id=subentry_id)

    gas = runtime_data.gas_boiler_coordinator
    if gas is not None and runtime_data.gas_boiler_device is not None:
        gas_prefix = f"{entry.entry_id}_gas_boiler"
        async_add_entities(
            [
                HcoCoordinatorSensor(
                    gas, description, gas_prefix, runtime_data.gas_boiler_device
                )
                for description in (
                    GAS_BOILER_HEAT_PUMP_COST,
                    GAS_BOILER_GAS_COST,
                    GAS_BOILER_COST_SAVINGS,
                )
            ],
            config_subentry_id=runtime_data.gas_boiler_subentry_id,
        )
