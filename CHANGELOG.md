# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - unreleased

First release of the redesigned integration. There are no earlier
deployments to migrate from.

### Optimizer
- Heat pump *Switching* setting. *Own thermostat* (default): the model
  stops the heat pump at the target like its room thermostat does, and the
  plan only sets the offset. *Follows the plan*: the plan also switches the
  pump off, with a minimum run time and pause (default 60 minutes each)
  carried in the DP state; the *Plan active* advice holds for those
  minimums.
- Away periods: an optional home schedule (schedule helper) per zone lowers
  the comfort floor by the away setback while nobody is home. The plan
  knows the return time and reheats in the cheapest hours before it; gas
  advice treats the planned dip as intended.
- With *Follows the plan*, pre-heating can use the whole comfort band (no
  thermostat cut-off at target + 0.3 °C), and the plan can switch the heat
  pump off just below the band: the comfort penalty decides, so a room
  0.05 °C too cold is no longer heated at the day's peak price just before
  the sun comes in. The baseline keeps a room thermostat at the target, so
  savings are not inflated.
- `pump_on` is the plan's on/off command with *Follows the plan*, and
  "the thermostat lets it deliver heat" with *Own thermostat*.
- The PV-surplus adjustment steps back down while the heat pump is idle
  and nothing is exported, instead of waiting for the next start.
- The PV-surplus adjustment no longer flaps: each change holds for 15
  minutes, and the surplus is judged without the heat pump's own draw, so
  the import caused by running on the surplus does not undo the increase.
- Home battery power sensors (any number, positive = charging, offered
  from Battery Controller during setup): battery discharge counts as grid
  import for the PV-surplus layer, so a zero-grid battery powering the heat
  pump is not mistaken for surplus. The battery gets the surplus first.
- The plan can switch the heat pump off anywhere inside the comfort band
  (was: only at or above target), so it coasts through expensive hours
  instead of staying on all day. Run advice compares the plan's
  electrical power with the idle threshold.
- The plan is re-optimized every 15 minutes again when grid sensors are
  configured: the 60 s PV-surplus loop kept pushing the scheduled run
  back, so the plan (and the heat buffer) froze until a large price or
  indoor temperature change.
- Backward-induction DP over indoor temperature with a linearly
  interpolated value function and a continuous-state forward pass (slow
  temperature drift is no longer rounded away).
- DP steps follow the price periods (15 or 60 min), the first step ending
  at the next price boundary; weather, solar and PV forecasts are aligned
  onto the same grid. The planning horizon (default 24 h) is honoured.
- Internal heat gains, humidity-dependent defrost losses, configurable
  heat pump capacity.
- Baseline (plain curve) simulated with the same physics; savings are
  corrected for the value of heat left in the building.

### Integration
- The fallback indoor temperature is the target temperature (was a fixed
  21 °C that made the optimizer believe the house was always too warm); an
  unavailable indoor sensor raises a repair issue.
- Target temperature and comfort band changes reach the optimizer
  immediately, without a reload.
- Re-optimization on price changes works for zero and negative prices.
- Radiation forecast aligned with the hour it describes; window solar gain
  uses sun position and plane-of-array irradiance.
- Thermal calibration integrates heat input over a whole observation window.
- Real-time PV-surplus layer only raises the offset above the plan.
- Hybrid gas boiler: heat pump first, gas as comfort backup. The boiler is
  recommended when the heat pump cannot restore comfort within 3 hours
  (regardless of price; setting "Gas as comfort backup", default on), or
  when comfort is at risk and gas is cheaper.
- One COP implementation everywhere; total savings also books losses.
- New sensors: planned indoor temperature, planned COP, value of stored
  heat, real-time offset, heat pump thermal energy.
- Entity names are translated (English, Dutch).
- Config flow with entity/number selectors, range and curve validation;
  unused settings (energy source sensors, curve offset, time base) removed.
- Diagnostics redact sensor IDs in the runtime config as well.
- A *Heat pump* device groups plan-active advice, expected COP, cost per
  kWh, a "cheaper" flag and the measured thermal sensors. The gas boiler
  gets separate run advice (*Advised*) and price comparison (*Cheaper*)
  binary sensors. Both devices have translated names. Savings and expected indoor
  temperature sensors are disabled diagnostics by default.
- After a restart the first plan and gas comparison wait until Home
  Assistant has started and their source sensors (prices, indoor
  temperature) report, instead of logging errors and raising repair issues
  for sensors that are still loading. An indoor change of 0.5 K or more
  re-optimizes right away.

### Calibration
- Learns heat loss, thermal mass, solar factor and internal gains; the COP
  curve (heat meter) or COP level (gas meter windows); and the emitter
  curve. Ridge-regularised towards the label estimates, with quality gates.
- Optional thermal power, tap-water and window/door sensors; excluded
  windows are counted and shown.
- Modes off / observe / apply per zone; a repair notification asks before
  a ready fit is used.
- Calibration sensor with readable results (effective energy label, time
  constant, cooling rate) and a model-accuracy sensor comparing the learned
  and label models, including a two-mass diagnosis.

### Removed
- The legacy optimizer, buffer model, history-based calibration sensor and
  related settings, sensors and documentation.
