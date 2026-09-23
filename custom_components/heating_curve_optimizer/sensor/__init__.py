"""Sensor platform for Heating Curve Optimizer."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import (
    DOMAIN,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_PRICE_SETTINGS,
    CONF_POWER_CONSUMPTION,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_TIME_BASE,
    SOURCE_TYPE_CONSUMPTION,
    DEFAULT_K_FACTOR,
    DEFAULT_COP_AT_35,
    DEFAULT_OUTDOOR_TEMP_COEFFICIENT,
    DEFAULT_COP_COMPENSATION_FACTOR,
    DEFAULT_TIME_BASE,
    CONF_K_FACTOR,
    CONF_BASE_COP,
    CONF_OUTDOOR_TEMP_COEFFICIENT,
    CONF_COP_COMPENSATION_FACTOR,
    CONF_HEAT_CURVE_MIN,
    CONF_HEAT_CURVE_MAX,
    CONF_HEAT_CURVE_MIN_OUTDOOR,
    CONF_HEAT_CURVE_MAX_OUTDOOR,
)

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
from .optimization.cost_savings import CoordinatorCostSavingsSensor
from .optimization.total_cost_savings import TotalCostSavingsSensor
from .cop.quadratic_cop import CoordinatorQuadraticCopSensor
from .cop.calculated_supply_temperature import (
    CoordinatorCalculatedSupplyTemperatureSensor,
)
from .diagnostics_sensor import CoordinatorDiagnosticsSensor
from .event_driven import (
    CurrentElectricityPriceSensor,
    HeatPumpThermalPowerSensor,
    CopEfficiencyDeltaSensor,
    HeatGenerationDeltaSensor,
)
from .daily_utility import (
    HeatPumpEnergyDailySensor,
    NetHeatLossEnergyDailySensor,
)
from ..calibration_sensor import CalibrationSensor
from ..sensor_thermal_calibration import ThermalCalibrationSensor
from ..sensor_realtime_offset import RealtimeOffsetAdjustmentSensor
from .gas_boiler.heat_pump_cost import GasBoilerHeatPumpCostSensor
from .gas_boiler.gas_cost import GasBoilerGasCostSensor
from .gas_boiler.cost_savings import GasBoilerCostSavingsSensor

_LOGGER = logging.getLogger(__name__)

# Entities are updated via their coordinator, never by per-entity I/O,
# so there is no reason to serialize updates against each other.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors from a config entry."""
    _LOGGER.debug("Setting up sensors for entry %s", entry.entry_id)

    # Get coordinators and config from the entry's runtime data
    runtime_data = entry.runtime_data
    weather_coordinator = runtime_data.weather_coordinator
    heat_coordinator = runtime_data.heat_coordinator
    optimization_coordinator = runtime_data.optimization_coordinator
    device = runtime_data.device
    config = runtime_data.config

    # Primary heating zone's full sensor catalog (see __init__.py's
    # _find_primary_zone_subentry) - heat_coordinator/optimization_coordinator
    # are None when no zone subentry is configured yet, a valid, if
    # useless, state: nothing to build sensors from.
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

    # Per-zone core sensors (phase 5c, REDESIGN.md): each zone's own
    # optimizer output, associated with its own subentry/device via
    # config_subentry_id. Scoped to the core "what did this zone's own
    # optimizer decide" sensors for a first cut - not the full catalog of
    # diagnostic/shadow/calibration sensors the primary zone gets (see
    # docs/redesign/REDESIGN.md phase 5c).
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

    # Hybrid gas-boiler comparison (optional, singleton subentry - see
    # __init__.py's gas boiler setup). Absent for every installation that
    # hasn't configured it. Enabled by default (unlike the phase-2/4/5
    # diagnostic sensors, which stay disabled-by-default extras added to
    # every install) - the subentry itself is the opt-in signal, so hiding
    # these again would just be redundant friction.
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


def _build_primary_zone_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    config: dict[str, Any],
    device: DeviceInfo,
    weather_coordinator: Any,
    heat_coordinator: Any,
    optimization_coordinator: Any,
) -> list[Any]:
    """Build the primary heating zone's full sensor catalog (unchanged
    from before every zone became a subentry - see async_setup_entry)."""
    # Sensor list
    entities: list[Any] = []

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

    entities.append(
        CoordinatorCostSavingsSensor(
            coordinator=optimization_coordinator,
            name="Cost Savings Forecast",
            unique_id=f"{entry.entry_id}_cost_savings_forecast",
            icon="mdi:chart-line-variant",
            device=device,
        )
    )

    # Total cumulative cost savings sensor
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

    # COP sensors (if supply sensor is configured)
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

    # Calibration sensor (parameter validation and recommendations)
    # Requires power_sensor, supply_sensor for validation
    power_sensor = config.get(CONF_POWER_CONSUMPTION)
    if power_sensor and supply_sensor:
        # Find entity IDs using entity registry (based on unique_ids we create)
        from homeassistant.helpers import entity_registry as er

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

    # Event-driven sensors (real-time state tracking)
    _setup_event_driven_sensors(
        hass, entry, config, device, entities, weather_coordinator
    )

    # Daily utility sensors (cumulative energy tracking)
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

    # Price sensor
    consumption_price_sensor = config.get(CONF_CONSUMPTION_PRICE_SENSOR)
    if consumption_price_sensor:
        price_settings = config.get(CONF_PRICE_SETTINGS, {})
        entities.append(
            CurrentElectricityPriceSensor(
                hass=hass,
                # Always mirrors consumption_price_sensor specifically (see
                # source_type=SOURCE_TYPE_CONSUMPTION below) - named to say
                # so explicitly, since this integration has no separate
                # sensor for the production/feed-in price and a generic
                # "Current Electricity Price" name would leave a user with
                # asymmetric buy/sell tariffs guessing which one this is.
                name="Current Consumption Price",
                unique_id=f"{entry.entry_id}_current_electricity_price",
                price_sensor=consumption_price_sensor,
                source_type=SOURCE_TYPE_CONSUMPTION,
                price_settings=price_settings,
                icon="mdi:currency-eur",
                device=device,
            )
        )

    # COP and thermal power sensors
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

        # Find outdoor temperature sensor reference
        # `: Any` - holds either a live entity reference or an entity_id
        # string fallback (a common HA idiom for cross-referencing an
        # entity that may or may not exist yet).
        outdoor_sensor_ref: Any = None
        for entity in entities:
            if isinstance(entity, CoordinatorOutdoorTemperatureSensor):
                outdoor_sensor_ref = entity
                break
        if outdoor_sensor_ref is None:
            outdoor_sensor_ref = "sensor.heating_curve_optimizer_outdoor_temperature"

        # Thermal power sensor
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

        # Find COP sensor (added conditionally earlier)
        cop_sensor = None
        for entity in entities:
            if isinstance(entity, CoordinatorQuadraticCopSensor):
                cop_sensor = entity
                break

        # Find heating curve offset sensor
        offset_sensor: Any = None
        for entity in entities:
            if isinstance(entity, CoordinatorHeatingCurveOffsetSensor):
                offset_sensor = entity
                break
        if offset_sensor is None:
            offset_sensor = "sensor.heating_curve_optimizer_heating_curve_offset"

        # Find calculated supply temperature sensor
        calculated_supply_sensor: Any = None
        for entity in entities:
            if isinstance(entity, CoordinatorCalculatedSupplyTemperatureSensor):
                calculated_supply_sensor = entity
                break
        if calculated_supply_sensor is None:
            calculated_supply_sensor = (
                "sensor.heating_curve_optimizer_calculated_supply_temperature"
            )

        # COP delta sensor
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

            # Heat generation delta sensor
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

    # Find thermal power sensor
    thermal_power_sensor_id = None
    for entity in entities:
        if isinstance(entity, HeatPumpThermalPowerSensor):
            thermal_power_sensor_id = f"sensor.{DOMAIN}_heat_pump_thermal_power"
            break

    # Add daily heat pump energy sensor
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

    # Add daily net heat loss energy sensor
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
