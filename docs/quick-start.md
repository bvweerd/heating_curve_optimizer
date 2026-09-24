# Quick start

## 1. Main settings

When you add the integration, one form asks for the settings shared by the
whole installation:

| Section | What to fill in |
|---|---|
| Electricity prices | Your consumption price sensor (required) and feed-in price sensor (optional). |
| Measurement sensors | Heat pump power (W/kW), measured supply temperature, grid import/export power. All optional. |
| Heat pump | Base COP, k-factor, outdoor coefficient, compensation factor, optionally the rated heating capacity. |
| Heating curve | The curve **as set on your heat pump**: supply temperature at the warm and the cold end, and the matching outdoor temperatures. |
| Advanced | Planning horizon (default 24 h). |

If Battery Controller or Dynamic Energy Contract Calculator is installed,
the integration offers to prefill the sensors it finds there.

See [Configuration](configuration.md) for guidance on every value.

## 2. Add a heating zone

Open the integration page and choose **Add heating zone**. A zone is one
heating circuit: floor area, energy label, ventilation, construction weight,
emitter type, windows, comfort setpoint and an indoor temperature sensor.

The first zone is the **primary zone**. It gets the full set of entities and
its target temperature and comfort band can be changed at runtime with the
number (or climate) entities. Additional zones are for separate heating
circuits; they can override the heating curve.

Without a zone the integration only shows the outdoor temperature and the
plain heating-curve supply temperature.

## 3. Check the plan

After a minute the main device shows, among others:

- **Heating curve offset**: the offset to apply now, with the full plan in
  the `offsets` attribute;
- **Planned indoor temperature**: the predicted temperature at the end of
  the current step, with the whole trajectory in `indoor_temps`;
- **Cost savings forecast**: expected savings over the horizon compared
  with running the plain curve.

## 4. Apply the offset

The integration does not control your heat pump. Use an automation that
writes the offset to your heat pump integration, for example:

```yaml
automation:
  - alias: Apply heating curve offset
    trigger:
      - platform: state
        entity_id: sensor.heating_curve_optimizer_heating_curve_offset
    condition:
      - condition: not
        conditions:
          - condition: state
            entity_id: sensor.heating_curve_optimizer_heating_curve_offset
            state: ["unknown", "unavailable"]
    action:
      - service: number.set_value
        target:
          entity_id: number.my_heat_pump_heating_curve_offset
        data:
          value: "{{ states('sensor.heating_curve_optimizer_heating_curve_offset') | int }}"
```

With grid sensors configured, use `sensor.…_real_time_heating_curve_offset`
instead: it equals the plan, raised while PV surplus is being exported.

## 5. Let it learn

With an indoor temperature sensor and a heat pump power sensor, the
integration learns the real heat loss coefficient and thermal mass of your
house. After 30 good samples (typically one to two weeks of heating) the
learned values replace the energy-label estimate. Progress is shown by the
**Thermal calibration samples** diagnostic sensor.
