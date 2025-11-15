# Quick Start Guide

Get your heating optimization running in 15 minutes!

## Prerequisites Checklist

Before starting, ensure you have:

- [x] Home Assistant 2023.1+ installed
- [x] HACS installed
- [x] Basic knowledge of your building (area, construction quality)
- [x] Electricity price sensor (or know your fixed price)
- [x] Power consumption sensor

## Step-by-Step Setup

### 1. Install the Integration

=== "Via HACS (Recommended)"
    1. Open **HACS** in Home Assistant
    2. Go to **Integrations**
    3. Click **⋮** (menu) → **Custom repositories**
    4. Add `https://github.com/bvweerd/heating_curve_optimizer`
    5. Category: **Integration**
    6. Click **Download**
    7. **Restart** Home Assistant

=== "Manual"
    1. Download from [GitHub Releases](https://github.com/bvweerd/heating_curve_optimizer/releases)
    2. Extract to `custom_components/heating_curve_optimizer/`
    3. **Restart** Home Assistant

### 2. Add the Integration

1. Navigate to **Settings** → **Devices & Services**
2. Click **+ ADD INTEGRATION**
3. Search for "**Heating Curve Optimizer**"
4. Click to start configuration wizard

### 3. Configure Building Parameters

#### Basic Settings

Fill in your building characteristics:

| Parameter | Example Value | How to Find |
|-----------|---------------|-------------|
| **Area** | 150 m² | Floor plan or property documents |
| **Energy Label** | C | Energy Performance Certificate (EPC) |
| **Glass East** | 4 m² | Measure windows facing ±45° of east |
| **Glass West** | 4 m² | Measure windows facing ±45° of west |
| **Glass South** | 10 m² | Measure windows facing ±45° of south |
| **Glass U-value** | 1.2 W/m²K | Window specifications (1.2 = standard double glazing) |

!!! tip "Energy Label"
    In Netherlands: Check [ep-online.nl](https://www.ep-online.nl/)

    No label? Use these estimates:

    - New home (< 10 years): **A** or **B**
    - Renovated (insulation upgraded): **B** or **C**
    - Average home (20-40 years): **C** or **D**
    - Older home (> 40 years, no upgrades): **E** or **F**

#### Heat Pump Settings

| Parameter | Typical Value | Notes |
|-----------|---------------|-------|
| **Base COP** | 3.5 - 4.5 | Check datasheet at A7/W35 condition |
| **K-Factor** | 0.025 - 0.035 | Use 0.03 if unsure |
| **COP Compensation** | 0.85 - 0.95 | Use 0.90 if unsure |

!!! info "Where to Find COP"
    Your heat pump datasheet will list COP at various conditions:

    - Look for **A7/W35** rating (7°C air, 35°C water)
    - Example: "COP 4.2 at A7/W35" → Base COP = **4.2**

### 4. Select Sensors

#### Consumption Sensor

Select your electricity consumption sensor:

- **Type**: Power or Energy sensor
- **Units**: W or kW
- **Example**: `sensor.power_consumption`

Common sources:

- P1 smart meter integration
- Shelly EM
- Energy monitor device
- Utility integration (e.g., HomeWizard)

#### Production Sensor (Optional)

If you have solar panels:

- **Select**: Solar production sensor
- **Units**: W or kW
- **Example**: `sensor.solar_power`

If no solar, leave empty.

### 5. Configure Prices

#### Consumption Price

=== "Dynamic Pricing"
    Select your dynamic price sensor:

    - Nordpool integration: `sensor.nordpool_kwh_nl_eur_3_10_0`
    - ENTSO-E: `sensor.entsoe_price`
    - Energy tariffs: Your configured price sensor

=== "Fixed Pricing"
    Leave sensor empty, the integration will use current price as fixed value.

#### Production Price (Optional)

If you have solar and receive feed-in compensation:

- **Select**: Production price sensor (if dynamic)
- **Or leave empty**: Integration will use consumption price - typical margin

### 6. Finish Configuration

Click **Submit** to complete setup.

You should see:

✅ "Integration configured successfully"

## Verify Installation

### Check Sensors

Navigate to **Developer Tools** → **States**

Search for: `sensor.heating_curve_optimizer`

You should see **17 sensors**:

#### Key Sensors

| Sensor | Expected State | Notes |
|--------|----------------|-------|
| `outdoor_temperature` | Numeric (°C) | Should update every 5 min |
| `heat_loss` | Numeric (kW) | Current heat loss |
| `net_heat_loss` | Numeric (kW) | After solar gain |
| `heating_curve_offset` | -4 to +4 | **Main optimization output** |
| `current_electricity_price` | Numeric (€/kWh) | Current price |

!!! warning "Sensor Unavailable?"
    If sensors show "unavailable":

    1. Wait 5 minutes for first update
    2. Check integration logs: Settings → System → Logs
    3. See [Troubleshooting Guide](reference/troubleshooting.md)

### Check Number Entities

You should see **5 number entities** for manual control:

- `number.heating_curve_optimizer_offset`
- `number.heating_curve_optimizer_min_supply_temp`
- `number.heating_curve_optimizer_max_supply_temp`
- `number.heating_curve_optimizer_min_outdoor_temp`
- `number.heating_curve_optimizer_max_outdoor_temp`

## Connect to Your Heating System

The integration outputs the optimal offset, but you need to **apply it to your heating system**.

### Option 1: Automation (Recommended)

Create an automation to apply the offset:

```yaml
automation:
  - alias: "Apply Heating Curve Offset"
    trigger:
      - platform: state
        entity_id: sensor.heating_curve_optimizer_heating_curve_offset
    action:
      - service: climate.set_temperature
        target:
          entity_id: climate.your_heat_pump
        data:
          temperature: >
            {{ states('sensor.calculated_supply_temperature') }}
```

Or if your heat pump supports offset directly:

```yaml
automation:
  - alias: "Apply Heating Curve Offset"
    trigger:
      - platform: state
        entity_id: sensor.heating_curve_optimizer_heating_curve_offset
    action:
      - service: number.set_value
        target:
          entity_id: number.heat_pump_offset
        data:
          value: >
            {{ states('sensor.heating_curve_optimizer_heating_curve_offset') }}
```

### Option 2: Node-RED

Use Node-RED for more complex logic:

1. **Trigger**: State change of `heating_curve_offset` sensor
2. **Function**: Process offset value
3. **Action**: Call heat pump service

### Option 3: Manual Monitoring

View the recommended offset in Lovelace:

```yaml
type: entities
entities:
  - entity: sensor.heating_curve_optimizer_heating_curve_offset
  - entity: sensor.heating_curve_optimizer_optimized_supply_temperature
  - entity: sensor.heating_curve_optimizer_heat_buffer
```

Manually adjust your heat pump offset to match the recommendation.

## Dashboard Card (Optional)

Create a nice dashboard view:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Heating Curve Optimizer
    entities:
      - entity: sensor.heating_curve_optimizer_heating_curve_offset
        name: Optimal Offset
      - entity: sensor.heating_curve_optimizer_heat_buffer
        name: Thermal Buffer
      - entity: sensor.heating_curve_optimizer_current_electricity_price
        name: Current Price

  - type: history-graph
    title: Offset History
    hours_to_show: 24
    entities:
      - entity: sensor.heating_curve_optimizer_heating_curve_offset

  - type: entities
    title: Manual Controls
    entities:
      - entity: number.heating_curve_optimizer_offset
      - entity: number.heating_curve_optimizer_min_supply_temp
      - entity: number.heating_curve_optimizer_max_supply_temp
```

## Initial Monitoring (First 24 Hours)

### What to Watch

1. **Offset values**: Should range from -4 to +4, changing gradually
2. **Buffer accumulation**: Should increase during sunny/warm periods
3. **Price correlation**: Offset should increase during low-price periods
4. **No errors**: Check logs for warnings/errors

### Expected Behavior

**Morning (low prices)**:

- Offset: +1 to +3 (pre-heating)
- Buffer: Accumulating

**Midday (high prices)**:

- Offset: -2 to 0 (reduced heating)
- Buffer: Being used

**Evening**:

- Offset: 0 to +2 (normal heating)
- Buffer: Depleting

## Fine-Tuning

After a few days, consider adjusting:

### 1. COP Parameters

Monitor your heat pump's actual efficiency:

- If optimizer seems too conservative: Decrease k-factor
- If optimizer is aggressive: Increase k-factor

### 2. Temperature Limits

Adjust comfort bounds:

```yaml
# Via number entities or configuration
Min Supply Temperature: 30°C  # Lower = more optimization flexibility
Max Supply Temperature: 50°C  # Higher = more heat capacity
```

### 3. Planning Window

- **6 hours** (default): Good balance
- **12 hours**: More pre-heating opportunities
- **24 hours**: Maximum optimization (higher computation)

## Common Questions

??? question "Offset not changing?"
    **Possible causes**:

    - Price sensor not providing forecast → Check price sensor attributes
    - All prices similar → Optimization limited with flat pricing
    - Very cold weather → Limited flexibility at max capacity

    **Check**: `sensor.heating_curve_optimizer_diagnostics` for details

??? question "Buffer always zero?"
    **Possible causes**:

    - No solar gain configured → Add window areas
    - Winter period → Lower solar radiation
    - Very cold → Heat loss exceeds solar gain

    **Expected**: Buffer only accumulates when solar gain exceeds heat loss

??? question "Savings not as expected?"
    **Factors affecting savings**:

    - Price volatility: Higher volatility = more savings
    - Weather: Shoulder seasons have best optimization potential
    - Building thermal mass: Better insulation = better buffering

    **Realistic expectations**:

    - Dynamic pricing: 10-30% savings
    - Fixed pricing: 2-8% savings (COP optimization only)

## Next Steps

Now that you're running:

- 📖 [Understanding the Algorithm](algorithm/overview.md) - Learn how it works
- 📊 [View Examples](examples/price-optimization.md) - See real-world scenarios
- 🔧 [Reference Guide](reference/sensors.md) - Detailed sensor information
- ❓ [Troubleshooting](reference/troubleshooting.md) - Solve common issues

---

**Congratulations! Your heating optimization is now active.** 🎉

Monitor your energy costs over the next few weeks to see the savings accumulate!
