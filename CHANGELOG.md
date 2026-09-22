# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - Redesign

Complete redesign of the optimization core, modelled on the sibling
`battery_controller` integration's architecture. Full design rationale and
implementation notes in `docs/redesign/REDESIGN.md` and
`docs/algorithm/redesign-thermal-model.md`.

### Added
- **New thermal model** (`building_model.py`, `heatpump_model.py`,
  `thermal_optimizer.py`): a 1R1C building model with an emitter power
  curve couples the heating-curve offset to *how much heat is actually
  delivered*, not just to COP - the building is modelled the same way
  `battery_controller` models a battery (indoor temperature as state of
  charge, thermal mass as capacity, UA as round-trip loss). Backward
  induction with a real terminal value function replaces the legacy DP's
  buffer-as-payload and ad-hoc end-of-horizon penalty.
- **`select.control_mode`**: choose between `legacy` (unchanged default),
  `follow_curve` (a clean no-optimization baseline), and `optimize_v2`
  (the redesigned optimizer). Falls back to `legacy` automatically if the
  new optimizer errors on a given cycle.
- **Thermal calibration** (`calibration.py`): learns the building's real
  UA and thermal mass from operation (real indoor-temperature response
  against real electricity-meter-derived heat input), instead of holding
  them at the energy-label estimate forever. New service
  `heating_curve_optimizer.reset_thermal_calibration`.
- **PV surplus / feed-in pricing**: heat covered by PV production is now
  priced at the production-price sensor's rate rather than the
  consumption rate, when configured.
- **`climate.HeatingOptimizerClimate`** (disabled by default): native HA
  climate entity for the existing target-temperature setpoint.
- Two disabled-by-default diagnostic sensors comparing the redesigned
  optimizer's plan and cost estimate against the legacy optimizer's, so
  the new model's behaviour can be watched on real data before switching
  `control_mode`.
- `quality_scale.yaml`: an honest, evidence-based self-assessment (see
  file for the current gaps).

### Fixed
- Two number entities (`target_indoor_temp`, hysteresis) stored their
  value in a location not keyed per config entry - a second config entry
  (a second heating system) would silently share/overwrite the first
  one's setpoint.
- `requirements: ["aiohttp"]` removed from `manifest.json` - aiohttp is a
  Home Assistant core dependency, never installed standalone.
- Coverage measurement (`setup.cfg`) measured `tests/` instead of the
  integration, always reporting close to 100% regardless of what the code
  actually did.

### Changed
- `manifest.json` no longer claims a `quality_scale` tier that
  `quality_scale.yaml`'s own audit does not support yet.

## [Unreleased]

### Added
- **Heat debt optimization**: New `max_buffer_debt` configuration parameter (default: 5.0 kWh)
  - Allows optimizer to reduce heating during expensive hours and compensate during cheaper hours
  - Enables cost savings through temporal load shifting using building's thermal mass
  - Configurable via UI in advanced settings
- Buffer can now go negative (heat debt) within configurable limits
- Two new optimizer tests for negative buffer functionality

### Changed
- **BREAKING**: Buffer constraint changed from `buffer >= 0` to `buffer >= -max_buffer_debt`
- Optimizer now returns actual buffer energy evolution (kWh) instead of cumulative offset sum
- Buffer tracking in dynamic programming now uses real energy values from DP table
- Updated documentation to explain heat debt concept

### Fixed
- Buffer evolution calculation now correctly reflects thermal energy (not cumulative offsets)
- Optimizer can now produce non-zero offsets when price variations exist
- **Heat Generation Delta sensor** now correctly calculates buffer change rate instead of COP-based heat delta
  - Formula changed from `heat × (COP_optimized / COP_baseline - 1)` to `offset × heat_demand × 0.15`
  - Sensor now accurately shows how fast thermal buffer is charging/discharging (in kW)
  - Helps users understand the relationship between offset, heat demand, and buffer changes

## [1.0.2] - 2024-12-24

### Added
- Comprehensive test suite with 127 tests
- Modular sensor architecture (sensors organized by function)
- Extensive documentation (README, CLAUDE.md, mkdocs)

### Changed
- Refactored sensor structure into organized subdirectories
- Improved code maintainability and testability

## [1.0.0] - 2024-11-16

### Added
- Initial release
- Dynamic programming optimization for heating curve
- Weather forecast integration (open-meteo.com)
- Electricity price optimization
- COP calculation with outdoor temperature effects
- Solar gain and PV production forecasting
- Building thermal properties configuration
- Energy label-based heat loss calculation
- Ventilation type selection
- Configuration via UI (multi-step flow)
- 16 sensor entities for monitoring
- Binary sensor for heat demand
- Number entities for manual control
- Diagnostics and calibration features

[Unreleased]: https://github.com/bvweerd/heating_curve_optimizer/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/bvweerd/heating_curve_optimizer/compare/v1.0.0...v1.0.2
[1.0.0]: https://github.com/bvweerd/heating_curve_optimizer/releases/tag/v1.0.0
