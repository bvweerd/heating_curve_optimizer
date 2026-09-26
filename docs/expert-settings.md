# Expert settings

The integration ships with defaults that work well for most Dutch residential
heat pump installations. The settings on this page are not exposed in the
normal configuration flow; they exist for installations where the defaults do
not fit. You can set them by adding them directly to the integration's
`options` dictionary in `configuration.yaml`, or by using the [HA Storage
editor](https://www.home-assistant.io/docs/tools/dev-tools/).

!!! warning "Proceed with care"
    Changing an expert setting incorrectly can make the optimizer run
    inefficiently or miss comfort constraints. Start with the defaults, observe
    the system for a few weeks, and only adjust a setting when you have a
    clear reason.

---

## Optimizer tuning

These settings control the trade-off the dynamic-programming optimizer makes
between electricity cost and indoor comfort.

### `comfort_penalty_weight`

| | |
|---|---|
| Default | `50.0` EUR/K²/h |
| Range | > 0 |

Cost assigned to each degree-kelvin² per hour that the indoor temperature
falls outside the comfort band. The optimizer minimises the sum of energy
cost and comfort penalty, so this weight determines how aggressively it
trades one for the other.

**Why 50 EUR/K²/h?** At this value a 0.5 K overshoot of the lower comfort
boundary costs 50 × 0.25 = 12.5 EUR/h of "penalty", which exceeds typical
Dutch day-ahead price spreads. In practice the optimizer rarely leaves the
band except when pre-heating is genuinely cheaper than recovering later.

**Increase** if the optimizer produces temperature swings that feel too large
(e.g., it regularly coasts 0.4 K below comfort). **Decrease** if you want
more aggressive price-following and are comfortable with slightly wider
temperature swings.

---

### `cycling_penalty_weight`

| | |
|---|---|
| Default | `0.01` EUR per K² of offset change per step |
| Range | ≥ 0 |

Penalty per planning step for each square kelvin of heating-curve offset
change. This discourages the optimizer from rapidly swinging the offset up
and down between steps, which a real heat pump's weather-compensation control
loop cannot faithfully track.

**Increase** for heat pumps with a slow internal control loop or systems where
the hydraulics respond sluggishly (e.g., large underfloor heating volumes).
**Decrease** if your heat pump tracks setpoints quickly and you want it to
follow price signals more closely. Setting it to 0 removes the smoothing
entirely.

---

### `hard_floor_penalty`

| | |
|---|---|
| Default | `1000.0` EUR |
| Range | > 0 |

One-time penalty applied whenever the predicted indoor temperature falls below
`comfort_min − 1.5 K`. This hard floor makes the optimizer treat very deep
undershoot as almost-forbidden rather than just expensive.

The default of 1000 EUR is large enough to override any realistic electricity
price signal. It is only relevant for heavily undersized heat pumps or
extreme cold snaps where the building cannot be kept warm regardless of what
the optimizer does. Reducing it lets the optimizer accept deeper undershoot
when it cannot be avoided; raising it further has no practical effect once
it already dominates the cost landscape.

---

### `feed_in_price_fallback`

| | |
|---|---|
| Default | `0.07` EUR/kWh |
| Range | ≥ 0 |

Opportunity cost assigned to electricity covered by PV production when no
feed-in price sensor is configured. This prevents the optimizer from treating
PV-covered heating as free: using solar electricity for heating is only
worthwhile if the alternative (exporting it) is worth less than this amount.

Set this to your actual feed-in tariff if you do not have a price sensor for
it. In the Netherlands typical net-metering-equivalent values are 0.05–0.10
EUR/kWh; countries without net metering may use values as low as 0.02.

---

### `offset_min` / `offset_max`

| | |
|---|---|
| Default | `−4` K / `+4` K |
| Range | Integers, `offset_min` < `offset_max` |

The range of heating-curve offsets the optimizer may choose. The default
±4 K is the range entered on most heat pump controllers (e.g., Daikin,
Nibe, Vaillant). Check your heat pump's installer menu: some allow ±6 K or
even wider.

**Widen** (`offset_min = −6`, `offset_max = 6`) only if your heat pump's
controller supports and reliably tracks that range, and your automation
applies the full offset value. A wider range gives the optimizer more
pre-heating and coasting headroom, which can increase savings. **Do not**
set `offset_max` wider than the heat pump will honour; the optimizer will
plan for temperatures the system cannot reach.

---

### `heatpump_headroom`

| | |
|---|---|
| Default | `1.3` (ratio) |
| Range | > 1.0 |

Ratio of assumed heat pump heating capacity to the emitter design-point
output. At the design outdoor temperature the emitters are sized to deliver
exactly the building's heat loss; the heat pump is assumed to be able to
deliver `heatpump_headroom` times that amount. This matters when `offset_max`
is applied: the optimizer checks whether the heat pump can actually sustain
the elevated supply temperature.

**Increase** (e.g., to 1.5–2.0) if your heat pump is notably oversized
relative to the emitter circuit. **Decrease** toward 1.0 if the heat pump
is closely matched to the building and you observe it struggling at high
offsets in cold weather. The maximum thermal power setting in the main
configuration overrides this estimate when provided.

---

## Calibration

These settings control how the calibration subsystem collects, fits and
validates learned building, COP and emitter models. See also [Calibration](calibration.md).

### `calibration_window`

| | |
|---|---|
| Default | `300` samples |
| Range | ≥ `min_samples_to_apply` |

Size of the rolling sample buffer for the building model fit. Older samples
beyond this window are dropped. Larger values make the fit more stable but
slower to track real changes (e.g., after a renovation). The default 300
samples at roughly one observation per 15–30 minutes represents 3–6 days of
steady data.

**Reduce** (e.g., to 100–150) after a major renovation or significant
building change where you want the fit to adapt quickly.

---

### `min_samples_to_apply`

| | |
|---|---|
| Default | `30` |
| Range | ≥ 1 |

Minimum number of observation windows that must have been collected before a
calibrated building model can be applied. Each window closes when the indoor
temperature has moved at least `min_indoor_temp_delta` K, so 30 windows
corresponds to 30 distinct heating or coasting events.

**Reduce** if you want calibration to kick in faster after installation.
**Increase** if you observe the fit oscillating in the first weeks (noisy
sensors or unusual operating patterns).

---

### `min_r_squared`

| | |
|---|---|
| Default | `0.5` |
| Range | 0.0 – 1.0 |

Coefficient of determination (R²) quality gate for the building model fit.
The fit must explain at least this fraction of the observed indoor-temperature
variance before it is used. R² = 0.5 means the model accounts for half of
the variation; values closer to 1.0 indicate a tighter fit.

**Lower** (e.g., to 0.35) for buildings with noisy indoor temperature sensors
or many uncontrolled disturbances (frequent opening of external doors,
irregular occupancy). **Raise** (e.g., to 0.65) if you only want the
calibration to apply when the data strongly supports it.

---

### `min_share_each_direction`

| | |
|---|---|
| Default | `0.15` (15 %) |
| Range | 0.0 – 0.5 |

Minimum fraction of samples that must show heating (heat pump on) and the
same fraction that must show coasting (heat pump off). Without observations
from both directions the fit cannot reliably separate thermal mass from heat
loss, because the two effects look the same in a regression that only sees
heating data.

**Lower** (e.g., to 0.08) in climates with very long continuous heating
seasons where coasting windows are rare. Note that the fit quality will
typically drop; pair with a lower `min_r_squared` if needed.

---

### `min_indoor_temp_delta`

| | |
|---|---|
| Default | `0.3` K |
| Range | > 0 |

An observation window is closed (and added to the sample pool) only once the
indoor temperature has moved by at least this amount from the window's
starting temperature. This filters out windows where the building was
thermally near-static and provides little information about the dynamics.

**Lower** (e.g., to 0.2) for high-precision temperature sensors (±0.1 K or
better). **Raise** (e.g., to 0.5) for sensors with high noise or resolution
coarser than 0.1 K steps.

---

### `prior_strength`

| | |
|---|---|
| Default | `3.0` (equivalent samples) |
| Range | ≥ 0 |

Ridge regularisation strength: the number of hypothetical "energy-label
prior" samples added to the regression. A higher value pulls the fit toward
the energy-label estimate and makes it more robust when real data is sparse
or noisy. At 0 the fit is pure ordinary least squares.

**Increase** (e.g., to 10) if the early calibration estimates appear
unreasonably far from the energy label. **Decrease** (e.g., to 1) once you
have many samples and trust the measurements over the label.

---

### `ratio_bounds_lower` / `ratio_bounds_upper`

| | |
|---|---|
| Default | `0.3` / `3.0` |
| Range | > 0 |

The fitted heat loss coefficient (UA) and thermal mass (C) must lie within
these multiples of the energy-label estimate. Values outside the bounds
indicate either bad sensor data or a label that is completely wrong; in either
case the fit is rejected.

**Widen** (e.g., `0.2` / `5.0`) for buildings where the energy label is
known to be significantly wrong (very old label, converted building, major
undocumented insulation upgrade). **Tighten** if you want to be conservative
and only accept fits close to the label.

---

### `solar_factor_bounds_upper`

| | |
|---|---|
| Default | `2.5` |
| Range | > 0 |

Upper bound on the learned solar gain multiplier relative to the model
prediction from window area and U-value. A value of 2.5 allows the learned
gain to be 2.5 times higher than the model suggests. This accommodates
buildings where the effective solar aperture (greenhouse effect, high-SHGC
glass, obstructions not in the model) differs substantially from the
configured window parameters.

Raise if you consistently see the model underestimating solar gain in spring.
Lower to 1.5 if you want the fit to stay closer to the theoretical value.

---

### `internal_gain_max_w_per_m2`

| | |
|---|---|
| Default | `12.0` W/m² |
| Range | ≥ 0 |

Maximum plausible internal heat gain per square metre of floor area. This is
the upper bound on the learned internal gains term in the building fit. The
default covers well-occupied residential use (people, cooking, appliances,
lighting); values above 12 W/m² for a home indicate something else is
generating heat (a server room, industrial equipment).

**Raise** (e.g., to 15–20 W/m²) for office or commercial premises where
equipment loads are higher.

---

### `min_cop_samples`

| | |
|---|---|
| Default | `20` |
| Range | ≥ 1 |

Minimum number of COP measurement points (each a heat-pump run with both
electrical and thermal power available) before the COP curve fit is
attempted. Requires a heat meter.

---

### `cop_scale_bounds_lower` / `cop_scale_bounds_upper`

| | |
|---|---|
| Default | `0.5` / `1.5` |
| Range | > 0 |

The learned COP scale factor (how far the configured COP model deviates from
reality) must lie within these bounds. Values outside suggest either a
mis-configured COP model or measurement errors. At the defaults, the learned
COP can deviate from the model by up to 50 % in either direction.

**Widen** (e.g., `0.3` / `2.0`) if you know your heat pump's real COP is
very far from the datasheet values. **Tighten** if you want calibration to
only apply small corrections.

---

### `min_emitter_samples`

| | |
|---|---|
| Default | `20` |
| Range | ≥ 1 |

Minimum number of measurement points (heat pump runs with a measured supply
temperature) before the emitter curve fit is attempted.

---

### `emitter_exponent_bounds_lower` / `emitter_exponent_bounds_upper`

| | |
|---|---|
| Default | `0.9` / `1.6` |
| Range | > 0 |

Allowed range for the fitted emitter exponent `n` in the power law
`Q = Q_nominal × ((T_supply − T_in) / ΔT_nominal)^n`. The standard EN 442
value for radiators is approximately 1.3; underfloor heating is around 1.1;
fan coils are around 1.0. The bounds cover all common emitter types with
some margin for real-world variation.

If the fit converges outside these bounds it is rejected. **Loosen** only if
you have strong evidence (e.g., manufacturer data) that your emitter truly
operates outside this range.

---

### `calibration_min_hours` / `calibration_max_hours`

| | |
|---|---|
| Default | `0.25` h / `6.0` h |
| Range | > 0; min < max |

Duration bounds for a single observation window. Windows shorter than
`calibration_min_hours` are discarded (too few data points for a reliable
mean). Windows longer than `calibration_max_hours` are also discarded
because over a very long interval external conditions change enough to
invalidate the steady-state assumption.

**Shorten** `calibration_min_hours` to 0.1 h if your heat pump cycles very
frequently and you still want to capture short heating events.
**Lengthen** `calibration_max_hours` to 8–12 h for buildings with very long
thermal time constants (large thermal mass, low UA).

---

### `calibration_max_jump_c`

| | |
|---|---|
| Default | `1.0` K |
| Range | > 0 |

If the indoor temperature rises or falls by more than this amount between two
consecutive 5-minute measurements, the current observation window is
discarded. Sudden jumps indicate an external disturbance (oven turned on,
external door opened) that would corrupt the thermal model fit.

**Raise** (e.g., to 1.5 K) if your indoor sensor is in a location where it
sees brief temperature spikes (e.g., near a kitchen) that are not
representative of the whole zone. **Lower** (e.g., to 0.6 K) for stable,
well-mixed zones where any large jump is a real anomaly.

---

### `gas_min_window_hours`

| | |
|---|---|
| Default | `2.0` h |
| Range | > 0 |

When a gas meter is configured (hybrid system), observation windows used for
COP-scale calibration must be at least this long. Longer windows reduce the
effect of gas meter quantisation (the meter increments in discrete steps) on
the heat estimate.

**Increase** for meters with coarse resolution (e.g., 0.1 m³ increments,
roughly 1 kWh per step). **Decrease** only if you have a high-resolution
pulse meter where meter quantisation is not an issue.

---

### `min_residual_samples`

| | |
|---|---|
| Default | `48` |
| Range | ≥ 1 |

Minimum number of residual samples needed before the autocorrelation
diagnostic (used to detect a two-mass building) is computed. 48 samples at
one-per-hour corresponds to two days of data.

---

### `two_mass_autocorrelation`

| | |
|---|---|
| Default | `0.6` |
| Range | 0.0 – 1.0 |

Residual autocorrelation threshold above which the `two_mass_suspected`
diagnostic flag is set. If the building model's one-step prediction errors
are correlated above this threshold, it suggests the building has two
distinct thermal masses (e.g., light walls plus a heavy concrete floor
slab) that the single-mass model cannot fully capture. This is informational
only; the optimizer continues using the single-mass model.

**Lower** (e.g., to 0.4) to flag the condition more readily. **Raise** to
0.8 if you only want the flag set for clear-cut two-mass situations.

---

## Climate and detection

These settings adapt the integration to local climate and sensor
characteristics.

### `ground_albedo`

| | |
|---|---|
| Default | `0.2` |
| Range | 0.0 – 1.0 |

Ground reflectance used in the plane-of-array irradiance calculation for PV
and window solar gain. 0.2 is a standard value for grass or soil (green
vegetation). Fresh snow reflects 0.7–0.9; urban concrete approximately 0.3.

In snowy climates with long snow cover, raising this to 0.5–0.7 during
winter will improve the solar gain estimate.

---

### `defrost_free_threshold`

| | |
|---|---|
| Default | `6.0` °C |
| Range | any; must be > `defrost_cold_threshold` |

Outdoor temperature above which defrost cycles are assumed not to occur and
no COP penalty is applied. The default of 6 °C suits the Dutch maritime
climate where frost on the evaporator is common between roughly −10 °C and
+6 °C.

**Lower** (e.g., to 3–4 °C) for drier or more continental climates where
the dew point is lower and frosting occurs less frequently at mild
temperatures.

---

### `defrost_cold_threshold`

| | |
|---|---|
| Default | `−10.0` °C |
| Range | any; must be < `defrost_free_threshold` |

Outdoor temperature below which air is too dry for significant icing and the
defrost penalty returns to zero. Between `defrost_cold_threshold` and
`defrost_free_threshold` the defrost COP penalty is applied with a triangular
profile (maximum at the midpoint).

Adjust for your local climate's typical dew-point temperature at cold
conditions.

---

### `defrost_base_penalty`

| | |
|---|---|
| Default | `0.25` |
| Range | 0.0 – 1.0 |

Maximum fractional COP reduction due to defrost cycles, applied at the
midpoint between the two thresholds. At the default, COP is reduced by at
most 25 % at the worst point. This accounts for the energy consumed by
defrost heaters and the interruption of heating during the defrost cycle.

**Lower** (e.g., to 0.15) for heat pumps with efficient hot-gas defrost that
completes quickly. **Raise** for systems that defrost frequently or for a
long time.

---

### `defrost_min_cop_multiplier`

| | |
|---|---|
| Default | `0.60` |
| Range | 0.0 – 1.0 |

Safety floor: defrost penalties can never reduce the effective COP below this
fraction of the nominal value. Prevents unrealistically low COP values from
dominating the cost calculation in borderline conditions.

---

### `min_cop`

| | |
|---|---|
| Default | `0.5` |
| Range | > 0 |

Absolute minimum COP used in any calculation. Below this floor the electrical
power estimate would become implausibly high. The default is well below any
real air-to-water heat pump operating condition; only adjust if you have an
unusual heat pump type.

---

### `mwh_magnitude_threshold`

| | |
|---|---|
| Default | `5.0` |
| Range | > 0 |

If the absolute value of a price reading from a sensor exceeds this
threshold, the integration assumes the price is in EUR/MWh and divides by
1000 to convert to EUR/kWh. Day-ahead prices in EUR/MWh typically range from
0 to 400; EUR/kWh prices are below 1 for all practical purposes.

Adjust upward (e.g., to 10 or 20) only if you operate in a market where
EUR/kWh prices regularly exceed 5 (which would be unusual).

---

### `idle_power_threshold_kw`

| | |
|---|---|
| Default | `0.1` kW |
| Range | > 0 |

Electrical power below which the heat pump is considered to be off (idle or
standby). This threshold is used for the *Heat pump plan active* binary
sensor and to exclude standby periods from calibration.

**Raise** (e.g., to 0.2–0.3 kW) for larger heat pumps that draw significant
standby power from fans or controls.

---

### `price_change_rel`

| | |
|---|---|
| Default | `0.10` (10 %) |
| Range | > 0 |

Relative price change (compared to the last optimisation run) that triggers
an immediate re-optimisation outside the normal 15-minute cycle. A value of
0.10 means a price move of more than 10 % triggers a new plan.

**Lower** (e.g., to 0.05) for more responsive re-planning when prices change
intraday. **Raise** (e.g., to 0.20) to reduce unnecessary re-computations
on volatile markets.

---

### `price_change_min_abs`

| | |
|---|---|
| Default | `0.01` EUR/kWh |
| Range | ≥ 0 |

Absolute minimum price change that can trigger re-optimisation, regardless of
the relative threshold. Prevents spurious re-planning when prices are near
zero and even a small absolute move exceeds the relative threshold.

---

### `min_running_power_kw`

| | |
|---|---|
| Default | `0.3` kW |
| Range | > 0 |

Minimum electrical power for the heat pump to be considered in a steady
heating state suitable for collecting COP and emitter calibration samples.
Points below this threshold are excluded to avoid startup transients and
partial-load measurements that may not represent steady-state COP.

**Lower** (e.g., to 0.15 kW) for small heat pumps (≤ 5 kW) that run at low
power during mild weather.

---

### `accuracy_horizon_hours`

| | |
|---|---|
| Default | `1.0` h |
| Range | > 0 |

Look-ahead horizon for the *Model accuracy* sensor (mean absolute error of
indoor temperature predictions). A shorter horizon focuses the error metric
on near-term prediction quality; a longer horizon reflects how well the model
tracks temperature over a full heating cycle.

---

## Gas boiler policy

These settings apply to the gas boiler subentry and control when the hybrid
policy recommends the gas boiler as a backup to the heat pump.

### `comfort_lookahead_hours`

| | |
|---|---|
| Default | `3.0` h |
| Range | > 0 |

How many hours ahead the heat-pump-only plan is checked for comfort breaches.
If the plan cannot restore the indoor temperature to within the comfort band
within this window, the *Gas boiler preferred* sensor switches on (when *Gas
as comfort backup* is enabled).

**Lower** (e.g., to 2 h) to give the gas boiler a shorter deadline before
being triggered. **Raise** (e.g., to 4 h) to let the heat pump try longer
before calling on the gas boiler.

---

### `comfort_tolerance_c`

| | |
|---|---|
| Default | `0.1` K |
| Range | ≥ 0 |

How far below `comfort_min` the planned indoor temperature must fall before
it is counted as a comfort breach for the gas-boiler trigger logic. A small
deadband prevents the gas boiler from being triggered by rounding noise or
tiny plan dips that do not affect perceived comfort.

**Raise** (e.g., to 0.3 K) if you find the gas boiler is triggered too
readily on minor planned dips. **Lower** to 0 for strict comfort enforcement.

---

## Zone-specific

### `window_shgc`

| | |
|---|---|
| Default | derived from glass U-value |
| Range | 0.0 – 1.0 |

Solar Heat Gain Coefficient of the glazing. When not set, the SHGC is
estimated from the configured glass U-value using the empirical relationship
`SHGC ≈ 0.87 − 0.007 × U_value` (valid for standard double and triple
glazing). Set this explicitly to the value from your window manufacturer's
datasheet to get more accurate solar gain modelling.

Typical values: standard double glazing 0.6–0.7, low-e double glazing
0.4–0.6, triple glazing 0.4–0.5, solar-control glass 0.2–0.4.
