"""Constants for the Dynamic Energy Heatpump Optimizer integration."""

# Domain of the integration
DOMAIN = "dynamic_energy_heatpump_optimizer"
DOMAIN_ABBREVIATION = "DEHO"

PLATFORMS = ["sensor"]

# Configuration keys
CONF_PRICE_SENSOR = "price_sensor"
CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temperature_sensor"
CONF_SUPPLY_TEMP_SENSOR = "supply_temperature_sensor"
CONF_MAX_HEATPUMP_POWER = "max_heatpump_power"

# COP polynomial coefficients as extracted from reference PDFs.
# Formulas are of the form: COP(T) = a + b*T + c*T**2
COP_OUTDOOR_COEFFS = (-0.00, 0.071, 4.55363)
COP_SUPPLY_COEFFS = (0.00, -0.152, 8.88286)

# Additional configuration keys
CONF_K_FACTOR = "k_factor"

DEFAULT_MAX_HEATPUMP_POWER = 5.0
DEFAULT_PLANNING_HORIZON = 12

DEFAULT_K_FACTOR = 1.0
