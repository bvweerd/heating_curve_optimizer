"""Constants for the Dynamic Energy Heatpump Optimizer integration."""

# Domain of the integration
DOMAIN = "dynamic_energy_heatpump_optimizer"
DOMAIN_ABBREVIATION = "DEHO"

PLATFORMS = ["sensor"]

# Configuration keys
CONF_SOURCE_TYPE = "source_type"
CONF_SOURCES = "sources"
CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_SETTINGS = "price_settings"
CONF_ENERGY_SENSORS = "energy_sensors"
CONF_SOLAR_SENSORS = "solar_sensors"
CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temperature_sensor"
CONF_SUPPLY_TEMP_SENSOR = "supply_temperature_sensor"
CONF_ROOM_TEMP_SENSOR = "room_temperature_sensor"
CONF_MAX_HEATPUMP_POWER = "max_heatpump_power"
CONF_CURRENT_POWER_SENSOR = "current_power_sensor"

# Possible source types
SOURCE_TYPE_CONSUMPTION = "Electricity consumption"
SOURCE_TYPE_PRODUCTION = "Electricity production"
SOURCE_TYPE_GAS = "Gas consumption"

# allowed values for source_type
SOURCE_TYPES = [
    SOURCE_TYPE_CONSUMPTION,
    SOURCE_TYPE_PRODUCTION,
    SOURCE_TYPE_GAS,
]

CONF_CONFIGS = "configurations"

PRICE_SETTINGS_KEYS = [
    "per_unit_supplier_electricity_markup",
    "per_unit_supplier_electricity_production_markup",
    "per_unit_government_electricity_tax",
    "per_unit_supplier_gas_markup",
    "per_unit_government_gas_tax",
    "per_day_grid_operator_electricity_connection_fee",
    "per_day_supplier_electricity_standing_charge",
    "per_day_government_electricity_tax_rebate",
    "per_day_grid_operator_gas_connection_fee",
    "per_day_supplier_gas_standing_charge",
    "vat_percentage",
    "production_price_include_vat",
]

DEFAULT_PRICE_SETTINGS = {
    "per_unit_supplier_electricity_markup": 0.02,
    "per_unit_supplier_electricity_production_markup": 0.0,
    "per_unit_government_electricity_tax": 0.1088,
    "per_unit_supplier_gas_markup": 0.0,
    "per_unit_government_gas_tax": 0.0,
    "per_day_grid_operator_electricity_connection_fee": 0.25,
    "per_day_supplier_electricity_standing_charge": 0.25,
    "per_day_government_electricity_tax_rebate": 0.25,
    "per_day_grid_operator_gas_connection_fee": 0.0,
    "per_day_supplier_gas_standing_charge": 0.0,
    "vat_percentage": 21.0,
    "production_price_include_vat": True,
}

# COP polynomial coefficients as extracted from reference PDFs.
# Formulas are of the form: COP(T) = a + b*T + c*T**2
COP_OUTDOOR_COEFFS = (-0.00, 0.071, 4.55363)
COP_SUPPLY_COEFFS = (0.00, -0.152, 8.88286)

# Heat loss coefficients per energy label (kW per m² per °C)
HEAT_LOSS_FACTORS = {
    "A/B": 1.5,
    "C/D": 2.5,
    "E/F/G": 3.5,
}

# Additional configuration keys
CONF_K_FACTOR = "k_factor"
CONF_HEAT_LOSS_LABEL = "heat_loss_label"
CONF_FLOOR_AREA = "floor_area"
CONF_PLANNING_HORIZON = "planning_horizon"

DEFAULT_MAX_HEATPUMP_POWER = 5.0
DEFAULT_PLANNING_HORIZON = 12

DEFAULT_K_FACTOR = 1.0
