"""Sensor platform for Heating Curve Optimizer.

All sensor classes are defined inline here (flat module structure).  The
former ``sensor/`` sub-package has been collapsed into this single file so
that the module graph mirrors battery_controller's approach: one file per
platform, no sub-packages.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .calibration import MIN_SAMPLES_TO_APPLY
from .entity import BaseUtilitySensor
from .helpers import coordinator_data_section, extract_price_forecast
from .const import (
    CONF_AREA_M2,
    CONF_BASE_COP,
    CONF_CEILING_HEIGHT,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_ENERGY_LABEL,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_K_FACTOR,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_POWER_CONSUMPTION,
    CONF_PRICE_SETTINGS,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_TIME_BASE,
    CONF_VENTILATION_TYPE,
    DEFAULT_COP_AT_35,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_K_FACTOR,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_THERMAL_STORAGE_EFFICIENCY,
    DEFAULT_TIME_BASE,
    DEFAULT_VENTILATION_TYPE,
    DEFAULT_CEILING_HEIGHT,
    DOMAIN,
    SOURCE_TYPE_CONSUMPTION,
    VENTILATION_TYPES,
    calculate_htc_from_energy_label,
    calculate_ventilation_htc,
)

_LOGGER = logging.getLogger(__name__)

# All entities are updated via their coordinator (push model); no parallel polling.
PARALLEL_UPDATES = 0


# ---------------------------------------------------------------------------
# Weather sensors
# ---------------------------------------------------------------------------


class CoordinatorOutdoorTemperatureSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Outdoor temperature sensor using weather coordinator."""

    _attr_translation_key = "outdoor_temperature"

    def __init__(
        self, coordinator: Any, name: str, unique_id: str, device: DeviceInfo
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="°C",
            device_class="temperature",
            icon="mdi:thermometer",
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self) -> float | None:
        """Return current temperature."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("current_temperature")
        return float(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "forecast": self.coordinator.data.get("temperature_forecast", []),
            "humidity_forecast": self.coordinator.data.get("humidity_forecast", []),
            "forecast_time_base": 60,
        }


# ---------------------------------------------------------------------------
# Heat sensors
# ---------------------------------------------------------------------------


class CoordinatorHeatLossSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Heat loss sensor using heat calculation coordinator."""

    _attr_translation_key = "heat_loss"
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self) -> float | None:
        """Return heat loss."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("heat_loss")
        return float(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast and diagnostic attributes."""
        if not self.coordinator.data:
            return {}

        config = self.coordinator.config
        area_m2 = config.get(CONF_AREA_M2, 0)
        energy_label = config.get(CONF_ENERGY_LABEL, "C")
        ventilation_type = config.get(CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE)
        ceiling_height = config.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)

        htc = calculate_htc_from_energy_label(
            energy_label,
            area_m2,
            ventilation_type=ventilation_type,
            ceiling_height=ceiling_height,
        )
        h_t = calculate_htc_from_energy_label(
            energy_label, area_m2, ventilation_type="none", ceiling_height=2.5
        ) - calculate_ventilation_htc(area_m2, "none", 2.5)
        h_v = calculate_ventilation_htc(area_m2, ventilation_type, ceiling_height)

        vent_data = VENTILATION_TYPES.get(ventilation_type, {})
        vent_name = vent_data.get("name_en", ventilation_type)
        volume = area_m2 * ceiling_height
        ach = vent_data.get("ach", 1.0)

        return {
            "forecast": self.coordinator.data.get("heat_loss_forecast", []),
            "forecast_time_base": 60,
            "htc_total_w_per_k": round(htc, 1),
            "htc_transmission_w_per_k": round(h_t, 1),
            "htc_ventilation_w_per_k": round(h_v, 1),
            "transmission_percentage": round(h_t / htc * 100, 1) if htc > 0 else 0,
            "ventilation_percentage": round(h_v / htc * 100, 1) if htc > 0 else 0,
            "energy_label": energy_label,
            "ventilation_type": ventilation_type,
            "ventilation_type_name": vent_name,
            "ceiling_height_m": ceiling_height,
            "building_volume_m3": round(volume, 1),
            "air_changes_per_hour": ach,
            "calculation_method": "HTC from energy label (NTA 8800) + ISO 13789 ventilation",
        }


class CoordinatorWindowSolarGainSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Solar gain sensor using heat calculation coordinator."""

    _attr_translation_key = "window_solar_gain"

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self) -> float | None:
        """Return solar gain."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("solar_gain")
        return float(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "forecast": self.coordinator.data.get("solar_gain_forecast", []),
            "forecast_time_base": 60,
        }


class CoordinatorPVProductionForecastSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """PV production forecast sensor using heat calculation coordinator."""

    _attr_translation_key = "pv_production_forecast"
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class="power",
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self) -> float | None:
        """Return current PV production forecast."""
        if not self.coordinator.data:
            return None
        forecast = self.coordinator.data.get("pv_production_forecast", [])
        return float(forecast[0]) if forecast else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "forecast": self.coordinator.data.get("pv_production_forecast", []),
            "forecast_time_base": 60,
        }


class CoordinatorNetHeatLossSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Net heat loss sensor using heat calculation coordinator."""

    _attr_translation_key = "net_heat_loss"
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self) -> float | None:
        """Return net heat loss."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("net_heat_loss")
        return float(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "forecast": self.coordinator.data.get("net_heat_loss_forecast", []),
            "forecast_time_base": 60,
        }


# ---------------------------------------------------------------------------
# Optimization sensors — shared base
# ---------------------------------------------------------------------------


class BaseOptimizationSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Base class for optimization sensors using coordinator."""

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
        *,
        unit: str = "°C",
        device_class: str | None = None,
        state_class: SensorStateClass = SensorStateClass.MEASUREMENT,
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit=unit,
            device_class=device_class,
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = state_class
        self._attr_should_poll = False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )


class CoordinatorHeatingCurveOffsetSensor(BaseOptimizationSensor):
    """Heating curve offset sensor using optimization coordinator."""

    _attr_translation_key = "heating_curve_offset"
    _unrecorded_attributes = frozenset(
        {
            "optimized_offsets",
            "buffer_evolution",
            "future_supply_temperatures",
            "baseline_supply_temperatures",
            "prices",
            "demand_forecast",
            "baseline_cop",
            "optimized_cop",
            "outdoor_forecast",
        }
    )

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator, name, unique_id, icon, device, unit="°C", device_class=None
        )

    @property
    def native_value(self) -> float | None:
        """Return optimized offset."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("optimized_offset")
        return float(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return optimization results."""
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data
        return {
            "optimized_offsets": data.get("optimized_offsets", []),
            "buffer_evolution": data.get("buffer_evolution", []),
            "initial_buffer": data.get("initial_buffer", 0.0),
            "previous_offset": data.get("previous_offset", 0),
            "future_supply_temperatures": data.get("future_supply_temperatures", []),
            "total_cost": data.get("total_cost", 0.0),
            "baseline_cost": data.get("baseline_cost", 0.0),
            "cost_savings": data.get("cost_savings", 0.0),
            "forecast_time_base": 60,
            "prices": data.get("prices", []),
            "demand_forecast": data.get("demand_forecast", []),
            "baseline_cop": data.get("baseline_cop", []),
            "optimized_cop": data.get("optimized_cop", []),
            "baseline_supply_temperatures": data.get(
                "baseline_supply_temperatures", []
            ),
            "outdoor_forecast": data.get("outdoor_forecast", []),
        }


class CoordinatorOptimizedSupplyTemperatureSensor(BaseOptimizationSensor):
    """Optimized supply temperature sensor using optimization coordinator."""

    _attr_translation_key = "optimized_supply_temperature"
    _unrecorded_attributes = frozenset(
        {"optimized_offsets", "future_supply_temperatures"}
    )

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            name,
            unique_id,
            icon,
            device,
            unit="°C",
            device_class="temperature",
        )

    @property
    def native_value(self) -> float | None:
        """Return optimized supply temperature."""
        if not self.coordinator.data:
            return None

        future_temps = self.coordinator.data.get("future_supply_temperatures", [])
        if not future_temps:
            return None

        return float(future_temps[0])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "optimized_offsets": self.coordinator.data.get("optimized_offsets", []),
            "future_supply_temperatures": self.coordinator.data.get(
                "future_supply_temperatures", []
            ),
            "forecast_time_base": 60,
        }


class CoordinatorHeatBufferSensor(BaseOptimizationSensor):
    """Heat buffer sensor using optimization coordinator."""

    _attr_translation_key = "heat_buffer"
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            name,
            unique_id,
            icon,
            device,
            unit="kWh",
            device_class="energy",
            state_class=SensorStateClass.TOTAL,
        )

    @property
    def native_value(self) -> float | None:
        """Return current buffer level."""
        if not self.coordinator.data:
            return None
        buffer_evolution = self.coordinator.data.get("buffer_evolution", [])
        return buffer_evolution[0] if buffer_evolution else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return buffer evolution forecast."""
        if not self.coordinator.data:
            return {}
        return {
            "forecast": self.coordinator.data.get("buffer_evolution", []),
            "forecast_time_base": 60,
            "initial_buffer": self.coordinator.data.get("initial_buffer", 0.0),
        }


class CoordinatorCostSavingsSensor(BaseOptimizationSensor):
    """Cost savings forecast sensor showing predicted optimization savings in EUR."""

    _attr_translation_key = "cost_savings_forecast"

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            name,
            unique_id,
            icon,
            device,
            unit="€",
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL,
        )

    @property
    def native_value(self) -> float | None:
        """Return cost savings in EUR."""
        if not self.coordinator.data:
            return None
        return float(self.coordinator.data.get("cost_savings", 0.0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return cost breakdown."""
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data
        baseline_cost = data.get("baseline_cost", 0.0)
        cost_savings = data.get("cost_savings", 0.0)
        savings_pct = (
            round(100 * cost_savings / baseline_cost, 1) if baseline_cost > 0 else 0.0
        )

        return {
            "total_cost_eur": round(data.get("total_cost", 0.0), 2),
            "baseline_cost_eur": round(baseline_cost, 2),
            "cost_savings_eur": round(cost_savings, 2),
            "savings_percentage": savings_pct,
            "planning_window_hours": len(data.get("optimized_offsets", [])),
        }


class TotalCostSavingsSensor(RestoreSensor, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Cumulative cost savings sensor tracking total savings since activation."""

    _attr_translation_key = "total_cost_savings"

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
        *,
        offset_sensor: str,
        outdoor_sensor: str,
        calculated_supply_sensor: str,
        consumption_price_sensor: str,
        heat_demand_sensor: str,
        k_factor: float,
        base_cop: float,
        outdoor_temp_coefficient: float,
        cop_compensation_factor: float,
        time_base: int = 60,
    ):
        """Initialize the sensor."""
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="€",
            device_class=SensorDeviceClass.MONETARY,
            icon=icon,
            visible=True,
            device=device,
        )
        self.hass = hass
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_should_poll = False
        self._attr_native_value = 0.0

        self.offset_sensor = offset_sensor
        self.outdoor_sensor = outdoor_sensor
        self.calculated_supply_sensor = calculated_supply_sensor
        self.consumption_price_sensor = consumption_price_sensor
        self.heat_demand_sensor = heat_demand_sensor

        self.k_factor = k_factor
        self.base_cop = base_cop
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.cop_compensation_factor = cop_compensation_factor
        self.time_base = time_base

        self._last_update: datetime | None = None
        self._total_savings = 0.0
        self._unsub_timer = None

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._total_savings = float(last_state.native_value)
            self._attr_native_value = self._total_savings
            _LOGGER.debug("Restored total cost savings: €%.3f", self._total_savings)

        update_interval = timedelta(minutes=self.time_base)
        self._unsub_timer = async_track_time_interval(
            self.hass, self._async_update_savings, update_interval
        )

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup when removed."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        await super().async_will_remove_from_hass()

    @callback  # type: ignore[untyped-decorator]
    async def _async_update_savings(self, now: datetime | None = None) -> None:
        """Update cumulative savings periodically."""
        offset_state = self.hass.states.get(self.offset_sensor)
        outdoor_state = self.hass.states.get(self.outdoor_sensor)
        calculated_supply_state = self.hass.states.get(self.calculated_supply_sensor)
        price_state = self.hass.states.get(self.consumption_price_sensor)
        demand_state = self.hass.states.get(self.heat_demand_sensor)

        if (
            not offset_state
            or not outdoor_state
            or not calculated_supply_state
            or not price_state
            or not demand_state
            or offset_state.state in ("unknown", "unavailable")
            or outdoor_state.state in ("unknown", "unavailable")
            or calculated_supply_state.state in ("unknown", "unavailable")
            or price_state.state in ("unknown", "unavailable")
            or demand_state.state in ("unknown", "unavailable")
        ):
            _LOGGER.debug("Not all sensors available for savings calculation")
            return

        try:
            current_offset = float(offset_state.state)
            outdoor_temp = float(outdoor_state.state)
            baseline_supply_temp = float(calculated_supply_state.state)
            current_price = float(price_state.state)
            heat_demand = float(demand_state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid sensor values for savings calculation")
            return

        if abs(current_offset) < 0.01 or heat_demand <= 0:
            return

        optimized_supply_temp = baseline_supply_temp + current_offset

        baseline_cop = (
            self.base_cop
            + self.outdoor_temp_coefficient * outdoor_temp
            - self.k_factor * (baseline_supply_temp - 35)
        ) * self.cop_compensation_factor
        baseline_cop = max(0.5, baseline_cop)

        optimized_cop = (
            self.base_cop
            + self.outdoor_temp_coefficient * outdoor_temp
            - self.k_factor * (optimized_supply_temp - 35)
        ) * self.cop_compensation_factor
        optimized_cop = max(0.5, optimized_cop)

        time_hours = self.time_base / 60.0

        baseline_electricity = (heat_demand / baseline_cop) * time_hours
        optimized_electricity = (heat_demand / optimized_cop) * time_hours

        baseline_cost = baseline_electricity * current_price
        optimized_cost = optimized_electricity * current_price
        period_savings = baseline_cost - optimized_cost

        if period_savings > 0:
            self._total_savings += period_savings
            self._attr_native_value = round(self._total_savings, 3)
            self._last_update = dt_util.utcnow()

            _LOGGER.debug(
                "Period savings: €%.4f (total: €%.3f)",
                period_savings,
                self._total_savings,
            )

            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if self._last_update:
            attrs["last_update"] = self._last_update.isoformat()
        attrs["time_base_minutes"] = self.time_base
        return attrs


# ---------------------------------------------------------------------------
# COP sensors
# ---------------------------------------------------------------------------


class CoordinatorQuadraticCopSensor(BaseUtilitySensor):
    """COP sensor that reads from supply and outdoor temperature sensors."""

    _attr_translation_key = "quadratic_cop"

    def __init__(
        self,
        hass: HomeAssistant,
        weather_coordinator: Any,
        name: str,
        unique_id: str,
        supply_sensor: str,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        cop_compensation_factor: float = DEFAULT_COP_COMPENSATION_FACTOR,
    ) -> None:
        """Initialize the COP sensor."""
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="",
            device_class=None,
            icon="mdi:alpha-c-circle",
            visible=True,
            device=device,
        )
        self.hass = hass
        self.weather_coordinator = weather_coordinator
        self.supply_sensor = supply_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.cop_compensation_factor = cop_compensation_factor
        self._attr_state_class = SensorStateClass.MEASUREMENT

    async def async_update(self) -> None:
        """Update COP based on supply and outdoor temperature."""
        s_state = self.hass.states.get(self.supply_sensor)
        if not s_state or s_state.state in ("unknown", "unavailable"):
            self._set_unavailable(f"Supply sensor {self.supply_sensor} unavailable")
            return

        try:
            supply_temp = float(s_state.state)
        except (ValueError, TypeError):
            self._set_unavailable("Invalid supply temperature")
            return

        weather_data = self.weather_coordinator.data
        if not weather_data:
            self._set_unavailable("No weather data available")
            return

        outdoor_temp = weather_data.get("current_temperature")
        if outdoor_temp is None:
            self._set_unavailable("No outdoor temperature")
            return

        cop = (
            self.base_cop
            + self.outdoor_temp_coefficient * outdoor_temp
            - self.k_factor * (supply_temp - 35)
        ) * self.cop_compensation_factor

        self._attr_native_value = round(max(1.0, cop), 2)
        self._mark_available()


class CoordinatorCalculatedSupplyTemperatureSensor(
    CoordinatorEntity,
    BaseUtilitySensor,  # type: ignore[misc]  # HA base class untyped
):
    """Calculated supply temperature based on heating curve and outdoor temp."""

    _attr_translation_key = "calculated_supply_temperature"

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        device: DeviceInfo,
        min_temp: float = 20.0,
        max_temp: float = 45.0,
        min_outdoor: float = -20.0,
        max_outdoor: float = 15.0,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="°C",
            device_class="temperature",
            icon="mdi:thermometer",
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.min_outdoor = min_outdoor
        self.max_outdoor = max_outdoor

    @property
    def native_value(self) -> float | None:
        """Calculate supply temperature from heating curve."""
        if not self.coordinator.data:
            return None

        raw_outdoor_temp = self.coordinator.data.get("current_temperature")
        if raw_outdoor_temp is None:
            return None
        outdoor_temp = float(raw_outdoor_temp)

        if outdoor_temp <= self.min_outdoor:
            supply_temp = self.max_temp
        elif outdoor_temp >= self.max_outdoor:
            supply_temp = self.min_temp
        else:
            temp_range = self.max_temp - self.min_temp
            outdoor_range = self.max_outdoor - self.min_outdoor
            supply_temp = self.max_temp - (
                (outdoor_temp - self.min_outdoor) / outdoor_range * temp_range
            )

        return float(round(supply_temp, 1))

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )


# ---------------------------------------------------------------------------
# Diagnostics sensor
# ---------------------------------------------------------------------------


class CoordinatorDiagnosticsSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Diagnostics sensor with all coordinator data."""

    _attr_translation_key = "diagnostics"

    def __init__(
        self,
        weather_coordinator: Any,
        heat_coordinator: Any,
        optimization_coordinator: Any,
        name: str,
        unique_id: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the diagnostics sensor."""
        CoordinatorEntity.__init__(self, weather_coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit=None,
            device_class=None,
            icon="mdi:information-outline",
            visible=True,
            device=device,
        )
        self._attr_should_poll = False
        self._attr_state_class = None
        self._attr_native_unit_of_measurement = None
        self.weather_coordinator = weather_coordinator
        self.heat_coordinator = heat_coordinator
        self.optimization_coordinator = optimization_coordinator

    @property
    def native_value(self) -> str:
        """Return status based on coordinator states."""
        success_count = sum(
            [
                self.weather_coordinator.last_update_success
                and self.weather_coordinator.data is not None,
                self.heat_coordinator.last_update_success
                and self.heat_coordinator.data is not None,
                self.optimization_coordinator.last_update_success
                and self.optimization_coordinator.data is not None,
            ]
        )

        if success_count == 3:
            return "OK"
        elif success_count == 2:
            return "PARTIAL"
        elif success_count == 1:
            return "INITIALIZING"
        else:
            return "ERROR"

    @property
    def available(self) -> bool:
        """Always available to show diagnostics."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all coordinator data as diagnostics."""
        attrs: dict[str, Any] = {}

        attrs["weather_available"] = (
            self.weather_coordinator.last_update_success
            and self.weather_coordinator.data is not None
        )
        attrs["heat_available"] = (
            self.heat_coordinator.last_update_success
            and self.heat_coordinator.data is not None
        )
        attrs["optimization_available"] = (
            self.optimization_coordinator.last_update_success
            and self.optimization_coordinator.data is not None
        )

        if self.weather_coordinator.data:
            attrs["weather_last_update"] = str(
                self.weather_coordinator.data.get("timestamp")
            )
            attrs["outdoor_temperature"] = self.weather_coordinator.data.get(
                "current_temperature"
            )
        else:
            attrs["weather_status"] = "No data yet"

        if self.heat_coordinator.data:
            attrs["heat_last_update"] = str(self.heat_coordinator.data.get("timestamp"))
            attrs["heat_loss"] = self.heat_coordinator.data.get("heat_loss")
            attrs["solar_gain"] = self.heat_coordinator.data.get("solar_gain")
            attrs["net_heat_loss"] = self.heat_coordinator.data.get("net_heat_loss")
        else:
            attrs["heat_status"] = "No data yet"

        if self.optimization_coordinator.data:
            attrs["optimization_last_update"] = str(
                self.optimization_coordinator.data.get("timestamp")
            )
            attrs["optimized_offset"] = self.optimization_coordinator.data.get(
                "optimized_offset"
            )
            attrs["total_cost"] = self.optimization_coordinator.data.get("total_cost")
            attrs["baseline_cost"] = self.optimization_coordinator.data.get(
                "baseline_cost"
            )
            attrs["cost_savings"] = self.optimization_coordinator.data.get(
                "cost_savings"
            )
        else:
            attrs["optimization_status"] = (
                "Waiting for first optimization run (starts within 5-10 seconds after startup)"
            )

        return attrs


# ---------------------------------------------------------------------------
# Event-driven sensors
# ---------------------------------------------------------------------------


class CurrentElectricityPriceSensor(BaseUtilitySensor):
    """Current electricity price sensor, tracking a price sensor in real-time."""

    _attr_translation_key = "current_consumption_price"
    _unrecorded_attributes = frozenset({"forecast_prices"})

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        price_sensor: str,
        source_type: str,
        price_settings: dict[str, float],
        icon: str,
        device: DeviceInfo,
    ) -> None:
        unit = "€/kWh"
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit=unit,
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.price_sensor = price_sensor
        self.source_type = source_type
        self.price_settings = price_settings
        self._extra_attrs: dict[str, Any] = {}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._extra_attrs

    async def async_update(self) -> None:
        state = self.hass.states.get(self.price_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            self._attr_available = False
            self._extra_attrs = {}
            _LOGGER.warning("Price sensor %s is unavailable", self.price_sensor)
            return
        try:
            base_price = float(state.state)
        except ValueError:
            self._attr_available = False
            self._extra_attrs = {}
            _LOGGER.warning("Price sensor %s has invalid state", self.price_sensor)
            return
        self._attr_available = True

        self._attr_native_value = round(base_price, 8)
        attrs: dict[str, Any] = {}
        for key in ("unit_of_measurement", "friendly_name", "device_class"):
            if key in state.attributes:
                attrs[key] = state.attributes[key]
        forecast = extract_price_forecast(state)
        if forecast:
            attrs["forecast_prices"] = forecast
        self._extra_attrs = attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                self.price_sensor,
                self._handle_price_change,
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()

    async def _handle_price_change(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            self._attr_available = False
            _LOGGER.warning("Price sensor %s is unavailable", self.price_sensor)
            return
        await self.async_update()
        if self.entity_id:
            self.async_write_ha_state()


class HeatPumpThermalPowerSensor(BaseUtilitySensor):
    """Calculate current thermal output of the heat pump."""

    _attr_translation_key = "heat_pump_thermal_power"

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        power_sensor: str,
        supply_sensor: str,
        outdoor_sensor: str | SensorEntity,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        cop_compensation_factor: float = 1.0,
    ):
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon="mdi:fire",
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.power_sensor = power_sensor
        self.supply_sensor = supply_sensor
        self.outdoor_sensor = outdoor_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.cop_compensation_factor = cop_compensation_factor

    async def async_update(self) -> None:
        p_state = self.hass.states.get(self.power_sensor)
        if p_state is None:
            self._set_unavailable(f"power sensor {self.power_sensor} not found")
            return
        if p_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"power sensor {self.power_sensor} has state '{p_state.state}'"
            )
            return

        s_state = self.hass.states.get(self.supply_sensor)
        if s_state is None:
            self._set_unavailable(f"supply sensor {self.supply_sensor} not found")
            return
        if s_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"supply sensor {self.supply_sensor} has state '{s_state.state}'"
            )
            return

        entity_id = (
            self.outdoor_sensor.entity_id
            if isinstance(self.outdoor_sensor, SensorEntity)
            else cast(str, self.outdoor_sensor)
        )
        sensor_name = entity_id or str(self.outdoor_sensor)

        if entity_id is None:
            self._set_unavailable("no outdoor sensor found")
            return

        o_state = self.hass.states.get(entity_id)
        if (
            isinstance(self.outdoor_sensor, SensorEntity)
            and self.outdoor_sensor.entity_id
        ):
            self.outdoor_sensor = self.outdoor_sensor.entity_id
            entity_id = cast(str, self.outdoor_sensor)
            sensor_name = entity_id

        if o_state is None:
            self._set_unavailable(f"outdoor sensor not found ({sensor_name})")
            return
        if o_state.state in ("unknown", "unavailable"):
            self._set_unavailable(
                f"outdoor sensor {sensor_name} has state '{o_state.state}'"
            )
            return

        try:
            power = float(p_state.state)
        except ValueError:
            self._set_unavailable(f"power sensor {self.power_sensor} has invalid value")
            return
        try:
            s_temp = float(s_state.state)
        except ValueError:
            self._set_unavailable(
                f"supply sensor {self.supply_sensor} has invalid value"
            )
            return
        try:
            o_temp = float(o_state.state)
        except ValueError:
            self._set_unavailable(f"outdoor sensor {sensor_name} has invalid value")
            return
        cop = (
            self.base_cop
            + self.outdoor_temp_coefficient * o_temp
            - self.k_factor * (s_temp - 35)
        ) * self.cop_compensation_factor
        cop = max(0.5, cop)
        thermal_power = power * cop / 1000.0
        _LOGGER.debug(
            "Thermal power calc power=%s cop=%s -> %s",
            power,
            cop,
            thermal_power,
        )
        self._mark_available()
        self._attr_native_value = round(thermal_power, 3)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if isinstance(self.outdoor_sensor, SensorEntity):
            self.outdoor_sensor = self.outdoor_sensor.entity_id


class CopEfficiencyDeltaSensor(BaseUtilitySensor):
    """Predict COP deltas for future offsets."""

    _attr_translation_key = "cop_delta"
    _unrecorded_attributes = frozenset({"future_cop", "cop_deltas"})

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        *,
        cop_sensor: str | SensorEntity,
        offset_entity: str | SensorEntity,
        outdoor_sensor: str | SensorEntity,
        calculated_supply_sensor: str | SensorEntity,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        cop_compensation_factor: float = 1.0,
    ) -> None:
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="",
            device_class=None,
            icon="mdi:alpha-c-circle",
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.cop_sensor = cop_sensor
        self.offset_entity = offset_entity
        self.outdoor_sensor = outdoor_sensor
        self.calculated_supply_sensor = calculated_supply_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.cop_compensation_factor = cop_compensation_factor
        self._extra_attrs: dict[str, list[float] | float] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float] | float]:
        return self._extra_attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for ent in (
            self._resolve_entity_id(self.cop_sensor),
            self._resolve_entity_id(self.offset_entity),
            self._resolve_entity_id(self.outdoor_sensor),
            self._resolve_entity_id(self.calculated_supply_sensor),
        ):
            if ent is None:
                continue
            self.async_on_remove(
                async_track_state_change_event(self.hass, ent, self._handle_change)
            )

    async def _handle_change(
        self, event: Event
    ) -> None:  # pragma: no cover - simple callback
        await self.async_update()
        self.async_write_ha_state()

    def _resolve_entity_id(self, entity_ref: str | SensorEntity) -> str | None:
        """Return the entity_id for a reference or None if unavailable."""
        if isinstance(entity_ref, SensorEntity):
            entity_id = entity_ref.entity_id
            if entity_id is not None:
                if entity_ref is self.cop_sensor:
                    self.cop_sensor = entity_id
                elif entity_ref is self.offset_entity:
                    self.offset_entity = entity_id
                elif entity_ref is self.outdoor_sensor:
                    self.outdoor_sensor = entity_id
                elif entity_ref is self.calculated_supply_sensor:
                    self.calculated_supply_sensor = entity_id
            return str(entity_id) if entity_id is not None else None
        return cast(str, entity_ref)

    def _get_state(self, entity_ref: str | SensorEntity) -> State | None:
        """Return hass state for the given entity reference."""
        entity_id = self._resolve_entity_id(entity_ref)
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return state

    async def async_update(self) -> None:
        offset_state = self._get_state(self.offset_entity)
        outdoor_state = self._get_state(self.outdoor_sensor)
        calculated_supply_state = self._get_state(self.calculated_supply_sensor)

        if (
            offset_state is None
            or outdoor_state is None
            or calculated_supply_state is None
            or outdoor_state.state in ("unknown", "unavailable")
            or calculated_supply_state.state in ("unknown", "unavailable")
        ):
            self._attr_available = False
            return

        try:
            outdoor_temp = float(outdoor_state.state)
            baseline_supply_temp = float(calculated_supply_state.state)
        except ValueError:
            self._attr_available = False
            return

        supply_temps = offset_state.attributes.get("future_supply_temperatures")
        if not supply_temps:
            self._attr_available = False
            return

        try:
            current_offset = float(offset_state.state)
        except (ValueError, TypeError):
            current_offset = 0.0

        baseline_cop = (
            self.base_cop
            + self.outdoor_temp_coefficient * outdoor_temp
            - self.k_factor * (baseline_supply_temp - 35)
        ) * self.cop_compensation_factor
        baseline_cop = max(0.5, baseline_cop)

        if abs(current_offset) < 0.01:
            self._attr_native_value = 0.0
            self._extra_attrs = {
                "future_cop": [round(baseline_cop, 3)] * len(supply_temps),
                "cop_deltas": [0.0] * len(supply_temps),
                "baseline_cop": round(baseline_cop, 3),
            }
            self._attr_available = True
            return

        predicted_cops = [
            max(
                0.5,
                (
                    self.base_cop
                    + self.outdoor_temp_coefficient * outdoor_temp
                    - self.k_factor * (float(s_temp) - 35)
                )
                * self.cop_compensation_factor,
            )
            for s_temp in supply_temps
        ]
        cop_deltas = [round(c - baseline_cop, 3) for c in predicted_cops]
        self._extra_attrs = {
            "future_cop": [round(c, 3) for c in predicted_cops],
            "cop_deltas": cop_deltas,
            "baseline_cop": round(baseline_cop, 3),
        }
        self._attr_native_value = cop_deltas[0] if cop_deltas else 0.0
        self._attr_available = True


class HeatGenerationDeltaSensor(BaseUtilitySensor):
    """Calculate buffer change rate based on offset and heat demand."""

    _attr_translation_key = "heat_generation_delta"
    _unrecorded_attributes = frozenset({"future_buffer_change_rates"})

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        *,
        thermal_power_sensor: str | SensorEntity,
        cop_sensor: str | SensorEntity,
        offset_entity: str | SensorEntity,
        outdoor_sensor: str | SensorEntity,
        calculated_supply_sensor: str | SensorEntity,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        cop_compensation_factor: float = 1.0,
    ) -> None:
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="kW",
            device_class=None,
            icon="mdi:fire",
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self.hass = hass
        self.thermal_power_sensor = thermal_power_sensor
        self.cop_sensor = cop_sensor
        self.offset_entity = offset_entity
        self.outdoor_sensor = outdoor_sensor
        self.calculated_supply_sensor = calculated_supply_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.cop_compensation_factor = cop_compensation_factor
        self._extra_attrs: dict[str, list[float] | float | str] = {}

    @property
    def extra_state_attributes(self) -> dict[str, list[float] | float | str]:
        return self._extra_attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        offset_entity_id = self._resolve_entity_id(self.offset_entity)
        if offset_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, offset_entity_id, self._handle_change
                )
            )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                "sensor.heating_curve_optimizer_net_heat_loss",
                self._handle_change,
            )
        )

    async def _handle_change(
        self, event: Event
    ) -> None:  # pragma: no cover - simple callback
        await self.async_update()
        self.async_write_ha_state()

    def _resolve_entity_id(self, entity_ref: str | SensorEntity) -> str | None:
        """Return the entity_id for a reference or None if unavailable."""
        if isinstance(entity_ref, SensorEntity):
            entity_id = entity_ref.entity_id
            if entity_id is not None:
                if entity_ref is self.thermal_power_sensor:
                    self.thermal_power_sensor = entity_id
                elif entity_ref is self.cop_sensor:
                    self.cop_sensor = entity_id
                elif entity_ref is self.offset_entity:
                    self.offset_entity = entity_id
                elif entity_ref is self.outdoor_sensor:
                    self.outdoor_sensor = entity_id
                elif entity_ref is self.calculated_supply_sensor:
                    self.calculated_supply_sensor = entity_id
            return str(entity_id) if entity_id is not None else None
        return cast(str, entity_ref)

    def _get_state(self, entity_ref: str | SensorEntity) -> State | None:
        """Return hass state for the given entity reference."""
        entity_id = self._resolve_entity_id(entity_ref)
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return state

    async def async_update(self) -> None:
        """Calculate buffer change rate based on offset and heat demand."""
        offset_state = self._get_state(self.offset_entity)

        if offset_state is None:
            self._attr_available = False
            return

        try:
            current_offset = float(offset_state.state)
        except (ValueError, TypeError):
            self._attr_available = False
            return

        if abs(current_offset) < 0.01:
            self._attr_native_value = 0.0
            self._extra_attrs = {
                "buffer_change_rate": 0.0,
                "future_buffer_change_rates": [],
                "explanation": "No offset applied - no buffer change",
            }
            self._attr_available = True
            return

        demand_forecast = offset_state.attributes.get("demand_forecast", [])
        optimized_offsets = offset_state.attributes.get("optimized_offsets", [])

        if not demand_forecast or not optimized_offsets:
            net_heat_loss_state = self.hass.states.get(
                "sensor.heating_curve_optimizer_net_heat_loss"
            )
            if net_heat_loss_state is None or net_heat_loss_state.state in (
                "unknown",
                "unavailable",
            ):
                self._attr_available = False
                return

            try:
                current_heat_demand = max(0.0, float(net_heat_loss_state.state))
            except (ValueError, TypeError):
                self._attr_available = False
                return

            buffer_change_rate = (
                current_offset
                * current_heat_demand
                * DEFAULT_THERMAL_STORAGE_EFFICIENCY
            )

            self._attr_native_value = round(buffer_change_rate, 3)
            self._extra_attrs = {
                "buffer_change_rate": round(buffer_change_rate, 3),
                "current_offset": current_offset,
                "current_heat_demand": round(current_heat_demand, 3),
                "thermal_storage_efficiency": DEFAULT_THERMAL_STORAGE_EFFICIENCY,
                "explanation": (
                    f"Buffer changing at {buffer_change_rate:.3f} kW "
                    f"(offset {current_offset}°C × demand {current_heat_demand:.3f} kW "
                    f"× efficiency {DEFAULT_THERMAL_STORAGE_EFFICIENCY})"
                ),
            }
            self._attr_available = True
            return

        future_buffer_change_rates = [
            round(
                offset * max(0.0, demand) * DEFAULT_THERMAL_STORAGE_EFFICIENCY,
                3,
            )
            for offset, demand in zip(optimized_offsets, demand_forecast)
        ]

        if demand_forecast:
            current_heat_demand = max(0.0, demand_forecast[0])
            buffer_change_rate = (
                current_offset
                * current_heat_demand
                * DEFAULT_THERMAL_STORAGE_EFFICIENCY
            )
        else:
            buffer_change_rate = 0.0
            current_heat_demand = 0.0

        self._attr_native_value = round(buffer_change_rate, 3)
        self._extra_attrs = {
            "buffer_change_rate": round(buffer_change_rate, 3),
            "future_buffer_change_rates": future_buffer_change_rates,
            "current_offset": current_offset,
            "current_heat_demand": round(current_heat_demand, 3),
            "thermal_storage_efficiency": DEFAULT_THERMAL_STORAGE_EFFICIENCY,
            "explanation": (
                f"Buffer changing at {buffer_change_rate:.3f} kW "
                f"(offset {current_offset}°C × demand {current_heat_demand:.3f} kW "
                f"× efficiency {DEFAULT_THERMAL_STORAGE_EFFICIENCY})"
            ),
        }
        self._attr_available = True


# ---------------------------------------------------------------------------
# Daily utility sensors
# ---------------------------------------------------------------------------


class HeatPumpEnergyDailySensor(RestoreSensor, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Daily utility sensor tracking heat pump generated thermal energy in kWh."""

    _attr_translation_key = "heat_pump_energy_daily"

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
        *,
        thermal_power_sensor: str,
    ) -> None:
        """Initialize the sensor."""
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kWh",
            device_class=SensorDeviceClass.ENERGY,
            icon=icon,
            visible=True,
            device=device,
        )
        self.hass = hass
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_should_poll = False
        self._attr_native_value = 0.0

        self.thermal_power_sensor = thermal_power_sensor

        self._last_update: datetime | None = None
        self._last_reset: datetime | None = None
        self._daily_total = 0.0
        self._unsub_timer = None
        self._unsub_state = None

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        last_sensor_data = await self.async_get_last_sensor_data()
        if last_sensor_data and last_sensor_data.native_value is not None:
            self._daily_total = float(last_sensor_data.native_value)
            self._attr_native_value = self._daily_total
            _LOGGER.debug(
                "Restored daily heat pump energy: %.3f kWh", self._daily_total
            )

        now = dt_util.utcnow()
        last_state = await self.async_get_last_state()
        if last_state and last_state.last_updated:
            last_date = last_state.last_updated.date()
            current_date = now.date()
            if current_date > last_date:
                _LOGGER.info(
                    "New day detected, resetting daily heat pump energy counter"
                )
                self._daily_total = 0.0
                self._attr_native_value = 0.0
                self._last_reset = now

        update_interval = timedelta(minutes=5)
        self._unsub_timer = async_track_time_interval(
            self.hass, self._async_update_energy, update_interval
        )

        self._unsub_state = async_track_state_change_event(
            self.hass,
            [self.thermal_power_sensor],
            self._handle_state_change,
        )

        self._schedule_daily_reset()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup when removed."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        await super().async_will_remove_from_hass()

    def _schedule_daily_reset(self) -> None:
        """Schedule reset at midnight."""
        now = dt_util.utcnow()
        tomorrow = now.date() + timedelta(days=1)
        next_midnight = dt_util.as_utc(datetime.combine(tomorrow, datetime.min.time()))

        async def _reset_at_midnight(_now: datetime | None) -> None:
            """Reset counter at midnight."""
            _LOGGER.info("Midnight reset: Daily heat pump energy counter")
            self._daily_total = 0.0
            self._attr_native_value = 0.0
            self._last_reset = dt_util.utcnow()
            self.async_write_ha_state()
            self._schedule_daily_reset()

        delta = next_midnight - now
        self.hass.loop.call_later(
            delta.total_seconds(),
            lambda: self.hass.async_create_task(_reset_at_midnight(None)),
        )

    async def _handle_state_change(self, event: Event) -> None:
        """Handle state change of thermal power sensor."""
        await self._async_update_energy()

    async def _async_update_energy(self, now: datetime | None = None) -> None:
        """Update cumulative energy periodically."""
        current_time = dt_util.utcnow()

        thermal_state = self.hass.states.get(self.thermal_power_sensor)

        if not thermal_state or thermal_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Thermal power sensor not available")
            return

        try:
            thermal_power_kw = float(thermal_state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid thermal power value: %s", thermal_state.state)
            return

        if self._last_update:
            time_delta_hours = (
                current_time - self._last_update
            ).total_seconds() / 3600.0
            energy_delta = thermal_power_kw * time_delta_hours

            if energy_delta > 0 and time_delta_hours < 1.0:
                self._daily_total += energy_delta
                self._attr_native_value = round(self._daily_total, 3)

                _LOGGER.debug(
                    "Energy update: +%.4f kWh (total: %.3f kWh, power: %.2f kW, duration: %.2f h)",
                    energy_delta,
                    self._daily_total,
                    thermal_power_kw,
                    time_delta_hours,
                )

                self.async_write_ha_state()

        self._last_update = current_time

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if self._last_update:
            attrs["last_update"] = self._last_update.isoformat()
        if self._last_reset:
            attrs["last_reset"] = self._last_reset.isoformat()
        attrs["source_sensor"] = self.thermal_power_sensor
        return attrs


class NetHeatLossEnergyDailySensor(RestoreSensor, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Daily utility sensor tracking net heat loss energy in kWh."""

    _attr_translation_key = "net_heat_loss_energy_daily"

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
        *,
        net_heat_loss_sensor: str,
    ) -> None:
        """Initialize the sensor."""
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kWh",
            device_class=SensorDeviceClass.ENERGY,
            icon=icon,
            visible=True,
            device=device,
        )
        self.hass = hass
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_should_poll = False
        self._attr_native_value = 0.0

        self.net_heat_loss_sensor = net_heat_loss_sensor

        self._last_update: datetime | None = None
        self._last_reset: datetime | None = None
        self._daily_total = 0.0
        self._unsub_timer = None
        self._unsub_state = None

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        last_sensor_data = await self.async_get_last_sensor_data()
        if last_sensor_data and last_sensor_data.native_value is not None:
            self._daily_total = float(last_sensor_data.native_value)
            self._attr_native_value = self._daily_total
            _LOGGER.debug("Restored daily net heat loss: %.3f kWh", self._daily_total)

        now = dt_util.utcnow()
        last_state = await self.async_get_last_state()
        if last_state and last_state.last_updated:
            last_date = last_state.last_updated.date()
            current_date = now.date()
            if current_date > last_date:
                _LOGGER.info("New day detected, resetting daily net heat loss counter")
                self._daily_total = 0.0
                self._attr_native_value = 0.0
                self._last_reset = now

        update_interval = timedelta(minutes=5)
        self._unsub_timer = async_track_time_interval(
            self.hass, self._async_update_energy, update_interval
        )

        self._unsub_state = async_track_state_change_event(
            self.hass,
            [self.net_heat_loss_sensor],
            self._handle_state_change,
        )

        self._schedule_daily_reset()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup when removed."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        await super().async_will_remove_from_hass()

    def _schedule_daily_reset(self) -> None:
        """Schedule reset at midnight."""
        now = dt_util.utcnow()
        tomorrow = now.date() + timedelta(days=1)
        next_midnight = dt_util.as_utc(datetime.combine(tomorrow, datetime.min.time()))

        async def _reset_at_midnight(_now: datetime | None) -> None:
            """Reset counter at midnight."""
            _LOGGER.info("Midnight reset: Daily net heat loss counter")
            self._daily_total = 0.0
            self._attr_native_value = 0.0
            self._last_reset = dt_util.utcnow()
            self.async_write_ha_state()
            self._schedule_daily_reset()

        delta = next_midnight - now
        self.hass.loop.call_later(
            delta.total_seconds(),
            lambda: self.hass.async_create_task(_reset_at_midnight(None)),
        )

    async def _handle_state_change(self, event: Event) -> None:
        """Handle state change of net heat loss sensor."""
        await self._async_update_energy()

    async def _async_update_energy(self, now: datetime | None = None) -> None:
        """Update cumulative energy periodically."""
        current_time = dt_util.utcnow()

        heat_loss_state = self.hass.states.get(self.net_heat_loss_sensor)

        if not heat_loss_state or heat_loss_state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Net heat loss sensor not available")
            return

        try:
            heat_loss_kw = float(heat_loss_state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid net heat loss value: %s", heat_loss_state.state)
            return

        if self._last_update:
            time_delta_hours = (
                current_time - self._last_update
            ).total_seconds() / 3600.0
            energy_delta = max(0, heat_loss_kw) * time_delta_hours

            if time_delta_hours < 1.0:
                self._daily_total += energy_delta
                self._attr_native_value = round(self._daily_total, 3)

                _LOGGER.debug(
                    "Energy update: +%.4f kWh (total: %.3f kWh, power: %.2f kW, duration: %.2f h)",
                    energy_delta,
                    self._daily_total,
                    heat_loss_kw,
                    time_delta_hours,
                )

                self.async_write_ha_state()

        self._last_update = current_time

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if self._last_update:
            attrs["last_update"] = self._last_update.isoformat()
        if self._last_reset:
            attrs["last_reset"] = self._last_reset.isoformat()
        attrs["source_sensor"] = self.net_heat_loss_sensor
        return attrs


# ---------------------------------------------------------------------------
# Gas boiler comparison sensors — shared base
# ---------------------------------------------------------------------------


class BaseGasBoilerSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Base class for sensors reading from GasBoilerCoordinator."""

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
        *,
        unit: str = "€/kWh",
        device_class: str | None = None,
        state_class: SensorStateClass = SensorStateClass.MEASUREMENT,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit=unit,
            device_class=device_class,
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = state_class
        self._attr_should_poll = False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and bool(self.coordinator.data.get("available", False))
        )


class GasBoilerHeatPumpCostSensor(BaseGasBoilerSensor):
    """Current heat-pump cost per kWh of heat delivered."""

    _attr_translation_key = "gas_boiler_heat_pump_cost"

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, unique_id, icon, device)

    @property
    def native_value(self) -> float | None:
        """Return the heat pump's cost per kWh thermal."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("heat_pump_cost_eur_per_kwh")
        return round(float(value), 5) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return supporting values behind the cost figure."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        return {
            "electricity_price_eur_per_kwh": data.get("electricity_price_eur_per_kwh"),
            "heat_pump_cop": data.get("heat_pump_cop"),
            "supply_temp": data.get("supply_temp"),
            "outdoor_temp": data.get("outdoor_temp"),
        }


class GasBoilerGasCostSensor(BaseGasBoilerSensor):
    """Current gas-boiler cost per kWh of heat delivered."""

    _attr_translation_key = "gas_boiler_gas_cost"

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, unique_id, icon, device)

    @property
    def native_value(self) -> float | None:
        """Return the gas boiler's cost per kWh thermal."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("gas_cost_eur_per_kwh")
        return round(float(value), 5) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return supporting values behind the cost figure."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        return {
            "gas_price_eur_per_m3": data.get("gas_price_eur_per_m3"),
            "efficiency": self.coordinator.config.get("gas_boiler_efficiency"),
            "calorific_value_kwh_per_m3": self.coordinator.config.get(
                "gas_calorific_value_kwh_per_m3"
            ),
        }


class GasBoilerCostSavingsSensor(BaseGasBoilerSensor):
    """Signed savings per kWh thermal: positive means gas is cheaper."""

    _attr_translation_key = "gas_boiler_cost_savings"

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, unique_id, icon, device)

    @property
    def native_value(self) -> float | None:
        """Return heat-pump-cost minus gas-cost, per kWh thermal."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("savings_eur_per_kwh")
        return round(float(value), 5) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the percentage form and the underlying recommendation."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        return {
            "savings_pct": data.get("savings_pct"),
            "prefer_gas_boiler": data.get("prefer_gas_boiler"),
            "heat_currently_needed": data.get("heat_currently_needed"),
        }


# ---------------------------------------------------------------------------
# Standalone sensors (formerly sensor_thermal_calibration.py and
# sensor_realtime_offset.py)
# ---------------------------------------------------------------------------


class ThermalCalibrationSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """Sample count and status of the thermal calibration fit."""

    _attr_translation_key = "thermal_calibration"

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="samples",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_should_poll = False
        self._attr_entity_registry_enabled_default = False  # opt-in diagnostic

    def _thermal_v2(self) -> dict[str, Any]:
        return coordinator_data_section(self.coordinator, "thermal_v2")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self._thermal_v2().get("available", False)
        )

    @property
    def native_value(self) -> float:
        """Return the number of samples behind the current fit."""
        return float(self._thermal_v2().get("calibration_sample_count", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the learned values, whether they're applied, and the prior."""
        data = self._thermal_v2()
        applied = data.get("calibration_applied", False)
        return {
            "applied": applied,
            "sample_count": data.get("calibration_sample_count", 0),
            "min_samples_to_apply": MIN_SAMPLES_TO_APPLY,
            "last_result": data.get("calibration_last_result"),
            "learned_ua_w_per_k": (
                data.get("building_ua_w_per_k") if applied else None
            ),
            "learned_thermal_mass_kwh_per_k": (
                data.get("building_thermal_mass_kwh_per_k") if applied else None
            ),
        }


class RealtimeOffsetAdjustmentSensor(CoordinatorEntity, BaseUtilitySensor):  # type: ignore[misc]  # HA base class untyped
    """The real-time controller's current adjustment to the planned offset."""

    _attr_translation_key = "realtime_offset_adjustment"

    def __init__(
        self,
        coordinator: Any,
        name: str,
        unique_id: str,
        icon: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="°C",
            device_class=None,
            icon=icon,
            visible=True,
            device=device,
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False
        self._attr_entity_registry_enabled_default = False  # opt-in diagnostic

    def _realtime(self) -> dict[str, Any]:
        return coordinator_data_section(self.coordinator, "realtime")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data is not None and bool(self._realtime())

    @property
    def native_value(self) -> float | None:
        """Return the current offset adjustment, in whole degrees."""
        value = self._realtime().get("adjustment")
        return float(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the planned/effective offset and the inputs behind the adjustment."""
        data = self._realtime()
        if not data:
            return {}
        return {
            "planned_offset": data.get("planned_offset"),
            "effective_offset": data.get("effective_offset"),
            "current_grid_w": data.get("current_grid_w"),
            "shadow_price_eur_per_kwh": data.get("shadow_price_eur_per_kwh"),
        }


# ---------------------------------------------------------------------------
# CalibrationSensor (formerly calibration_sensor.py)
# ---------------------------------------------------------------------------

# Import the full CalibrationSensor from its dedicated module (it is ~780
# lines of history-analysis logic that lives in calibration_sensor.py - keep
# it there for readability, but expose it from this platform module so HA
# resolves the sensor platform from here).
from .calibration_sensor import CalibrationSensor  # noqa: E402  # local re-export


# ---------------------------------------------------------------------------
# async_setup_entry  (from sensor/__init__.py)
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors from a config entry."""
    _LOGGER.debug("Setting up sensors for entry %s", entry.entry_id)

    runtime_data = entry.runtime_data
    weather_coordinator = runtime_data.weather_coordinator
    heat_coordinator = runtime_data.heat_coordinator
    optimization_coordinator = runtime_data.optimization_coordinator
    device = runtime_data.device
    config = runtime_data.config

    if heat_coordinator is not None and optimization_coordinator is not None:
        entities = _build_primary_zone_entities(
            hass,
            entry,
            config,
            device,
            weather_coordinator,
            heat_coordinator,
            optimization_coordinator,
        )
        _LOGGER.debug("Adding %d sensor entities", len(entities))
        async_add_entities(entities)
    else:
        _LOGGER.debug(
            "Skipping primary heating zone sensors for %s: no primary "
            "heating zone configured yet",
            entry.entry_id,
        )

    for subentry_id, zone_data in runtime_data.zones.items():
        zone_optimization_coordinator = zone_data.get("optimization_coordinator")
        zone_heat_coordinator = zone_data.get("heat_coordinator")
        zone_device = zone_data.get("device")
        if not (
            zone_optimization_coordinator and zone_heat_coordinator and zone_device
        ):
            continue
        zone_entry_id = f"{entry.entry_id}_{subentry_id}"
        async_add_entities(
            [
                CoordinatorHeatingCurveOffsetSensor(
                    coordinator=zone_optimization_coordinator,
                    name="Heating Curve Offset",
                    unique_id=f"{zone_entry_id}_heating_curve_offset",
                    icon="mdi:chart-line",
                    device=zone_device,
                ),
                CoordinatorOptimizedSupplyTemperatureSensor(
                    coordinator=zone_optimization_coordinator,
                    name="Optimized Supply Temperature",
                    unique_id=f"{zone_entry_id}_optimized_supply_temperature",
                    icon="mdi:thermometer-chevron-up",
                    device=zone_device,
                ),
                CoordinatorNetHeatLossSensor(
                    coordinator=zone_heat_coordinator,
                    name="Net Heat Loss",
                    unique_id=f"{zone_entry_id}_net_heat_loss",
                    icon="mdi:fire-off",
                    device=zone_device,
                ),
            ],
            config_subentry_id=subentry_id,
        )

    gas_boiler_coordinator = runtime_data.gas_boiler_coordinator
    gas_boiler_device = runtime_data.gas_boiler_device
    gas_boiler_subentry_id = runtime_data.gas_boiler_subentry_id
    if gas_boiler_coordinator is not None and gas_boiler_device is not None:
        gas_boiler_entry_id = f"{entry.entry_id}_gas_boiler"
        async_add_entities(
            [
                GasBoilerHeatPumpCostSensor(
                    coordinator=gas_boiler_coordinator,
                    name="Gas Boiler Heat Pump Cost",
                    unique_id=f"{gas_boiler_entry_id}_heat_pump_cost",
                    icon="mdi:heat-pump",
                    device=gas_boiler_device,
                ),
                GasBoilerGasCostSensor(
                    coordinator=gas_boiler_coordinator,
                    name="Gas Boiler Gas Cost",
                    unique_id=f"{gas_boiler_entry_id}_gas_cost",
                    icon="mdi:fire",
                    device=gas_boiler_device,
                ),
                GasBoilerCostSavingsSensor(
                    coordinator=gas_boiler_coordinator,
                    name="Gas Boiler Cost Savings",
                    unique_id=f"{gas_boiler_entry_id}_cost_savings",
                    icon="mdi:piggy-bank-outline",
                    device=gas_boiler_device,
                ),
            ],
            config_subentry_id=gas_boiler_subentry_id,
        )

    # Migration: remove legacy None-subentry device associations.
    # Before per-subentry device support, zone/gas-boiler devices were registered
    # under the main config entry (subentry=None). After async_add_entities with
    # config_subentry_id, the device has both None and the subentry ID in its
    # associations, causing it to appear in "not under a sub-item" and the correct
    # subentry in the HA UI. Mirror battery_controller's cleanup pattern.
    device_registry = dr.async_get(hass)
    _get_dev = getattr(device_registry, "async_get_device_by_identifier", None)
    subentry_device_ids: list[str] = [
        f"{entry.entry_id}_{sid}" for sid in runtime_data.zones
    ]
    if gas_boiler_subentry_id is not None:
        subentry_device_ids.append(f"{entry.entry_id}_gas_boiler")
    for dev_id in subentry_device_ids:
        if _get_dev is not None:
            dev = _get_dev({(DOMAIN, dev_id)}, entry.entry_id)
        else:
            dev = device_registry.async_get_device(identifiers={(DOMAIN, dev_id)})
        if dev and None in dev.config_entries_subentries.get(entry.entry_id, set()):
            device_registry.async_update_device(
                dev.id,
                remove_config_entry_id=entry.entry_id,
                remove_config_subentry_id=None,
            )


def _build_primary_zone_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    config: dict[str, Any],
    device: DeviceInfo,
    weather_coordinator: Any,
    heat_coordinator: Any,
    optimization_coordinator: Any,
) -> list[Any]:
    """Build the primary heating zone's full sensor catalog."""
    entities: list[Any] = []

    entities.append(
        CoordinatorOutdoorTemperatureSensor(
            coordinator=weather_coordinator,
            name="Outdoor Temperature",
            unique_id=f"{entry.entry_id}_outdoor_temperature",
            device=device,
        )
    )

    entities.append(
        CoordinatorHeatLossSensor(
            coordinator=heat_coordinator,
            name="Heat Loss",
            unique_id=f"{entry.entry_id}_heat_loss",
            icon="mdi:fire",
            device=device,
        )
    )

    entities.append(
        CoordinatorWindowSolarGainSensor(
            coordinator=heat_coordinator,
            name="Window Solar Gain",
            unique_id=f"{entry.entry_id}_window_solar_gain",
            icon="mdi:white-balance-sunny",
            device=device,
        )
    )

    entities.append(
        CoordinatorPVProductionForecastSensor(
            coordinator=heat_coordinator,
            name="PV Production Forecast",
            unique_id=f"{entry.entry_id}_pv_production_forecast",
            icon="mdi:solar-power",
            device=device,
        )
    )

    entities.append(
        CoordinatorNetHeatLossSensor(
            coordinator=heat_coordinator,
            name="Net Heat Loss",
            unique_id=f"{entry.entry_id}_net_heat_loss",
            icon="mdi:fire-off",
            device=device,
        )
    )

    entities.append(
        CoordinatorHeatingCurveOffsetSensor(
            coordinator=optimization_coordinator,
            name="Heating Curve Offset",
            unique_id=f"{entry.entry_id}_heating_curve_offset",
            icon="mdi:chart-line",
            device=device,
        )
    )

    entities.append(
        CoordinatorOptimizedSupplyTemperatureSensor(
            coordinator=optimization_coordinator,
            name="Optimized Supply Temperature",
            unique_id=f"{entry.entry_id}_optimized_supply_temperature",
            icon="mdi:thermometer-chevron-up",
            device=device,
        )
    )

    entities.append(
        CoordinatorHeatBufferSensor(
            coordinator=optimization_coordinator,
            name="Heat Buffer",
            unique_id=f"{entry.entry_id}_heat_buffer",
            icon="mdi:battery-medium",
            device=device,
        )
    )

    entities.append(
        CoordinatorCostSavingsSensor(
            coordinator=optimization_coordinator,
            name="Cost Savings Forecast",
            unique_id=f"{entry.entry_id}_cost_savings_forecast",
            icon="mdi:chart-line-variant",
            device=device,
        )
    )

    entities.append(
        TotalCostSavingsSensor(
            hass=hass,
            name="Total Cost Savings",
            unique_id=f"{entry.entry_id}_total_cost_savings",
            icon="mdi:piggy-bank",
            device=device,
            offset_sensor="sensor.heating_curve_optimizer_heating_curve_offset",
            outdoor_sensor="sensor.heating_curve_optimizer_outdoor_temperature",
            calculated_supply_sensor="sensor.heating_curve_optimizer_calculated_supply_temperature",
            consumption_price_sensor=config.get(CONF_CONSUMPTION_PRICE_SENSOR, ""),
            heat_demand_sensor="sensor.heating_curve_optimizer_net_heat_loss",
            k_factor=float(config.get(CONF_K_FACTOR, DEFAULT_K_FACTOR)),
            base_cop=float(config.get(CONF_BASE_COP, DEFAULT_COP_AT_35)),
            outdoor_temp_coefficient=float(
                config.get(
                    CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
                )
            ),
            cop_compensation_factor=float(
                config.get(
                    CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
                )
            ),
            time_base=int(config.get(CONF_TIME_BASE, DEFAULT_TIME_BASE)),
        )
    )

    supply_sensor = config.get(CONF_SUPPLY_TEMPERATURE_SENSOR)
    calculated_supply_sensor = None
    if supply_sensor:
        entities.append(
            CoordinatorQuadraticCopSensor(
                hass=hass,
                weather_coordinator=weather_coordinator,
                name="Quadratic COP",
                unique_id=f"{entry.entry_id}_quadratic_cop",
                supply_sensor=supply_sensor,
                device=device,
                k_factor=config.get(CONF_K_FACTOR, DEFAULT_K_FACTOR),
                base_cop=config.get(CONF_BASE_COP, DEFAULT_COP_AT_35),
                outdoor_temp_coefficient=config.get(
                    CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT
                ),
                cop_compensation_factor=config.get(
                    CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR
                ),
            )
        )

        calculated_supply_sensor = CoordinatorCalculatedSupplyTemperatureSensor(
            coordinator=weather_coordinator,
            name="Calculated Supply Temperature",
            unique_id=f"{entry.entry_id}_calculated_supply_temperature",
            device=device,
            min_temp=config.get(CONF_HEAT_CURVE_MIN, 20.0),
            max_temp=config.get(CONF_HEAT_CURVE_MAX, 45.0),
            min_outdoor=config.get(CONF_HEAT_CURVE_MIN_OUTDOOR, -20.0),
            max_outdoor=config.get(CONF_HEAT_CURVE_MAX_OUTDOOR, 15.0),
        )
        entities.append(calculated_supply_sensor)

    entities.append(
        CoordinatorDiagnosticsSensor(
            weather_coordinator=weather_coordinator,
            heat_coordinator=heat_coordinator,
            optimization_coordinator=optimization_coordinator,
            name="Diagnostics",
            unique_id=f"{entry.entry_id}_diagnostics",
            device=device,
        )
    )

    power_sensor = config.get(CONF_POWER_CONSUMPTION)
    if power_sensor and supply_sensor:
        registry = er.async_get(hass)
        heat_loss_entity = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_heat_loss"
        )
        cop_entity = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_quadratic_cop"
        )

        _LOGGER.debug(
            "Calibration sensor entity lookup: heat_loss=%s, cop=%s",
            heat_loss_entity,
            cop_entity,
        )

        entities.append(
            CalibrationSensor(
                hass=hass,
                name="Calibration",
                unique_id=f"{entry.entry_id}_calibration",
                device=device,
                entry=entry,
                heat_loss_sensor=heat_loss_entity,
                thermal_power_sensor=power_sensor,
                outdoor_sensor=(
                    weather_coordinator.data.get("outdoor_sensor_id")
                    if weather_coordinator.data
                    else None
                ),
                indoor_sensor=config.get("indoor_temperature_sensor"),
                supply_temp_sensor=supply_sensor,
                cop_sensor=cop_entity,
            )
        )

    _setup_event_driven_sensors(
        hass, entry, config, device, entities, weather_coordinator
    )

    _setup_daily_utility_sensors(hass, entry, config, device, entities)

    entities.append(
        ThermalCalibrationSensor(
            coordinator=optimization_coordinator,
            name="Thermal Calibration",
            unique_id=f"{entry.entry_id}_thermal_calibration",
            icon="mdi:tune",
            device=device,
        )
    )
    entities.append(
        RealtimeOffsetAdjustmentSensor(
            coordinator=optimization_coordinator,
            name="Realtime Offset Adjustment",
            unique_id=f"{entry.entry_id}_realtime_offset_adjustment",
            icon="mdi:solar-power-variant",
            device=device,
        )
    )

    return entities


def _setup_event_driven_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
    config: dict[str, Any],
    device: DeviceInfo,
    entities: list[Any],
    weather_coordinator: Any,
) -> None:
    """Set up event-driven sensors that track state changes in real-time."""

    consumption_price_sensor = config.get(CONF_CONSUMPTION_PRICE_SENSOR)
    if consumption_price_sensor:
        price_settings = config.get(CONF_PRICE_SETTINGS, {})
        entities.append(
            CurrentElectricityPriceSensor(
                hass=hass,
                name="Current Consumption Price",
                unique_id=f"{entry.entry_id}_current_electricity_price",
                price_sensor=consumption_price_sensor,
                source_type=SOURCE_TYPE_CONSUMPTION,
                price_settings=price_settings,
                icon="mdi:currency-eur",
                device=device,
            )
        )

    supply_sensor = config.get(CONF_SUPPLY_TEMPERATURE_SENSOR)
    power_sensor = config.get(CONF_POWER_CONSUMPTION)

    if supply_sensor and power_sensor:
        k_factor = float(config.get(CONF_K_FACTOR, DEFAULT_K_FACTOR))
        base_cop = float(config.get(CONF_BASE_COP, DEFAULT_COP_AT_35))
        outdoor_temp_coefficient = float(
            config.get(CONF_OUTDOOR_TEMP_COEFFICIENT, DEFAULT_OUTDOOR_TEMP_COEFFICIENT)
        )
        cop_compensation_factor = float(
            config.get(CONF_COP_COMPENSATION_FACTOR, DEFAULT_COP_COMPENSATION_FACTOR)
        )

        outdoor_sensor_ref: Any = None
        for entity in entities:
            if isinstance(entity, CoordinatorOutdoorTemperatureSensor):
                outdoor_sensor_ref = entity
                break
        if outdoor_sensor_ref is None:
            outdoor_sensor_ref = "sensor.heating_curve_optimizer_outdoor_temperature"

        thermal_power_sensor = HeatPumpThermalPowerSensor(
            hass=hass,
            name="Heat Pump Thermal Power",
            unique_id=f"{entry.entry_id}_thermal_power",
            power_sensor=power_sensor,
            supply_sensor=supply_sensor,
            outdoor_sensor=outdoor_sensor_ref,
            device=device,
            k_factor=k_factor,
            base_cop=base_cop,
            outdoor_temp_coefficient=outdoor_temp_coefficient,
            cop_compensation_factor=cop_compensation_factor,
        )
        entities.append(thermal_power_sensor)

        cop_sensor = None
        for entity in entities:
            if isinstance(entity, CoordinatorQuadraticCopSensor):
                cop_sensor = entity
                break

        offset_sensor: Any = None
        for entity in entities:
            if isinstance(entity, CoordinatorHeatingCurveOffsetSensor):
                offset_sensor = entity
                break
        if offset_sensor is None:
            offset_sensor = "sensor.heating_curve_optimizer_heating_curve_offset"

        calculated_supply_sensor: Any = None
        for entity in entities:
            if isinstance(entity, CoordinatorCalculatedSupplyTemperatureSensor):
                calculated_supply_sensor = entity
                break
        if calculated_supply_sensor is None:
            calculated_supply_sensor = (
                "sensor.heating_curve_optimizer_calculated_supply_temperature"
            )

        if cop_sensor and calculated_supply_sensor:
            entities.append(
                CopEfficiencyDeltaSensor(
                    hass=hass,
                    name="COP Delta",
                    unique_id=f"{entry.entry_id}_cop_delta",
                    cop_sensor=cop_sensor,
                    offset_entity=offset_sensor,
                    outdoor_sensor=outdoor_sensor_ref,
                    calculated_supply_sensor=calculated_supply_sensor,
                    device=device,
                    k_factor=k_factor,
                    base_cop=base_cop,
                    outdoor_temp_coefficient=outdoor_temp_coefficient,
                    cop_compensation_factor=cop_compensation_factor,
                )
            )

            entities.append(
                HeatGenerationDeltaSensor(
                    hass=hass,
                    name="Heat Generation Delta",
                    unique_id=f"{entry.entry_id}_heat_generation_delta",
                    thermal_power_sensor=thermal_power_sensor,
                    cop_sensor=cop_sensor,
                    offset_entity=offset_sensor,
                    outdoor_sensor=outdoor_sensor_ref,
                    calculated_supply_sensor=calculated_supply_sensor,
                    device=device,
                    k_factor=k_factor,
                    base_cop=base_cop,
                    outdoor_temp_coefficient=outdoor_temp_coefficient,
                    cop_compensation_factor=cop_compensation_factor,
                )
            )


def _setup_daily_utility_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
    config: dict[str, Any],
    device: DeviceInfo,
    entities: list[Any],
) -> None:
    """Set up daily utility sensors that track cumulative energy (kWh)."""

    thermal_power_sensor_id = None
    for entity in entities:
        if isinstance(entity, HeatPumpThermalPowerSensor):
            thermal_power_sensor_id = f"sensor.{DOMAIN}_heat_pump_thermal_power"
            break

    if thermal_power_sensor_id:
        entities.append(
            HeatPumpEnergyDailySensor(
                hass=hass,
                name="Heat Pump Energy Daily",
                unique_id=f"{entry.entry_id}_heat_pump_energy_daily",
                icon="mdi:fire",
                device=device,
                thermal_power_sensor=thermal_power_sensor_id,
            )
        )

    entities.append(
        NetHeatLossEnergyDailySensor(
            hass=hass,
            name="Net Heat Loss Energy Daily",
            unique_id=f"{entry.entry_id}_net_heat_loss_energy_daily",
            icon="mdi:fire-off",
            device=device,
            net_heat_loss_sensor=f"sensor.{DOMAIN}_net_heat_loss",
        )
    )
