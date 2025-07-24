"""Constants for the Heatpump Curve Optimizer integration."""

# Domain of the integration
DOMAIN = "optimizer"
DOMAIN_ABBREVIATION = "OPT"

PLATFORMS = ["sensor"]

# Configuration keys
CONF_SOURCE_TYPE = "source_type"
CONF_SOURCES = "sources"
CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_SETTINGS = "price_settings"

# New configuration keys for the heatpump optimizer
CONF_AREA_M2 = "area_m2"
CONF_ENERGY_LABEL = "energy_label"
CONF_OUTDOOR_TEMPERATURE = "outdoor_temperature"
CONF_SOLAR_FORECAST = "solar_forecast"
CONF_POWER_CONSUMPTION = "power_consumption"

# Allowed energy labels
ENERGY_LABELS = ["A", "B", "C", "D", "E", "F", "G"]

# Possible source types
SOURCE_TYPE_CONSUMPTION = "Electricity consumption"
SOURCE_TYPE_PRODUCTION = "Electricity production"

# allowed values for source_type
SOURCE_TYPES = [
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
]

CONF_CONFIGS = "configurations"
