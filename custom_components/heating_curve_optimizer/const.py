"""Constants for the Heating Curve Optimizer integration."""

from homeassistant.const import Platform

# Domain of the integration
DOMAIN = "heating_curve_optimizer"

# Supported platforms for this integration
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.CLIMATE,
]

# Main entry: prices
CONF_CONSUMPTION_PRICE_SENSOR = "consumption_price_sensor"
CONF_PRODUCTION_PRICE_SENSOR = "production_price_sensor"

# Zone subentry: building
CONF_AREA_M2 = "area_m2"
CONF_ENERGY_LABEL = "energy_label"
CONF_INTERNAL_GAINS_W_PER_M2 = "internal_gains_w_per_m2"
# Average internal heat gains of an occupied dwelling (people, appliances,
# lighting), per m² floor area. NTA 8800 uses ~2-4 W/m² for residential use.
DEFAULT_INTERNAL_GAINS_W_PER_M2 = 3.0

# Main entry: optimizer horizon. The step length follows the price sensor's
# own interval (15 or 60 min), so the DP steps coincide with price periods.
CONF_PLANNING_WINDOW = "planning_window"
DEFAULT_PLANNING_WINDOW = 24  # hours

# Glass related configuration
CONF_GLASS_EAST_M2 = "glass_east_m2"
CONF_GLASS_WEST_M2 = "glass_west_m2"
CONF_GLASS_SOUTH_M2 = "glass_south_m2"
CONF_GLASS_U_VALUE = "glass_u_value"
DEFAULT_GLASS_U_VALUE = 1.2
CONF_POWER_CONSUMPTION = "power_consumption"
CONF_INDOOR_TEMPERATURE_SENSOR = "indoor_temperature_sensor"
CONF_SUPPLY_TEMPERATURE_SENSOR = "supply_temperature_sensor"
# Heating zones (config subentries). Each zone is one heating circuit with
# its own building envelope, emitters, thermostat and - optionally - its
# own heating curve; price sensors and heat pump parameters are shared from
# the main entry. The first zone is the primary one.
ZONE_SUBENTRY_TYPE = "heating_zone"

# Hybrid gas-boiler cost comparison, as a singleton config subentry (unlike
# ZONE_SUBENTRY_TYPE, capped at one instance in HeatingGasBoilerSubentryFlow
# - a hybrid system has exactly one physical gas boiler). Entirely optional
# and zero-impact when absent: no gas_boiler_coordinator is created, no
# entities are added, nothing changes for an installation that hasn't
# configured it. See gas_boiler_model.py / gas_boiler_coordinator.py.
GAS_SUBENTRY_TYPE = "gas_boiler"
CONF_GAS_PRICE_SENSOR = "gas_price_sensor"
CONF_GAS_BOILER_EFFICIENCY = "gas_boiler_efficiency"
CONF_GAS_CALORIFIC_VALUE = "gas_calorific_value_kwh_per_m3"
# Optional cumulative gas meter (m³ or kWh) of the boiler: gives absolute
# heat for calibrating the heat pump's COP level.
CONF_GAS_METER_SENSOR = "gas_meter_sensor"
# Use the gas boiler as comfort backup when the heat pump cannot restore the
# comfort band, regardless of price. Off: gas only when it is also cheaper.
CONF_GAS_COMFORT_BACKUP = "gas_comfort_backup"
DEFAULT_GAS_COMFORT_BACKUP = True

# 90%: typical Dutch HR-combi boiler at non-optimal (higher) return
# temperatures. A well-tuned condensing unit at a low return temperature can
# reach ~1.07 (107%, HHV/bovenwaarde-based) - user-adjustable per appliance,
# this is a conservative rather than optimistic default.
DEFAULT_GAS_BOILER_EFFICIENCY = 0.90
# Dutch G-gas bovenwaarde (higher heating value/HHV), ~35.17 MJ/m3 - the
# standard value Dutch grid operators publish, not the lower heating value
# (~8.8 kWh/m3) some appliance datasheets quote instead. User-adjustable
# since it varies slightly by gas quality/grid.
DEFAULT_GAS_CALORIFIC_VALUE_KWH_PER_M3 = 9.77

# Real-time grid power (optional): enables the PV-surplus controller.
CONF_GRID_IMPORT_SENSOR = "grid_import_sensor"
# Optional calibration inputs.
CONF_HEAT_PUMP_THERMAL_POWER_SENSOR = "heat_pump_thermal_power_sensor"
CONF_DHW_ACTIVE_SENSOR = "dhw_active_sensor"
CONF_WINDOW_SENSORS = "window_sensors"  # zone
CONF_CALIBRATION_MODE = "calibration_mode"  # zone
DEFAULT_CALIBRATION_MODE = "observe"
CONF_GRID_EXPORT_SENSOR = "grid_export_sensor"
CONF_HEAT_PUMP_MAX_THERMAL_POWER = "heat_pump_max_thermal_power_kw"
CONF_K_FACTOR = "k_factor"
CONF_BASE_COP = "base_cop"
CONF_COP_COMPENSATION_FACTOR = "cop_compensation_factor"
CONF_OUTDOOR_TEMP_COEFFICIENT = "outdoor_temp_coefficient"
CONF_HEAT_CURVE_MIN_OUTDOOR = "heat_curve_min_outdoor"
CONF_HEAT_CURVE_MAX_OUTDOOR = "heat_curve_max_outdoor"
CONF_HEAT_CURVE_MIN = "heat_curve_min"
CONF_HEAT_CURVE_MAX = "heat_curve_max"
CONF_VENTILATION_TYPE = "ventilation_type"
CONF_CEILING_HEIGHT = "ceiling_height"
CONF_TARGET_INDOOR_TEMP = "target_indoor_temp"
CONF_INDOOR_TEMP_HYSTERESIS_LOWER = "indoor_temp_hysteresis_lower"  # Below target
CONF_INDOOR_TEMP_HYSTERESIS_UPPER = "indoor_temp_hysteresis_upper"  # Above target
CONF_OFFSET_DELTA_T = "offset_delta_t"  # Minutes per 1°C offset change

# Primary-zone setpoints written live to entry.options by the number and
# climate entities. Changing them re-runs the optimizer instead of reloading.
ENTITY_MANAGED_OPTIONS = frozenset(
    {
        CONF_TARGET_INDOOR_TEMP,
        CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
        CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    }
)

# Default values for heating curve settings
DEFAULT_HEAT_CURVE_MIN = 25.0  # supply °C at the warm end of the curve
DEFAULT_HEAT_CURVE_MAX = 45.0  # supply °C at the cold end of the curve
DEFAULT_HEAT_CURVE_MIN_OUTDOOR = -10.0  # design (coldest) outdoor temperature
DEFAULT_HEAT_CURVE_MAX_OUTDOOR = 15.0  # outdoor temperature where heating stops
DEFAULT_TARGET_INDOOR_TEMP = 20.0  # °C - desired room temperature
DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER = (
    0.3  # °C - hysteresis below target (heat pump ON)
)
DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER = (
    0.5  # °C - hysteresis above target (heat pump OFF)
)
# Minutes per 1°C offset change: 30 allows 2°C per hour, which most heat
# pumps follow without overshoot. 10 is fast, 60 is slow.
DEFAULT_OFFSET_DELTA_T = 30

# Default ventilation and building settings
DEFAULT_VENTILATION_TYPE = "natural_standard"
DEFAULT_CEILING_HEIGHT = 2.5  # meters

# Ventilation types and their effective Air Changes per Hour (ACH)
# ACH represents the volume of air exchanged per hour
# For heat recovery systems, effective ACH accounts for recovered heat
VENTILATION_TYPES: dict[str, dict[str, float | str]] = {
    "none": {"ach": 0.2, "name_nl": "Geen/minimaal", "name_en": "None/minimal"},
    "natural_low": {
        "ach": 0.5,
        "name_nl": "Natuurlijke ventilatie (basis)",
        "name_en": "Natural ventilation (basic)",
    },
    "natural_standard": {
        "ach": 1.0,
        "name_nl": "Natuurlijke ventilatie (standaard)",
        "name_en": "Natural ventilation (standard)",
    },
    "natural_leaky": {
        "ach": 1.5,
        "name_nl": "Natuurlijke ventilatie (lekkage)",
        "name_en": "Natural ventilation (leaky)",
    },
    "mechanical_exhaust": {
        "ach": 0.7,
        "name_nl": "Mechanische afzuiging",
        "name_en": "Mechanical exhaust",
    },
    "balanced_no_recovery": {
        "ach": 0.8,
        "name_nl": "Gebalanceerd (zonder WTW)",
        "name_en": "Balanced (no heat recovery)",
    },
    "heat_recovery_50": {
        "ach": 0.4,
        "name_nl": "WTW 50% rendement",
        "name_en": "Heat recovery 50% efficiency",
    },
    "heat_recovery_70": {
        "ach": 0.24,
        "name_nl": "WTW 70% rendement",
        "name_en": "Heat recovery 70% efficiency",
    },
    "heat_recovery_90": {
        "ach": 0.08,
        "name_nl": "WTW 90% rendement",
        "name_en": "Heat recovery 90% efficiency",
    },
}

# PV arrays (config subentries), one per orientation.
PV_SUBENTRY_TYPE = "pv_array"
CONF_PV_PEAK_POWER_KWP = "peak_power_kwp"
CONF_PV_ORIENTATION = "orientation"  # degrees, 0-360, 180 = south
CONF_PV_TILT = "tilt"  # degrees, 0-90
CONF_PV_EFFICIENCY_FACTOR = "efficiency_factor"
CONF_PV_DC_COUPLED = "dc_coupled"

DEFAULT_PV_ORIENTATION_DEG = 180.0  # south-facing
DEFAULT_PV_TILT = 35.0  # degrees - typical for Netherlands
DEFAULT_PV_EFFICIENCY_FACTOR = 0.85

# Allowed energy labels
ENERGY_LABELS = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]

# Mapping energy label -> primary energy consumption (kWh/m²/year)
# Based on NTA 8800 standard (since Jan 2021)
# Using midpoint values for each label range
ENERGY_LABEL_CONSUMPTION = {
    "A+++": 50,  # Very low energy (passive house level)
    "A++": 75,  # Very low energy
    "A+": 90,  # Very low energy
    "A": 132,  # 105-160 kWh/m²/year (midpoint)
    "B": 175,  # 160-190 kWh/m²/year (midpoint)
    "C": 220,  # 190-250 kWh/m²/year (midpoint)
    "D": 275,  # 250-300 kWh/m²/year (midpoint)
    "E": 340,  # 300-380 kWh/m²/year (midpoint)
    "F": 420,  # 380-460 kWh/m²/year (midpoint)
    "G": 500,  # >460 kWh/m²/year (estimate)
}

# Heating fraction of total primary energy by label
# Better insulated homes have relatively more DHW energy use
HEATING_FRACTION_MAP = {
    "A+++": 0.40,  # Passive house: excellent insulation, DHW is major portion
    "A++": 0.42,
    "A+": 0.45,
    "A": 0.50,  # Good insulation
    "B": 0.55,
    "C": 0.60,
    "D": 0.65,
    "E": 0.68,
    "F": 0.70,
    "G": 0.72,  # Poor insulation: heating dominates
}

# Heating degree-days for Netherlands (base 18°C)
HEATING_DEGREE_DAYS_NL = 2900


def calculate_ventilation_htc(
    area_m2: float,
    ventilation_type: str = DEFAULT_VENTILATION_TYPE,
    ceiling_height: float = DEFAULT_CEILING_HEIGHT,
) -> float:
    """
    Calculate ventilation Heat Transfer Coefficient (H_V).

    This calculates the heat loss through ventilation and air infiltration
    based on the ventilation system type and building volume.

    Formula:
    - Volume (m³) = Area × Ceiling height
    - H_V (W/K) = ρ × c × Volume × ACH / 3.6
      where ρ = 1.2 kg/m³ (air density)
            c = 1.005 kJ/(kg·K) (specific heat of air)
            ACH = Air Changes per Hour from ventilation type

    Args:
        area_m2: Floor area in m²
        ventilation_type: Ventilation system type key from VENTILATION_TYPES
        ceiling_height: Ceiling height in meters (default: 2.5m)

    Returns:
        Ventilation Heat Transfer Coefficient in W/K
    """
    # Get ACH for this ventilation type
    vent_data = VENTILATION_TYPES.get(ventilation_type)
    if vent_data is None:
        # Fallback to standard natural ventilation
        vent_data = VENTILATION_TYPES[DEFAULT_VENTILATION_TYPE]
    ach = float(vent_data["ach"])

    # Calculate building volume
    volume = area_m2 * ceiling_height

    # Calculate ventilation HTC
    # ρ (air density) = 1.2 kg/m³
    # c (specific heat) = 1.005 kJ/(kg·K)
    # Division by 3.6 converts kJ to W and hours to seconds
    rho = 1.2  # kg/m³
    c = 1.005  # kJ/(kg·K)
    h_v = rho * c * volume * ach / 3.6

    return h_v


def calculate_htc_from_energy_label(
    energy_label: str,
    area_m2: float,
    heating_degree_days: float = HEATING_DEGREE_DAYS_NL,
    ventilation_type: str = DEFAULT_VENTILATION_TYPE,
    ceiling_height: float = DEFAULT_CEILING_HEIGHT,
) -> float:
    """
    Calculate total Heat Transfer Coefficient (HTC) from energy label.

    This converts the energy label (primary energy in kWh/m²/year) to the
    total building heat loss coefficient (W/K) using heating degree-days.

    The total HTC includes both transmission losses (through building fabric)
    and ventilation losses (air exchange), following ISO 13789:
        HTC_total = H_T + H_V

    Formula:
    - Annual heating energy (kWh) = Label energy × Area × Heating fraction
    - H_T (W/K) = Annual heating energy × 1000 / (HDD × 24)
    - H_V (W/K) = Ventilation heat loss coefficient (see calculate_ventilation_htc)
    - HTC (W/K) = H_T + H_V

    Args:
        energy_label: Energy label (A+++, A++, A+, A, B, C, D, E, F, G)
        area_m2: Floor area in m²
        heating_degree_days: Heating degree-days for the climate (default: NL)
        ventilation_type: Ventilation system type (default: natural_standard)
        ceiling_height: Ceiling height in meters (default: 2.5m)

    Returns:
        Total Heat Transfer Coefficient in W/K (transmission + ventilation)
    """
    # Get energy consumption and heating fraction for this label
    energy_per_m2 = ENERGY_LABEL_CONSUMPTION.get(energy_label.upper(), 220)
    heating_fraction = HEATING_FRACTION_MAP.get(energy_label.upper(), 0.60)

    # Calculate annual heating energy (kWh/year)
    annual_heating_energy = energy_per_m2 * area_m2 * heating_fraction

    # Convert to transmission HTC using degree-days
    # H_T (W/K) = kWh/year × 1000 W/kW / (degree-days × 24 hours/day)
    if heating_degree_days <= 0:
        heating_degree_days = HEATING_DEGREE_DAYS_NL

    h_t = annual_heating_energy * 1000.0 / (heating_degree_days * 24.0)

    # Calculate ventilation HTC
    h_v = calculate_ventilation_htc(area_m2, ventilation_type, ceiling_height)

    # Total HTC = transmission + ventilation (ISO 13789)
    htc_total = h_t + h_v

    return htc_total


# Default COP at a supply temperature of 35 °C
DEFAULT_COP_AT_35 = 4.2

# Default decline in COP per °C supply temperature increase
DEFAULT_K_FACTOR = 0.11

# Default increase in COP per °C outdoor temperature rise
DEFAULT_OUTDOOR_TEMP_COEFFICIENT = 0.08

# Default COP compensation factor
DEFAULT_COP_COMPENSATION_FACTOR = 1.0

# Thermal mass per m² floor area, in Wh/(m2*K), by construction weight class.
# Starting values until calibration.py learns the real value for a home.
CONF_THERMAL_MASS_CLASS = "thermal_mass_class"
DEFAULT_THERMAL_MASS_CLASS = "medium"
THERMAL_MASS_WH_PER_M2_K = {
    "light": 40.0,  # timber frame, light interior finishes
    "medium": 90.0,  # standard Dutch cavity wall + concrete floor
    "heavy": 165.0,  # masonry/concrete throughout, exposed screed or floor
}

# Heat emitter type: how much thermal power an emitter can push into the
# room for a given (supply_temp - indoor_temp), relative to its power at
# the design point. exponent follows the standard EN 442 radiator exponent
# (~1.3) vs. the flatter underfloor/fan-coil curves.
CONF_EMITTER_TYPE = "emitter_type"
DEFAULT_EMITTER_TYPE = "radiator"
EMITTER_EXPONENT_MAP = {
    "radiator": 1.3,
    "underfloor": 1.1,
    "fan_coil": 1.0,
}

# Real-time PV-surplus controller: a deadbanded step controller on live grid
# power, layered on the DP plan (see realtime_controller.py).
CONF_REALTIME_DEADBAND_W = "realtime_deadband_w"
DEFAULT_REALTIME_DEADBAND_W = 300.0  # W - looser than a battery's ~50 W:
# heating's actuator is a whole-degree curve offset, not a continuous power
# setpoint, so chasing small fluctuations only adds wear for no benefit.
DEFAULT_REALTIME_INTERVAL_S = 60  # much slower than battery_controller's
# ~10 s: a heat pump's weather-compensation curve has nothing to gain from
# being re-commanded faster than its own control loop settles.
