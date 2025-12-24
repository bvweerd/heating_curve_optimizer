# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
