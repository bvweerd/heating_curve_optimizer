# Configuration Guide

This guide explains all configuration options for the Heating Curve Optimizer integration.

## Configuration Flow

The integration uses a multi-step configuration wizard:

```mermaid
graph LR
    A[Start] --> B[Basic Settings]
    B --> C[Source Selection]
    C --> D[Price Settings]
    D --> E[Complete]

    style A fill:#4caf50,stroke:#333,stroke-width:2px
    style E fill:#4caf50,stroke:#333,stroke-width:2px
```

## Step 1: Basic Settings

### Building Parameters

#### **Area (m²)**
- **Description**: Total heated floor area of your home
- **Range**: 50 - 500 m²
- **Default**: 150 m²
- **Impact**: Directly affects heat loss calculation

!!! example
    A typical Dutch terraced house: 120-150 m²
    Detached house: 180-250 m²

#### **Energy Label**
- **Description**: Building energy efficiency rating
- **Options**: A+++, A++, A+, A, B, C, D, E, F, G
- **Default**: C
- **Impact**: Determines U-value (thermal transmittance)

Energy label to U-value mapping:

| Label | U-value (W/m²K) | Insulation Quality |
|-------|-----------------|-------------------|
| A+++ | 0.18 | Passive house standard |
| A++ | 0.25 | Excellent insulation |
| A+ | 0.35 | Very good insulation |
| A | 0.45 | Good insulation |
| B | 0.60 | Above average |
| C | 0.80 | Average (default) |
| D | 1.00 | Below average |
| E | 1.40 | Poor insulation |
| F | 1.80 | Very poor |
| G | 2.50 | Minimal insulation |

!!! tip "Finding Your Energy Label"
    Check your property's Energy Performance Certificate (EPC). In the Netherlands, this is called the "Energielabel". You can find it at [ep-online.nl](https://www.ep-online.nl/).

### Window Configuration

#### **Glass East (m²)**
- **Description**: Total window area facing east (±45°)
- **Range**: 0 - 50 m²
- **Default**: 5 m²
- **Impact**: Morning solar gain

#### **Glass West (m²)**
- **Description**: Total window area facing west (±45°)
- **Range**: 0 - 50 m²
- **Default**: 5 m²
- **Impact**: Evening solar gain

#### **Glass South (m²)**
- **Description**: Total window area facing south (±45°)
- **Range**: 0 - 50 m²
- **Default**: 10 m²
- **Impact**: Peak solar gain (most important)

#### **Glass U-value (W/m²K)**
- **Description**: Thermal transmittance of windows
- **Range**: 0.5 - 3.0
- **Default**: 1.2
- **Impact**: Heat loss through windows

Common window U-values:

| Window Type | U-value | Notes |
|-------------|---------|-------|
| Triple glazing, argon fill | 0.6 - 0.8 | Best performance |
| Double glazing, HR++ | 1.0 - 1.2 | Modern standard |
| Double glazing, HR+ | 1.6 - 2.0 | Older double glazing |
| Single glazing | 5.0 - 6.0 | Very poor |

!!! warning "Accurate Measurements Matter"
    Measure window dimensions (width × height) and sum by orientation. Include patio doors and skylights.

### Heat Pump Parameters

#### **Base COP**
- **Description**: Heat pump COP at 35°C supply temperature and 7°C outdoor temperature
- **Range**: 2.0 - 6.0
- **Default**: 3.5
- **Impact**: Baseline efficiency calculation

!!! info "Finding Base COP"
    Check your heat pump datasheet for COP at A7/W35 (7°C outdoor, 35°C water). This is a standardized test condition.

#### **K-Factor**
- **Description**: COP degradation per °C supply temperature increase
- **Range**: 0.01 - 0.10
- **Default**: 0.03
- **Impact**: How much COP drops when supply temp rises

Heat pump type guidelines:

| Type | K-Factor | Notes |
|------|----------|-------|
| Ground source | 0.02 - 0.025 | More stable |
| Air-to-water (inverter) | 0.025 - 0.035 | Good modulation |
| Air-to-water (on/off) | 0.035 - 0.045 | Less efficient at high temps |

!!! tip "Calibrating K-Factor"
    Monitor your heat pump's actual COP at different supply temperatures and adjust k-factor to match reality.

#### **COP Compensation Factor**
- **Description**: Multiplier to adjust theoretical COP to real-world system efficiency
- **Range**: 0.5 - 1.2
- **Default**: 0.9
- **Impact**: Accounts for distribution losses, defrost cycles, auxiliary pumps

!!! example
    Theoretical COP = 4.0
    Real system COP = 3.6 (measured)
    Compensation factor = 3.6 / 4.0 = **0.9**

### Advanced Settings

#### **Planning Window (hours)**
- **Description**: How far ahead to optimize
- **Range**: 2 - 24 hours
- **Default**: 6 hours
- **Impact**: Longer window = better optimization but more computation

#### **Time Base (minutes)**
- **Description**: Optimization time step size
- **Range**: 15 - 120 minutes
- **Default**: 60 minutes
- **Impact**: Smaller steps = finer control but more computation

!!! warning "Performance Consideration"
    Planning window of 24 hours with 15-minute time base creates 96 time steps, which may be computationally intensive.

## Step 2: Source Selection

Select the sensors that provide power consumption and production data.

### **Consumption Sensor**
- **Required**: Yes
- **Device Class**: `power` or `energy`
- **Unit**: W or kW
- **Description**: Your home's total electricity consumption

!!! example "Typical Sources"
    - Smart meter sensor: `sensor.power_consumption`
    - Energy monitor: `sensor.house_power`
    - Shelly EM: `sensor.shellyem_power`

### **Production Sensor**
- **Required**: No (but recommended if you have solar panels)
- **Device Class**: `power` or `energy`
- **Unit**: W or kW
- **Description**: Solar panel or other electricity production

!!! tip "Solar Production"
    If you have solar panels, **definitely** configure production sensor. This enables:
    - Solar gain buffering
    - Net price calculation (consumption price - production price)
    - Optimized heating during peak production

## Step 3: Price Settings

Configure electricity price sensors for optimization.

### **Consumption Price Sensor**
- **Required**: Yes (or configure fixed price)
- **Unit**: €/kWh or your currency
- **Description**: Variable electricity consumption price

#### Supported Price Sensor Formats

The integration supports multiple price sensor attribute formats:

=== "Format 1: raw_today / raw_tomorrow"
    ```yaml
    attributes:
      raw_today:
        - hour: "2025-11-15T00:00:00+01:00"
          price: 0.23
        - hour: "2025-11-15T01:00:00+01:00"
          price: 0.21
        ...
      raw_tomorrow:
        - hour: "2025-11-16T00:00:00+01:00"
          price: 0.25
        ...
    ```

=== "Format 2: forecast_prices"
    ```yaml
    attributes:
      forecast_prices:
        - datetime: "2025-11-15T00:00:00+01:00"
          price: 0.23
        - datetime: "2025-11-15T01:00:00+01:00"
          price: 0.21
        ...
    ```

=== "Format 3: net_prices_today / net_prices_tomorrow"
    ```yaml
    attributes:
      net_prices_today:
        - 0.23
        - 0.21
        - 0.19
        ...
      net_prices_tomorrow:
        - 0.25
        - 0.24
        ...
    ```

!!! example "Popular Integrations"
    - **Nordpool**: Uses `raw_today` / `raw_tomorrow`
    - **ENTSO-E**: Uses `forecast_prices`
    - **Energy Tariffs**: Uses custom formats

### **Production Price Sensor**
- **Required**: No
- **Unit**: €/kWh
- **Description**: Electricity sell-back price (feed-in tariff)

!!! info "Why Production Price?"
    When you have solar production, the **effective** cost of electricity is:

    \\[ \text{Net Cost} = \text{Consumption Price} - \text{Production Price} \\]

    During peak production, net cost can be negative, making heating essentially free!

### **Fixed Price Mode**

If you don't have a variable price sensor:

1. Leave price sensors empty
2. Integration uses current sensor state as fixed price
3. Optimization still works but focuses on COP efficiency rather than price timing

!!! warning "Limited Optimization"
    Fixed price mode provides minimal cost savings. Variable pricing (e.g., dynamic contracts) unlocks the full potential.

## Updating Configuration

After initial setup, you can modify settings:

1. **Navigate** to Settings → Devices & Services
2. **Find** "Heating Curve Optimizer"
3. **Click** "Configure"
4. **Modify** parameters
5. **Save**

Changes take effect immediately (within one update cycle).

## Configuration Examples

### Example 1: Well-Insulated House with Solar

```yaml
Area: 150 m²
Energy Label: A+
Glass East: 3 m²
Glass West: 3 m²
Glass South: 12 m²
Glass U-value: 0.8 W/m²K

Base COP: 4.2
K-Factor: 0.025
COP Compensation: 0.92

Consumption Sensor: sensor.power_consumption
Production Sensor: sensor.solar_production
Consumption Price: sensor.nordpool_kwh_nl_eur_3_10_0
Production Price: sensor.feed_in_tariff
```

### Example 2: Average House, No Solar

```yaml
Area: 120 m²
Energy Label: C
Glass East: 5 m²
Glass West: 5 m²
Glass South: 8 m²
Glass U-value: 1.2 W/m²K

Base COP: 3.5
K-Factor: 0.03
COP Compensation: 0.9

Consumption Sensor: sensor.power_consumption
Production Sensor: (none)
Consumption Price: sensor.electricity_price
Production Price: (none)
```

### Example 3: Poorly Insulated House

```yaml
Area: 180 m²
Energy Label: E
Glass East: 6 m²
Glass West: 6 m²
Glass South: 10 m²
Glass U-value: 2.0 W/m²K

Base COP: 3.0
K-Factor: 0.035
COP Compensation: 0.85

Consumption Sensor: sensor.power_consumption
Production Sensor: (none)
Consumption Price: 0.30 (fixed)
Production Price: (none)
```

## Validation

The integration validates your configuration:

- ✓ Area must be reasonable (50-500 m²)
- ✓ Energy label must be valid
- ✓ COP parameters must be physically plausible
- ✓ Selected sensors must exist and have valid states

If validation fails, you'll see an error message explaining the issue.

---

**Next**: [Quick Start Guide](quick-start.md) - Start optimizing your heating!
