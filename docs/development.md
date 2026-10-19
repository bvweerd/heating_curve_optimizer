# Development

## Setup

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pre-commit mypy
pre-commit install
```

## Checks

```bash
pre-commit run --all-files   # pyupgrade, codespell, ruff, mypy (strict)
pytest                       # includes coverage floor
```

CI runs the same on Python 3.13 and 3.14 against the latest Home Assistant,
plus hassfest and HACS validation.

## Layout

| Module | Responsibility |
|---|---|
| `building_model.py` | 1R1C / mass-air building and emitter model (pure Python). |
| `mass_model.py` | Mass temperature observer, 5-minute history, coupling learning, measured COP check. |
| `heatpump_model.py` | The single COP implementation. |
| `thermal_optimizer.py` | DP optimizer and baseline (pure Python). |
| `calibration.py` | Building, COP and emitter fits, quality gates, residual diagnosis; persisted. |
| `repairs.py` | Fix flow that switches a zone's calibration to *Apply*. |
| `coordinator_weather.py` / `coordinator_heat.py` / `coordinator_optimization.py` | Data pipeline. |
| `gas_boiler_model.py` / `gas_boiler_coordinator.py` | Hybrid advice. |
| `realtime_controller.py` | PV-surplus layer. |
| `helpers.py` | Price parsing, time alignment, solar geometry, sensor reading. |
| `sensor.py` | Entity descriptions over coordinator data. |
| `config_flow.py` | Main flow, options flow and subentry flows. |

`scripts/validate_diagnostics.py` replays the validation history of a
diagnostics download (see [Troubleshooting](troubleshooting.md#validating-the-models-after-a-few-days)).

## Tests

- `tests/test_thermal_optimizer.py` checks energy conservation, comfort,
  horizon-end behaviour, slow drift and a brute-force comparison.
- `tests/test_init.py` sets up the whole integration against a mocked
  open-meteo and price sensor.
- Translations: `strings.json` is the source; `translations/en.json` is an
  identical copy and `translations/nl.json` has the same keys.
