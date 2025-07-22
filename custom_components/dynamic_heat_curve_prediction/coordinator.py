import logging
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.core import HomeAssistant
from .const import DOMAIN, DEFAULT_HORIZON_HOURS, ENERGY_LABEL_U
from .model import predict_indoor_temps, optimize_offsets

_LOGGER = logging.getLogger(__name__)

class HeatCurveCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self.config = config
        self.area = config.get("area_m2", 100)
        self.label = config.get("energy_label", "C")
        self.horizon = config.get("horizon_hours", DEFAULT_HORIZON_HOURS)
        self.U = ENERGY_LABEL_U.get(self.label.upper(), 1.0)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=15),
        )

    async def _async_update_data(self):
        temp_forecast = [float(self.hass.states.get("sensor.outdoor_temperature").state)] * self.horizon
        solar_forecast = [float(self.hass.states.get("sensor.forecast_solar_energy_production_today").state)] * self.horizon
        price_forecast = [float(self.hass.states.get("sensor.nordpool_kwh_nl_eur_0_10").state)] * self.horizon

        indoor_forecast = predict_indoor_temps(temp_forecast, solar_forecast, self.area, self.U)
        offsets = optimize_offsets(temp_forecast, solar_forecast, price_forecast, self.area, self.U, self.horizon, cop_base=3.0)

        return {
            "offsets": offsets,
            "indoor_forecast": indoor_forecast,
        }