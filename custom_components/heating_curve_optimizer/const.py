"""Constants for the Heating Curve Optimizer integration."""

# Domain of the integration
DOMAIN = "heating_curve_optimizer"
DOMAIN_ABBREVIATION = "HCO"

# Supported platforms for this integration
PLATFORMS = ["sensor", "number", "binary_sensor"]

# Configuration keys
CONF_SOURCE_TYPE = "source_type"
CONF_SOURCES = "sources"
CONF_PRICE_SENSOR = "price_sensor"
CONF_CONSUMPTION_PRICE_SENSOR = "consumption_price_sensor"
CONF_PRODUCTION_PRICE_SENSOR = "production_price_sensor"
CONF_PRICE_SETTINGS = "price_settings"

# New configuration keys for the heating curve optimizer
CONF_AREA_M2 = "area_m2"
CONF_ENERGY_LABEL = "energy_label"
# Planning window and time base configuration
CONF_PLANNING_WINDOW = "planning_window"
CONF_TIME_BASE = "time_base"
# Default values
DEFAULT_PLANNING_WINDOW = 6  # hours
DEFAULT_TIME_BASE = 60  # minutes per step
# Glass related configuration
CONF_GLASS_EAST_M2 = "glass_east_m2"
CONF_GLASS_WEST_M2 = "glass_west_m2"
CONF_GLASS_SOUTH_M2 = "glass_south_m2"
CONF_GLASS_U_VALUE = "glass_u_value"
CONF_POWER_CONSUMPTION = "power_consumption"
CONF_INDOOR_TEMPERATURE_SENSOR = "indoor_temperature_sensor"
CONF_SUPPLY_TEMPERATURE_SENSOR = "supply_temperature_sensor"
CONF_K_FACTOR = "k_factor"
CONF_BASE_COP = "base_cop"
CONF_COP_COMPENSATION_FACTOR = "cop_compensation_factor"
CONF_OUTDOOR_TEMP_COEFFICIENT = "outdoor_temp_coefficient"
CONF_HEAT_CURVE_MIN_OUTDOOR = "heat_curve_min_outdoor"
CONF_HEAT_CURVE_MAX_OUTDOOR = "heat_curve_max_outdoor"

# PV panel configuration
CONF_PV_EAST_WP = "pv_east_wp"
CONF_PV_SOUTH_WP = "pv_south_wp"
CONF_PV_WEST_WP = "pv_west_wp"
CONF_PV_TILT = "pv_tilt"

# Default PV tilt angle (degrees) - typical for Netherlands
DEFAULT_PV_TILT = 35

# Allowed energy labels
ENERGY_LABELS = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]

# Mapping energielabel -> primary energy consumption (kWh/m²/year)
# Based on NTA 8800 standard (since Jan 2021)
# Using midpoint values for each label range
ENERGY_LABEL_CONSUMPTION = {
    "A+++": 50,   # Very low energy (passive house level)
    "A++": 75,    # Very low energy
    "A+": 90,     # Very low energy
    "A": 132,     # 105-160 kWh/m²/year (midpoint)
    "B": 175,     # 160-190 kWh/m²/year (midpoint)
    "C": 220,     # 190-250 kWh/m²/year (midpoint)
    "D": 275,     # 250-300 kWh/m²/year (midpoint)
    "E": 340,     # 300-380 kWh/m²/year (midpoint)
    "F": 420,     # 380-460 kWh/m²/year (midpoint)
    "G": 500,     # >460 kWh/m²/year (estimate)
}

# Heating fraction of total primary energy by label
# Better insulated homes have relatively more DHW energy use
HEATING_FRACTION_MAP = {
    "A+++": 0.40,  # Passive house: excellent insulation, DHW is major portion
    "A++": 0.42,
    "A+": 0.45,
    "A": 0.50,     # Good insulation
    "B": 0.55,
    "C": 0.60,
    "D": 0.65,
    "E": 0.68,
    "F": 0.70,
    "G": 0.72,     # Poor insulation: heating dominates
}

# Heating degree-days for Netherlands (base 18°C)
# Average value for Dutch climate
HEATING_DEGREE_DAYS_NL = 2900

# Binnentemperatuur in °C voor warmteverliesberekening
INDOOR_TEMPERATURE = 21.0

# Legacy U-value map (deprecated, kept for backward compatibility)
# DO NOT USE - these values incorrectly treat energy labels as U-values
U_VALUE_MAP = {
    "A+++": 0.2,
    "A++": 0.3,
    "A+": 0.4,
    "A": 0.6,
    "B": 0.8,
    "C": 1.0,
    "D": 1.2,
    "E": 1.4,
    "F": 1.6,
    "G": 1.8,
}


def calculate_htc_from_energy_label(
    energy_label: str, area_m2: float, heating_degree_days: float = HEATING_DEGREE_DAYS_NL
) -> float:
    """
    Calculate Heat Transfer Coefficient (HTC) from energy label.

    This converts the energy label (primary energy in kWh/m²/year) to an
    actual building heat loss coefficient (W/K) using heating degree-days.

    Formula:
    - Annual heating energy (kWh) = Label energy × Area × Heating fraction
    - HTC (W/K) = Annual heating energy × 1000 / (HDD × 24)

    Args:
        energy_label: Energy label (A+++, A++, A+, A, B, C, D, E, F, G)
        area_m2: Floor area in m²
        heating_degree_days: Heating degree-days for the climate (default: NL)

    Returns:
        Heat Transfer Coefficient in W/K
    """
    # Get energy consumption and heating fraction for this label
    energy_per_m2 = ENERGY_LABEL_CONSUMPTION.get(energy_label.upper(), 220)
    heating_fraction = HEATING_FRACTION_MAP.get(energy_label.upper(), 0.60)

    # Calculate annual heating energy (kWh/year)
    annual_heating_energy = energy_per_m2 * area_m2 * heating_fraction

    # Convert to HTC using degree-days
    # HTC (W/K) = kWh/year × 1000 W/kW / (degree-days × 24 hours/day)
    if heating_degree_days <= 0:
        heating_degree_days = HEATING_DEGREE_DAYS_NL

    htc = annual_heating_energy * 1000.0 / (heating_degree_days * 24.0)

    return htc

# Default COP at a supply temperature of 35 °C
DEFAULT_COP_AT_35 = 4.2

# Default decline in COP per °C supply temperature increase
DEFAULT_K_FACTOR = 0.11

# Default increase in COP per °C outdoor temperature rise
DEFAULT_OUTDOOR_TEMP_COEFFICIENT = 0.08

# Default COP compensation factor
DEFAULT_COP_COMPENSATION_FACTOR = 1.0

# Possible source types
SOURCE_TYPE_CONSUMPTION = "Electricity consumption"
SOURCE_TYPE_PRODUCTION = "Electricity production"

# allowed values for source_type
SOURCE_TYPES = [
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
]

CONF_CONFIGS = "configurations"
