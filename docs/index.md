# Heating Curve Optimizer

A Home Assistant integration that plans the **heating curve offset** of a
weather-compensated heat pump for the next 24 hours, so the house is heated
when electricity is cheap and the heat pump is efficient, while the indoor
temperature stays inside a comfort band you choose.

!!! warning "Experimental"
    The integration only publishes sensors. It never controls your heat
    pump by itself: you apply the planned offset with your own automation.
    Watch the behaviour of your installation closely in the first weeks.

## What it does

Every 15 minutes (and whenever the price or a setpoint changes) it:

1. reads the weather forecast from [open-meteo.com](https://open-meteo.com)
   and your electricity price forecast;
2. models your house as a thermal mass that loses heat to the outside and
   gains heat from the heat pump, the sun through the windows and internal
   gains (people, appliances);
3. searches, with dynamic programming, for the sequence of offsets
   (−4 … +4 °C) that minimises the electricity cost while keeping the
   indoor temperature in the comfort band;
4. publishes the first offset as `sensor.…_heating_curve_offset`, together
   with the planned supply temperature, the predicted indoor temperature,
   the expected savings and diagnostics.

Typical results: pre-heating a few tenths of a degree during cheap hours and
coasting through expensive hours; lower supply temperatures (higher COP)
whenever the thermal mass allows it.

## Main features

- Physical 1R1C building model with a continuous-state DP optimizer.
- Planning steps aligned with the price periods (15 or 60 minutes).
- COP model with outdoor/supply temperature dependency, defrost losses and
  a Carnot limit.
- Solar gain through windows and PV production forecast using solar
  geometry (plane-of-array irradiance).
- Calibration of the building, COP curve and emitters from your own
  measurements, with a status and accuracy sensor to follow the results.
- Optional real-time PV-surplus layer and hybrid gas-boiler advice
  (heat pump first).
- Multiple heating zones (heating circuits).

## Where to go next

- [Installation](installation.md)
- [Quick start](quick-start.md): from installation to an automation
- [Configuration](configuration.md): every setting explained
- [How it works](algorithm.md): the model and the optimizer
- [Entities](entities.md): every sensor and its attributes
- [Troubleshooting](troubleshooting.md)
