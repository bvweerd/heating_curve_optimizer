"""Constants for the Heatpump Curve Optimizer integration."""

# Domain of the integration
DOMAIN = "heatcurve_optimizer"
DOMAIN_ABBREVIATION = "HCO"

PLATFORMS = ["sensor"]

# Configuration keys
CONF_SOURCE_TYPE = "source_type"
CONF_SOURCES = "sources"
CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_SETTINGS = "price_settings"

# New configuration keys for the heatpump optimizer
CONF_AREA_M2 = "area_m2"
CONF_ENERGY_LABEL = "energy_label"
# Glass related configuration
CONF_GLASS_EAST_M2 = "glass_east_m2"
CONF_GLASS_WEST_M2 = "glass_west_m2"
CONF_GLASS_SOUTH_M2 = "glass_south_m2"
CONF_GLASS_U_VALUE = "glass_u_value"
# One or more Solcast sensors. The sensors should expose a ``detailed forecast``
# attribute with the expected PV production for the coming hours. The raw list
# from these attributes will be copied to the solar gain sensor attributes.
CONF_SOLAR_FORECAST = "solar_forecasts"
CONF_POWER_CONSUMPTION = "power_consumption"

# Allowed energy labels
ENERGY_LABELS = ["A", "B", "C", "D", "E", "F", "G"]

# Mapping energielabel -> U-waarde (W/m²K)
U_VALUE_MAP = {
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

# Efficiëntiefactor voor zoninstraling
SOLAR_EFFICIENCY = 0.15

# Possible source types
SOURCE_TYPE_CONSUMPTION = "Electricity consumption"
SOURCE_TYPE_PRODUCTION = "Electricity production"

# allowed values for source_type
SOURCE_TYPES = [
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
]

CONF_CONFIGS = "configurations"
