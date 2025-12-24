"""Sensor platform for Heating Curve Optimizer."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import DOMAIN

# Import all sensor classes
from .weather.outdoor_temperature import CoordinatorOutdoorTemperatureSensor
from .heat.heat_loss import CoordinatorHeatLossSensor
from .heat.solar_gain import CoordinatorWindowSolarGainSensor
from .heat.pv_production import CoordinatorPVProductionForecastSensor
from .heat.net_heat_loss import CoordinatorNetHeatLossSensor
from .optimization.heating_curve_offset import CoordinatorHeatingCurveOffsetSensor
from .optimization.optimized_supply_temperature import (
    CoordinatorOptimizedSupplyTemperatureSensor,
)
from .optimization.heat_buffer import CoordinatorHeatBufferSensor
from .cop.quadratic_cop import CoordinatorQuadraticCopSensor
from .cop.calculated_supply_temperature import (
    CoordinatorCalculatedSupplyTemperatureSensor,
)
from .diagnostics_sensor import CoordinatorDiagnosticsSensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors from a config entry."""
    _LOGGER.debug("Setting up sensors for entry %s", entry.entry_id)

    # Get coordinators and config from hass.data
    entry_data = hass.data[DOMAIN][entry.entry_id]
    weather_coordinator = entry_data["weather_coordinator"]
    heat_coordinator = entry_data["heat_coordinator"]
    optimization_coordinator = entry_data["optimization_coordinator"]
    device = entry_data["device"]
    config = entry_data["config"]

    # Sensor list
    entities = []

    # Weather sensors
    entities.append(
        CoordinatorOutdoorTemperatureSensor(
            coordinator=weather_coordinator,
            name="Outdoor Temperature",
            unique_id=f"{entry.entry_id}_outdoor_temperature",
            device=device,
        )
    )

    # Heat sensors
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

    # Optimization sensors
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

    # COP sensors (if supply sensor is configured)
    supply_sensor = config.get("supply_temperature_sensor")
    if supply_sensor:
        entities.append(
            CoordinatorQuadraticCopSensor(
                hass=hass,
                weather_coordinator=weather_coordinator,
                name="Quadratic COP",
                unique_id=f"{entry.entry_id}_quadratic_cop",
                supply_sensor=supply_sensor,
                device=device,
                k_factor=config.get("k_factor", 0.025),
                base_cop=config.get("base_cop", 4.0),
                outdoor_temp_coefficient=config.get("outdoor_temp_coefficient", 0.025),
                cop_compensation_factor=config.get("cop_compensation_factor", 0.9),
            )
        )

        entities.append(
            CoordinatorCalculatedSupplyTemperatureSensor(
                coordinator=weather_coordinator,
                name="Calculated Supply Temperature",
                unique_id=f"{entry.entry_id}_calculated_supply_temperature",
                device=device,
                min_temp=config.get("min_supply_temp", 20.0),
                max_temp=config.get("max_supply_temp", 45.0),
                min_outdoor=config.get("min_outdoor_temp", -20.0),
                max_outdoor=config.get("max_outdoor_temp", 15.0),
            )
        )

    # Diagnostics sensor
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

    _LOGGER.debug("Adding %d sensor entities", len(entities))
    async_add_entities(entities)
