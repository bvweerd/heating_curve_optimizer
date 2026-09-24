# CLAUDE.md - Guide for AI assistants

Home Assistant custom integration that plans the heating-curve offset of a
weather-compensated heat pump with a DP optimizer over indoor temperature.
It publishes sensors only; it never actuates hardware.

User documentation lives in `docs/` (MkDocs, built with `--strict`); keep it
in sync with behaviour changes. There are no deployments to stay compatible
with yet: remove obsolete code instead of keeping legacy paths.

## Layout

```
custom_components/heating_curve_optimizer/
  __init__.py                 setup/unload, zones, gas boiler, update listener, service
  const.py                    config keys, defaults, energy-label -> UA helpers
  building_model.py           BuildingConfig (1R1C, internal gains), EmitterConfig   [pure]
  heatpump_model.py           HeatPumpConfig.cop_at: the ONLY COP implementation    [pure]
  thermal_optimizer.py        optimize_thermal_schedule: DP + baseline + shadow price [pure]
  calibration.py              ridge fits: building (C, UA, solar factor, internal gains, COP scale
                              from gas windows), COP curve, emitter curve; quality gates; Store
  repairs.py                  fix flow: calibration "observe" -> "apply"
  coordinator_weather.py      open-meteo (30 min); radiation shifted one slot (preceding-hour mean)
  coordinator_heat.py         per zone (5 min): heat loss, window solar gain, PV, indoor temp
  coordinator_optimization.py per zone (15 min): step grid, alignment, DP, calibration, realtime
  gas_boiler_model.py / gas_boiler_coordinator.py   hybrid advice (heat pump first)
  realtime_controller.py      PV-surplus layer, only raises the offset above the plan
  helpers.py                  price parsing, resample_to_steps, solar geometry, read_power_kw
  sensor.py                   HcoSensorEntityDescription (value_fn/attrs_fn) + a few classes
  binary_sensor.py number.py climate.py diagnostics.py config_flow.py companion_integrations.py
  strings.json translations/{en,nl}.json icons.json services.yaml quality_scale.yaml
tests/                        pytest; conftest.py has make_entry()/setup_entry() for e2e tests
docs/                         index, installation, quick-start, configuration, algorithm, entities, troubleshooting, development
```

## Key behaviour

- **Main entry** (`entry.data`, edited via options flow): price sensors,
  measurement sensors, heat pump COP parameters, heating curve,
  `offset_delta_t`, `planning_window`.
- **Subentries**: `heating_zone` (first one = primary zone, keeps
  `entry.entry_id` as its id; others use `f"{entry_id}_{subentry_id}"`),
  `pv_array`, `gas_boiler` (single). Zone config = `{**main, **subentry.data}`.
- **Live setpoints**: number/climate entities write target temperature and
  hysteresis to `entry.options` (`ENTITY_MANAGED_OPTIONS`). The update
  listener then refreshes the primary zone instead of reloading;
  `HeatCalculationCoordinator.effective_config()` merges them in.
- **Step grid**: one DP step per price period, first step from now to the
  next price boundary, horizon = planning window rounded up to periods.
  All forecasts go through `resample_to_steps`.
- **Optimizer**: state `(T_in, prev_offset)`, value function on a 0.1 °C
  grid with linear interpolation, exact transitions, continuous forward
  pass. Comfort: 50 €/K²/h outside the band + 1000 € below
  `comfort_min - 1.5`. Terminal value prices stored heat above
  `comfort_min`. Baseline = offset 0 through the same model.
- **Indoor temperature fallback** is the target temperature; a configured
  but unavailable sensor raises repair issue `indoor_sensor_unavailable_*`.
- **Hybrid** (heat pump first, gas as comfort backup): gas when the plan
  is still below the band after 3 h (`heat_pump_cannot_keep_up`, any
  price unless `gas_comfort_backup` is off), or when below/dipping below the band but recovering
  (`below_comfort_band`) and gas is cheaper per kWh of heat.
- **Optimization data keys** (consumed by sensors, gas boiler, diagnostics):
  `offset, offsets, supply_temps, indoor_temps, cop, cost_eur,
  baseline_*, cost_savings_eur, shadow_price_eur_per_kwh,
  step_durations_hours, step_start_times, step_minutes, comfort_min/max,
  buffer_kwh, calibration (summary dict), model_accuracy, realtime`.
- **Calibration**: per zone mode off/observe/apply (`calibration_mode`); only
  with one zone. Windows close after 0.3 K movement; excluded on DHW, open
  window, 1 K jump, gaps. Applied fits replace UA/C/internal gains/solar
  factor (building), COP curve (heat meter) or COP scale (gas windows), and
  the emitter curve.

## Conventions

- Python 3.13+, `mypy --strict` clean, ruff (`ruff.toml`) + pyupgrade +
  codespell via pre-commit. No `# type: ignore` for HA base classes.
- Entities use `has_entity_name` + `translation_key`; never set `_attr_name`.
  Icons in `icons.json`. Unique id: `f"{zone_id}_{key}"`.
- Coordinators take `config_entry` and raise `_update_failed(key)` with the
  message in `strings.json` → `exceptions`.
- `strings.json` is the source: `translations/en.json` is an identical
  copy, `translations/nl.json` has the same keys (translated).
- New COP-dependent code must call `HeatPumpConfig.cop_at`.

## Checks before pushing

```bash
pre-commit run --all-files
mypy custom_components/heating_curve_optimizer
pytest            # coverage floor 85%
mkdocs build --strict   # when docs changed (requirements-docs.txt)
```
