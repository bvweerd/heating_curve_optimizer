# Heat Pump Configuration Profiles

This section contains optimized configuration profiles for specific heat pump models. These profiles provide tested parameter values to help you get started quickly.

## Available Profiles

### LG Heat Pumps

- **[LG HM091MR.U44 Monoblock](LG-HM091MR-U44.md)** - 9kW R32 Monoblock
  - COP: 4.18 @ A7/W35
  - SCOP: 4.45
  - Recommended k_factor: 0.030
  - Recommended base_cop: 4.2

## Using a Profile

Each profile includes:

1. **Recommended parameters** - Tested values for the optimizer
2. **Calibration guide** - Step-by-step instructions to fine-tune
3. **Common issues** - Troubleshooting specific to the model
4. **Technical background** - Understanding the calculations

## Contributing a Profile

Have you calibrated the optimizer for your heat pump? Help others by contributing a profile:

1. Copy an existing profile as a template
2. Add your heat pump specifications
3. Include your calibrated parameters
4. Document your calibration process
5. Submit a pull request

### Profile Template

Your profile should include:

```markdown
# [Manufacturer] [Model] - Optimal Settings

## Overview
- Model name and specifications
- COP ratings
- Operating range
- Refrigerant type

## Recommended Configuration
- base_cop: [value]
- k_factor: [value]
- outdoor_temp_coefficient: [value]
- cop_compensation_factor: [value]

## Calibration Guide
Step-by-step instructions for users

## Common Issues
Model-specific troubleshooting

## Technical Background
COP formula and validation
```

## General Guidelines

If your heat pump isn't listed, use these general guidelines:

### Modern Inverter Heat Pumps (2020+)

| Parameter | Typical Range | Starting Value |
|-----------|---------------|----------------|
| base_cop | 3.5 - 5.0 | 4.0 |
| k_factor | 0.025 - 0.040 | 0.030 |
| outdoor_temp_coefficient | 0.06 - 0.10 | 0.08 |
| cop_compensation_factor | 0.85 - 0.95 | 0.90 |

### Older Heat Pumps (before 2020)

| Parameter | Typical Range | Starting Value |
|-----------|---------------|----------------|
| base_cop | 3.0 - 4.0 | 3.5 |
| k_factor | 0.040 - 0.060 | 0.045 |
| outdoor_temp_coefficient | 0.06 - 0.10 | 0.08 |
| cop_compensation_factor | 0.80 - 0.90 | 0.85 |

### By Refrigerant Type

**R32 (modern)**: Generally better high-temperature performance
- Lower k_factor (0.025 - 0.035)
- Higher base_cop possible

**R410A (common)**: Balanced performance
- Medium k_factor (0.030 - 0.045)
- Standard base_cop

**R407C (older)**: Lower high-temperature efficiency
- Higher k_factor (0.040 - 0.055)
- Lower base_cop

### By Type

**Monoblock**: All-in-one outdoor unit
- Fewer losses → higher cop_compensation_factor (0.90-0.95)
- Simpler installation

**Split System**: Separate indoor/outdoor units
- Refrigerant line losses → lower cop_compensation_factor (0.85-0.90)
- More flexible installation

## Finding Your Heat Pump Specifications

### 1. Manufacturer Datasheet

Look for these key values:
- **COP @ A7/W35**: This becomes your `base_cop`
- **COP @ A7/W55**: Use to calculate `k_factor`
- **SCOP**: Seasonal average, sometimes more accurate for year-round use

**Calculating k_factor from datasheet**:
```
k_factor = (COP_35 - COP_55) / (55 - 35)

Example:
COP @ 35°C = 4.2
COP @ 55°C = 3.4
k_factor = (4.2 - 3.4) / 20 = 0.04
```

### 2. Energy Label

EU energy labels provide:
- **A+++/A++/A+ @ 35°C**: Excellent high COP (4.5+)
- **A+++/A++/A+ @ 55°C**: Good high-temperature performance (lower k_factor)

### 3. SCOP Rating

The Seasonal COP is often more realistic for annual performance:
- Use SCOP as your `base_cop` for better calibration
- Adjust `cop_compensation_factor` closer to 1.0 (0.95)

### 4. Real-World Testing

The most accurate method:

1. **Measure over 24 hours**:
   - Thermal output (from heat meter or integration)
   - Electrical consumption (from energy meter)

2. **Calculate actual COP**:
   ```
   COP = Thermal energy (kWh) / Electrical energy (kWh)
   ```

3. **Compare with optimizer prediction**:
   - Check `sensor.heating_curve_optimizer_quadratic_cop`
   - Adjust `cop_compensation_factor` accordingly

## Heat Pump Manufacturer Resources

### Popular Manufacturers

- **Daikin**: [Altherma Series](https://www.daikin.eu/)
- **Mitsubishi**: [Ecodan Series](https://www.mitsubishielectric.com/)
- **LG**: [Therma V Series](https://www.lg.com/heating)
- **Panasonic**: [Aquarea Series](https://www.panasonic.com/aquarea)
- **Vaillant**: [aroTHERM Series](https://www.vaillant.com/)
- **Nibe**: [F Series](https://www.nibe.eu/)
- **Samsung**: [EHS Series](https://www.samsung.com/)

### Finding Technical Data

Most manufacturers provide:
1. **Product datasheets** - Technical specifications
2. **Installation manuals** - Operating ranges
3. **Performance curves** - COP vs temperature graphs
4. **Energy labels** - EU regulation ratings

## Need Help?

Can't find your heat pump profile or having trouble calibrating?

- 📖 **[Configuration Guide](../configuration.md)** - General setup instructions
- 🔧 **[Calibration Guide](../calibration.md)** - Detailed calibration process
- 💬 **[Community Discussions](https://github.com/bvweerd/heating_curve_optimizer/discussions)** - Ask for help
- 🐛 **[Report Issues](https://github.com/bvweerd/heating_curve_optimizer/issues)** - Something not working?

---

**Contribute your profile**: Help others with the same heat pump model by sharing your calibrated settings!
