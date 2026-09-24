# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - unreleased

First release of the redesigned integration. There are no earlier
deployments to migrate from.

### Optimizer
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
- Hybrid gas boiler: heat pump first; the boiler is recommended only when
  comfort is at risk and gas is cheaper per kWh of heat.
- One COP implementation everywhere; total savings also books losses.
- New sensors: planned indoor temperature, planned COP, value of stored
  heat, real-time offset, heat pump thermal energy.
- Entity names are translated (English, Dutch).
- Config flow with entity/number selectors, range and curve validation;
  unused settings (energy source sensors, curve offset, time base) removed.
- Diagnostics redact sensor IDs in the runtime config as well.

### Removed
- The legacy optimizer, buffer model, history-based calibration sensor and
  related settings, sensors and documentation.
