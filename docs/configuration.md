# Configuration

All settings are made in the UI. Main settings can be changed later through
**Configure** on the integration page; zones, PV arrays and the gas boiler
through their own **Reconfigure** menu. Changing a main setting reloads the
integration; changing the target temperature or comfort band through the
number/climate entities re-runs the optimizer immediately without a reload.

## Main entry

### Electricity prices

| Setting | Default | Notes |
|---|---|---|
| Consumption price sensor | — | Required. Must carry a forecast (see [Installation](installation.md)). The optimizer's steps follow this sensor's interval, so quarter-hour prices give quarter-hour planning. If the sensor only has a current price, that price is assumed for the whole horizon. |
| Feed-in price sensor | — | Optional. Heat-pump electricity covered by your own PV is valued at this price. Without it, 0.07 EUR/kWh is used, so PV-covered heating is never treated as free. |

### Measurement sensors (optional)

| Setting | Used for |
|---|---|
| Heat pump electrical power | Thermal power and energy sensors; thermal calibration; the gas-boiler comparison. W or kW (unit attribute required). |
| Measured supply temperature | COP of the actual operating point (thermal power sensor, calibration). Without it the planned supply temperature is used. |
| Grid import / export power | Enables the real-time PV-surplus layer. W or kW; a sensor without unit is read as W. |

### Heat pump

COP model:

```
COP = (base_cop + outdoor_coefficient × T_outdoor − k_factor × (T_supply − 35)) × compensation
COP ≤ (T_supply + 273.15) / (T_supply − T_outdoor)          (Carnot limit)
COP × defrost factor (0.6 … 1.0, between −10 °C and +6 °C at high humidity)
```

| Setting | Default | Typical |
|---|---|---|
| Base COP | 4.2 | COP at 35 °C supply and 0 °C outdoor. From the datasheet (A2/W35 is a good reference). |
| k-factor | 0.11 | COP loss per °C supply temperature above 35 °C. 0.08–0.12 for air-to-water. |
| Outdoor temperature coefficient | 0.08 | COP gain per °C outdoor temperature. 0.05–0.10. |
| Compensation factor | 1.0 | Scales the model to your measured seasonal COP. |
| Maximum thermal power | empty | Rated heating capacity in kW. Empty: 1.3 × the building's heat loss at the design outdoor temperature. |

### Heating curve

Enter the curve exactly as it is set on the heat pump; the optimizer adds an
offset of −4 … +4 °C on top of it.

| Setting | Default | Notes |
|---|---|---|
| Supply temperature at the warm end | 25 °C | Used at and above the warm-end outdoor temperature. |
| Supply temperature at the cold end | 45 °C | Used at and below the design outdoor temperature. The emitters are assumed to deliver exactly the building's heat loss at this point. |
| Design outdoor temperature | −10 °C | Dutch design value. |
| Warm-end outdoor temperature | 15 °C | |
| Minutes per 1 °C offset change | 30 | Ramp-rate limit: 30 allows 2 °C per hour. With 15-minute steps the limit is at least 1 °C per step. |

### Advanced

| Setting | Default | Notes |
|---|---|---|
| Planning horizon | 24 h | Rounded up to whole price periods and limited by the available price forecast (day-ahead prices usually reach 12–36 h ahead). |

## Heating zone

| Setting | Default | Notes |
|---|---|---|
| Name | — | |
| Heated floor area | — | m². |
| Energy label | C | Converted to a heat loss coefficient (NTA 8800 energy use × heating share / degree days), plus ventilation loss. Replaced by the calibrated value once learned. |
| Ventilation | natural (standard) | Air change rate for the ventilation loss. |
| Ceiling height | 2.5 m | Volume for the ventilation loss. |
| Construction | medium | Thermal mass: light 40, medium 90, heavy 165 Wh/(m²·K). |
| Heat emitters | radiators | Emitter exponent: radiators 1.3, underfloor 1.1, fan coils 1.0. |
| Internal heat gains | 3 W/m² | People, appliances, lighting. |
| Window area south/east/west | 0 | m² glass; solar gain uses sun position and direct/diffuse irradiance. |
| Glazing U-value | 1.2 | Determines the solar heat gain coefficient. |
| Target temperature, comfort band below/above | 20 °C, 0.3, 0.5 | The optimizer keeps the indoor temperature between target − below and target + above. Deviations are penalised quadratically (50 €/K²/h). |
| Indoor temperature sensor | — | Strongly recommended. Without it the optimizer assumes the room is at its target temperature and calibration is disabled. An unavailable sensor raises a repair issue. |
| Own heating curve (warm/cold end) | empty | Only for a separate heating circuit with its own curve. Fill both or neither. |

The first zone is the primary zone; its setpoints are exposed as number and
climate entities. Additional zones each get their own device with offset,
supply temperature, planned indoor temperature, savings and heat demand.

!!! note "Several zones on one heat pump"
    Each zone is optimized independently, as its own heating circuit. The
    shared heat pump capacity is not divided between zones.

## PV array

Peak power (kWp), orientation (azimuth, 180 = south), tilt, system
efficiency and DC coupling. Several arrays can be added. When Battery
Controller has PV arrays, you can import one.

## Gas boiler (hybrid)

Gas price sensor (EUR/m³), boiler efficiency (default 0.90), calorific
value (default 9.77 kWh/m³, Dutch upper heating value) and **Gas as comfort
backup** (default on): recommend the boiler whenever the heat pump cannot
restore the comfort band within 3 hours, even if gas is more expensive.
Switch it off to use gas only when it is also cheaper. See
[How it works](algorithm.md#hybrid-gas-boiler) for when the boiler is
recommended.
