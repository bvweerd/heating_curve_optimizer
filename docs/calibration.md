# Calibration

The optimizer starts from estimates: heat loss from the energy label,
thermal mass from the construction class, solar gain from the window areas,
the COP curve from your datasheet values and radiators sized exactly for the
design temperature. Calibration replaces these estimates with values learned
from your own measurements.

## What you need

| Sensor | Required for | Configured in |
|---|---|---|
| Indoor temperature | Everything | Heating zone |
| Heat pump electrical power **or** thermal power | Building model | Main entry |
| Measured supply temperature | Emitter curve | Main entry |
| Heat pump thermal power (heat meter, or output reported by the heat pump) | COP curve; makes all results independent of the COP model | Main entry, optional |
| Gas meter of the hybrid boiler (m³ or kWh) | COP level without a heat meter | Gas boiler, optional |
| Tap water heating active (binary) | Leaving tap water runs out | Main entry, optional |
| Window/door contacts (binary) | Leaving ventilation out | Heating zone, optional |

Calibration is only possible with **one** heating zone: with several zones
the heat pump's output cannot be attributed to one of them.

## What is learned

### Building (phase 1)

Every observation window (at least 15 minutes, until the indoor temperature
has moved 0.3 °C, at most 6 hours) gives the mean heat input, solar gain and
indoor–outdoor difference, and the rate of temperature change. A regularised
least-squares fit of

```
C · rate + UA · ΔT − s · Q_solar − g = Q_heat (+ Q_gas)
```

gives the **heat loss coefficient** `UA`, the **thermal mass** `C`, a
**solar factor** `s` on the modelled window gain and the **internal gains**
`g`. The fit is pulled towards the energy-label estimate while there are few
samples; that pull fades as evidence accumulates.

Windows are discarded when tap water was heated, a window or door was open,
the indoor temperature jumped more than 1 °C between two runs, or there was
a gap in the data.

!!! note "Without a heat meter"
    Heat is then electrical power × the COP model. If the COP model is, say,
    10 % optimistic, the learned `UA` and `C` are 10 % high as well, but the
    **time constant** (`C/UA`, how long the building coasts) and the
    **electricity** needed to keep it warm (`UA/COP`) are still right. Those
    are what the optimizer uses, so calibration is worthwhile without a heat
    meter.

### COP level from the gas meter (phase 2b)

On a hybrid system, windows heated by the boiler contain an absolute heat
measurement: gas volume × calorific value × efficiency. With at least five
such windows the fit also learns the **COP scale** – how far the COP model
is off. Coarse meter updates are fine: the gas meter is read at the start
and end of each window, and windows are at least 2 hours long when a gas
meter is configured. This is only reliable if the boiler does not also heat
tap water.

### COP curve from a heat meter (phase 2a)

With both electrical and thermal power, every run where the heat pump is
running gives a measured COP at the current outdoor and supply temperature.
A fit of `COP / defrost = base + a · T_outdoor − k · (T_supply − 35)` gives
the base COP, the outdoor coefficient and the k-factor.

### Emitter curve (phase 3)

With a measured supply temperature, each run with the heat pump running
gives heat output versus `T_supply − T_indoor`. A log-linear fit gives the
**nominal output** of the radiators or floor heating at the design point
and the **exponent**. This determines how much heat an offset of +1 °C
really adds.

### One or two thermal masses (phase 4)

The model uses one thermal mass. The *Model accuracy* sensor checks whether
that is enough: if its prediction errors are strongly correlated from hour
to hour (`two_mass_suspected: true`, typically with floor heating in a
heavy screed), the building responds with a fast and a slow part that one
mass cannot describe. This is a diagnosis only; the optimizer keeps the
single-mass model.

## Quality checks

A building fit is used only when all hold:

- at least 30 samples;
- both heating and cooling windows (each at least 15 %);
- coefficient of determination R² ≥ 0.5;
- plausible values: `UA` and `C` within 0.3–3× the label estimate, solar
  factor 0–2.5, internal gains 0–12 W/m², COP scale 0.5–1.5.

The COP curve needs 20 samples and a plausible result; the emitter curve 20
samples, R² ≥ 0.5 and an exponent of 0.9–1.6.

## Modes

Set per heating zone (*Calibration*):

| Mode | Behaviour |
|---|---|
| Off | Nothing is learned. |
| Observe (default) | Learns and shows results. When the fit passes the quality checks, a repair notification asks whether to use it; confirming switches the zone to *Apply*. |
| Apply | The learned models are used as soon as they pass the quality checks. |

The service `heating_curve_optimizer.reset_thermal_calibration` discards
everything learned.

## Following the results

The **Calibration** sensor has one of the states *Off*, *Not possible*,
*Collecting measurements*, *Provisional* (enough samples, quality checks not
yet passed), *Ready, waiting for confirmation* or *Applied*. Its attributes
explain the result:

| Attribute | Meaning |
|---|---|
| `progress_pct`, `sample_count`, `samples_needed` | How far along it is. |
| `last_result`, `excluded_windows` | Why the latest window did or did not count. |
| `heat_source` | `measured` (heat meter) or `cop_model`. |
| `effective_energy_label`, `heat_loss_vs_label_pct` | "Your house behaves like label B: 18 % less heat loss than label C suggests." |
| `time_constant_hours`, `cooling_rate_at_5c_k_per_h` | How fast the house cools without heating at 5 °C outside. |
| `learned_solar_factor`, `learned_internal_gain_w` | Window gain and internal gains. |
| `learned_cop_at_a0_w35`, `cop_vs_configured_pct`, `learned_k_factor` | Measured COP curve (heat meter). |
| `learned_cop_scale`, `gas_windows` | COP level from the gas meter. |
| `learned_emitter_power_kw`, `assumed_emitter_power_kw`, `learned_emitter_exponent` | Emitter curve. |
| `r_squared`, `plausible` | Quality of the building fit. |
| `*_in_use` | The values the optimizer is using right now. |

The **Model accuracy** sensor is the mean absolute error (K) of the
optimizer's one-hour-ahead indoor temperature predictions over the last 24
hours. `mae_label_model_k` is the same error for the energy-label model, so
you can see whether calibration helps. The comparison assumes the planned
offset was actually applied. `errors_k` holds the recent errors for a
graph; `residual_autocorrelation` and `two_mass_suspected` are the phase 4
diagnosis.
