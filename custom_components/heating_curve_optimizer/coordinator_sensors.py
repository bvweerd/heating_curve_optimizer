"""Coordinator-based sensor implementations for Heating Curve Optimizer.

These sensors use DataUpdateCoordinators for efficient data fetching and calculations.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import BaseUtilitySensor
from .const import (
    DEFAULT_K_FACTOR,
    DEFAULT_COP_AT_35,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_COP_COMPENSATION_FACTOR,
)


class CoordinatorOutdoorTemperatureSensor(CoordinatorEntity, BaseUtilitySensor):
    """Outdoor temperature sensor using weather coordinator."""

    def __init__(self, coordinator, name: str, unique_id: str, device: DeviceInfo):
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
            translation_key=name.lower().replace(" ", "_").replace(".", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return current temperature."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("current_temperature")

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


class CoordinatorHeatLossSensor(CoordinatorEntity, BaseUtilitySensor):
    """Heat loss sensor using heat calculation coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
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
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return heat loss."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("heat_loss")

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

        from .const import (
            CONF_AREA_M2,
            CONF_ENERGY_LABEL,
            CONF_VENTILATION_TYPE,
            CONF_CEILING_HEIGHT,
            DEFAULT_VENTILATION_TYPE,
            DEFAULT_CEILING_HEIGHT,
            VENTILATION_TYPES,
            calculate_htc_from_energy_label,
            calculate_ventilation_htc,
        )

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


class CoordinatorWindowSolarGainSensor(CoordinatorEntity, BaseUtilitySensor):
    """Solar gain sensor using heat calculation coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
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
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return solar gain."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("solar_gain")

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


class CoordinatorPVProductionForecastSensor(CoordinatorEntity, BaseUtilitySensor):
    """PV production forecast sensor using heat calculation coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
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
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return current PV production forecast."""
        if not self.coordinator.data:
            return None
        forecast = self.coordinator.data.get("pv_production_forecast", [])
        return forecast[0] if forecast else None

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


class CoordinatorNetHeatLossSensor(CoordinatorEntity, BaseUtilitySensor):
    """Net heat loss sensor using heat calculation coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
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
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return net heat loss."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("net_heat_loss")

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


class CoordinatorHeatingCurveOffsetSensor(CoordinatorEntity, BaseUtilitySensor):
    """Heating curve offset sensor using optimization coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
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
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return optimized offset."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("optimized_offset")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return optimization results."""
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data
        return {
            "optimized_offsets": data.get("optimized_offsets", []),
            "buffer_evolution": data.get("buffer_evolution", []),
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


class CoordinatorOptimizedSupplyTemperatureSensor(CoordinatorEntity, BaseUtilitySensor):
    """Optimized supply temperature sensor using optimization coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="°C",
            device_class="temperature",
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return optimized supply temperature."""
        if not self.coordinator.data:
            return None

        # Get future supply temperatures from coordinator
        future_temps = self.coordinator.data.get("future_supply_temperatures", [])
        if not future_temps:
            return None

        # Return first future supply temperature (current optimized temperature)
        return future_temps[0]

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
            "optimized_offsets": self.coordinator.data.get("optimized_offsets", []),
            "future_supply_temperatures": self.coordinator.data.get(
                "future_supply_temperatures", []
            ),
            "forecast_time_base": 60,
        }


class CoordinatorHeatBufferSensor(CoordinatorEntity, BaseUtilitySensor):
    """Heat buffer sensor using optimization coordinator."""

    def __init__(
        self, coordinator, name: str, unique_id: str, icon: str, device: DeviceInfo
    ):
        """Initialize the sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit="kWh",
            device_class="energy",
            icon=icon,
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        # For energy device_class, state_class must be 'total' not 'measurement'
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return current buffer level."""
        if not self.coordinator.data:
            return None
        buffer_evolution = self.coordinator.data.get("buffer_evolution", [])
        return buffer_evolution[0] if buffer_evolution else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return buffer evolution forecast."""
        if not self.coordinator.data:
            return {}
        return {
            "forecast": self.coordinator.data.get("buffer_evolution", []),
            "forecast_time_base": 60,
        }


class CoordinatorQuadraticCopSensor(BaseUtilitySensor):
    """COP sensor that reads from supply and outdoor temperature sensors."""

    def __init__(
        self,
        hass: HomeAssistant,
        weather_coordinator,
        name: str,
        unique_id: str,
        supply_sensor: str,
        device: DeviceInfo,
        k_factor: float = DEFAULT_K_FACTOR,
        base_cop: float = DEFAULT_COP_AT_35,
        outdoor_temp_coefficient: float = DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
        cop_compensation_factor: float = DEFAULT_COP_COMPENSATION_FACTOR,
    ):
        """Initialize the COP sensor."""
        super().__init__(
            name=name,
            unique_id=unique_id,
            unit="",
            device_class=None,
            icon="mdi:alpha-c-circle",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self.hass = hass
        self.weather_coordinator = weather_coordinator
        self.supply_sensor = supply_sensor
        self.k_factor = k_factor
        self.base_cop = base_cop
        self.outdoor_temp_coefficient = outdoor_temp_coefficient
        self.cop_compensation_factor = cop_compensation_factor
        self._attr_state_class = SensorStateClass.MEASUREMENT

    async def async_update(self):
        """Update COP based on supply and outdoor temperature."""
        # Get supply temperature
        s_state = self.hass.states.get(self.supply_sensor)
        if not s_state or s_state.state in ("unknown", "unavailable"):
            self._set_unavailable(f"Supply sensor {self.supply_sensor} unavailable")
            return

        try:
            supply_temp = float(s_state.state)
        except (ValueError, TypeError):
            self._set_unavailable("Invalid supply temperature")
            return

        # Get outdoor temperature from coordinator
        weather_data = self.weather_coordinator.data
        if not weather_data:
            self._set_unavailable("No weather data available")
            return

        outdoor_temp = weather_data.get("current_temperature")
        if outdoor_temp is None:
            self._set_unavailable("No outdoor temperature")
            return

        # Calculate COP using quadratic formula
        # COP = (base_cop + outdoor_coefficient * T_out - k_factor * (T_supply - 35)) * compensation
        cop = (
            self.base_cop
            + self.outdoor_temp_coefficient * outdoor_temp
            - self.k_factor * (supply_temp - 35)
        ) * self.cop_compensation_factor

        self._attr_native_value = round(max(1.0, cop), 2)
        self._mark_available()


class CoordinatorCalculatedSupplyTemperatureSensor(
    CoordinatorEntity, BaseUtilitySensor
):
    """Calculated supply temperature based on heating curve and outdoor temp."""

    def __init__(
        self,
        coordinator,
        name: str,
        unique_id: str,
        device: DeviceInfo,
        min_temp: float = 20.0,
        max_temp: float = 45.0,
        min_outdoor: float = -20.0,
        max_outdoor: float = 15.0,
    ):
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
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = False
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.min_outdoor = min_outdoor
        self.max_outdoor = max_outdoor

    @property
    def native_value(self):
        """Calculate supply temperature from heating curve."""
        if not self.coordinator.data:
            return None

        # Get outdoor temperature
        outdoor_temp = self.coordinator.data.get("current_temperature")
        if outdoor_temp is None:
            return None

        # Calculate supply temperature using heating curve
        # Linear interpolation between min/max temps based on outdoor temp
        if outdoor_temp <= self.min_outdoor:
            supply_temp = self.max_temp
        elif outdoor_temp >= self.max_outdoor:
            supply_temp = self.min_temp
        else:
            # Linear interpolation
            temp_range = self.max_temp - self.min_temp
            outdoor_range = self.max_outdoor - self.min_outdoor
            supply_temp = self.max_temp - (
                (outdoor_temp - self.min_outdoor) / outdoor_range * temp_range
            )

        return round(supply_temp, 1)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )


class CoordinatorDiagnosticsSensor(CoordinatorEntity, BaseUtilitySensor):
    """Diagnostics sensor with all coordinator data."""

    def __init__(
        self,
        weather_coordinator,
        heat_coordinator,
        optimization_coordinator,
        name: str,
        unique_id: str,
        device: DeviceInfo,
    ):
        """Initialize the diagnostics sensor."""
        # Use weather coordinator as primary
        CoordinatorEntity.__init__(self, weather_coordinator)
        BaseUtilitySensor.__init__(
            self,
            name=name,
            unique_id=unique_id,
            unit=None,  # No unit for string sensor
            device_class=None,
            icon="mdi:information-outline",
            visible=True,
            device=device,
            translation_key=name.lower().replace(" ", "_"),
        )
        self._attr_should_poll = False
        # Diagnostics sensor returns string, so no state_class or unit
        self._attr_state_class = None
        self._attr_native_unit_of_measurement = None
        self.weather_coordinator = weather_coordinator
        self.heat_coordinator = heat_coordinator
        self.optimization_coordinator = optimization_coordinator

    @property
    def native_value(self):
        """Return OK if all coordinators are working."""
        if (
            self.weather_coordinator.last_update_success
            and self.heat_coordinator.last_update_success
            and self.optimization_coordinator.last_update_success
        ):
            return "OK"
        return "ERROR"

    @property
    def available(self) -> bool:
        """Always available to show diagnostics."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all coordinator data as diagnostics."""
        attrs = {}

        # Weather coordinator data
        if self.weather_coordinator.data:
            attrs["weather_last_update"] = str(
                self.weather_coordinator.data.get("timestamp")
            )
            attrs["outdoor_temperature"] = self.weather_coordinator.data.get(
                "current_temperature"
            )
            attrs["weather_success"] = self.weather_coordinator.last_update_success

        # Heat coordinator data
        if self.heat_coordinator.data:
            attrs["heat_last_update"] = str(self.heat_coordinator.data.get("timestamp"))
            attrs["heat_loss"] = self.heat_coordinator.data.get("heat_loss")
            attrs["solar_gain"] = self.heat_coordinator.data.get("solar_gain")
            attrs["net_heat_loss"] = self.heat_coordinator.data.get("net_heat_loss")
            attrs["heat_success"] = self.heat_coordinator.last_update_success

        # Optimization coordinator data
        if self.optimization_coordinator.data:
            attrs["optimization_last_update"] = str(
                self.optimization_coordinator.data.get("timestamp")
            )
            attrs["optimized_offset"] = self.optimization_coordinator.data.get(
                "optimized_offset"
            )
            attrs["total_cost"] = self.optimization_coordinator.data.get("total_cost")
            attrs["optimization_success"] = (
                self.optimization_coordinator.last_update_success
            )

        return attrs
