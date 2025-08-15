"""Constants for the Heating Curve Optimizer integration."""

# Domain of the integration
DOMAIN = "heating_curve_optimizer"
DOMAIN_ABBREVIATION = "HCO"

# Supported platforms for this integration
PLATFORMS = ["sensor", "number"]

# Configuration keys
CONF_SOURCE_TYPE = "source_type"
CONF_SOURCES = "sources"
CONF_PRICE_SENSOR = "price_sensor"
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

# Allowed energy labels
ENERGY_LABELS = ["A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G"]

# Mapping energielabel -> U-waarde (W/m²K)
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

# Binnentemperatuur in °C voor warmteverliesberekening
INDOOR_TEMPERATURE = 21.0

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
