# Installation

## Requirements

- Home Assistant **2025.4** or newer.
- An electricity price sensor with a **forecast** attribute. Supported
  formats, in order of preference:
    1. `net_prices_today` / `net_prices_tomorrow`
    2. `raw_today` / `raw_tomorrow` with `start`/`value` entries (Nord Pool,
       [Dynamic Energy Contract Calculator](https://github.com/bvweerd/dynamic_energy_contract_calculator))
    3. `today_hours` / `tomorrow_hours` (OMIE)
    4. `forecast_prices` or `forecast`
    5. `raw_today` / `today` lists without timestamps

    Prices in EUR/MWh are converted automatically.
- Internet access to `api.open-meteo.com` (no account needed).
- Strongly recommended: an indoor temperature sensor per heating zone.

## Marketplace (Home Assistant 2026.11+)

1. *Settings → Marketplace → Add from link* → enter
   `https://github.com/bvweerd/heating_curve_optimizer`.
2. Install **Heating Curve Optimizer**. A first install needs no restart.

Adding a repository from a link needs a GitHub connection (device sign-in,
read-only, public data only). Installed through HACS before 2026.11? Nothing
to do: the installation and its custom repository are migrated automatically.

## HACS (2026.10 and older)

1. HACS → Integrations → ⋮ → *Custom repositories* → add
   `https://github.com/bvweerd/heating_curve_optimizer` as *Integration*.
2. Install **Heating Curve Optimizer** and restart Home Assistant.

## Manual

Copy `custom_components/heating_curve_optimizer` into your
`config/custom_components` directory and restart Home Assistant.

## Add the integration

*Settings → Devices & services → Add integration → Heating Curve Optimizer.*
Continue with the [quick start](quick-start.md).

## Removal

*Settings → Devices & services → Heating Curve Optimizer → ⋮ → Delete*,
then remove the files (or uninstall through the Marketplace or HACS). Learned calibration data
is stored in `.storage/heating_curve_optimizer_<entry_id>_thermal_calibration`
and is deleted with the entry's storage cleanup; remove it by hand if needed.
