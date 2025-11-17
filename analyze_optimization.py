#!/usr/bin/env python3
"""Analyze why heating curve offset optimization returns 0.

This script calculates the cost difference between different offsets
to understand why the optimizer chooses offset 0.
"""

# Configuration from diagnostics
k_factor = 0.028  # From options
base_cop_35 = 4.28  # From options
outdoor_temp_coefficient = 0.06  # From options
cop_compensation_factor = 0.98  # From options
DEFAULT_COP_AT_35 = 4.2  # From const.py (used in optimizer)

# Current conditions
base_supply_temp = 26.2  # °C
outdoor_temp = 3.8  # °C
demand_kw = 1.471  # kW (first hour)

# Prices
price_high = 0.2474571  # EUR/kWh
price_low = 0.2192036  # EUR/kWh
price_diff = price_high - price_low

print("=" * 70)
print("HEATING CURVE OPTIMIZER ANALYSIS")
print("=" * 70)
print()
print("Configuration:")
print(f"  k_factor: {k_factor}")
print(f"  base_cop (config): {base_cop_35}")
print(f"  DEFAULT_COP_AT_35 (used in optimizer): {DEFAULT_COP_AT_35}")
print(f"  outdoor_temp_coefficient: {outdoor_temp_coefficient}")
print(f"  cop_compensation_factor: {cop_compensation_factor}")
print()
print("Current conditions:")
print(f"  Base supply temperature: {base_supply_temp}°C")
print(f"  Outdoor temperature: {outdoor_temp}°C")
print(f"  Heat demand: {demand_kw} kW")
print()
print("Price range:")
print(f"  Highest price: €{price_high:.4f}/kWh")
print(f"  Lowest price: €{price_low:.4f}/kWh")
print(f"  Difference: €{price_diff:.4f}/kWh ({price_diff/price_low*100:.1f}%)")
print()
print("=" * 70)


def calculate_cop(supply_temp, outdoor_temp, k_factor, outdoor_coeff, comp_factor):
    """Calculate COP using the optimizer's formula."""
    cop = (
        DEFAULT_COP_AT_35
        + outdoor_coeff * outdoor_temp
        - k_factor * (supply_temp - 35)
    ) * comp_factor
    return max(0.5, cop)


def calculate_cost(demand, price, cop):
    """Calculate electricity cost for given demand, price, and COP."""
    return demand * price / cop if cop > 0 else demand * price * 10


print("COP CALCULATION FOR DIFFERENT OFFSETS:")
print("-" * 70)

offsets = [-4, -3, -2, -1, 0, 1, 2, 3, 4]
cops = {}
costs_high = {}
costs_low = {}

for offset in offsets:
    supply_temp = base_supply_temp + offset
    cop = calculate_cop(
        supply_temp,
        outdoor_temp,
        k_factor,
        outdoor_temp_coefficient,
        cop_compensation_factor,
    )
    cops[offset] = cop

    cost_high = calculate_cost(demand_kw, price_high, cop)
    cost_low = calculate_cost(demand_kw, price_low, cop)

    costs_high[offset] = cost_high
    costs_low[offset] = cost_low

    print(f"Offset {offset:+2d}°C → Supply: {supply_temp:5.1f}°C → COP: {cop:.3f}")

print()
print("=" * 70)
print("COST ANALYSIS (per hour):")
print("-" * 70)

baseline_offset = 0
baseline_cop = cops[0]
baseline_cost_high = costs_high[0]
baseline_cost_low = costs_low[0]

print(f"Baseline (offset 0): COP {baseline_cop:.3f}")
print()

for offset in offsets:
    cop = cops[offset]
    cost_high = costs_high[offset]
    cost_low = costs_low[offset]

    cop_diff = cop - baseline_cop
    cop_diff_pct = (cop_diff / baseline_cop) * 100

    cost_high_diff = cost_high - baseline_cost_high
    cost_low_diff = cost_low - baseline_cost_low

    print(f"Offset {offset:+2d}°C:")
    print(f"  COP: {cop:.3f} ({cop_diff:+.3f}, {cop_diff_pct:+.1f}%)")
    print(f"  Cost at high price: €{cost_high:.5f} ({cost_high_diff:+.5f})")
    print(f"  Cost at low price:  €{cost_low:.5f} ({cost_low_diff:+.5f})")
    print()

print("=" * 70)
print("OPTIMIZATION STRATEGY ANALYSIS:")
print("-" * 70)

# Strategy 1: Always use offset 0
cost_always_0 = (costs_high[0] + costs_low[0]) / 2
print(f"Strategy 1 (always offset 0): €{cost_always_0:.5f} avg per hour")

# Strategy 2: Offset -1 at high price, +1 at low price (maximize savings)
cost_optimized = (costs_high[-1] + costs_low[1]) / 2
savings = cost_always_0 - cost_optimized
savings_pct = (savings / cost_always_0) * 100

print(f"Strategy 2 (offset -1 @ high, +1 @ low): €{cost_optimized:.5f} avg per hour")
print(f"Potential savings: €{savings:.5f} per hour ({savings_pct:.2f}%)")
print()

# Calculate savings over 12 hours
hours = 12
total_savings = savings * hours
print(f"Over {hours} hours: €{total_savings:.4f} savings")
print()

print("=" * 70)
print("CONCLUSION:")
print("-" * 70)

if abs(savings) < 0.001:  # Less than 0.1 cent per hour
    print("❌ The k_factor is TOO LOW (0.028)!")
    print()
    print("With such a low k_factor, COP barely changes with temperature.")
    print("Price differences are too small to justify offset adjustments.")
    print()
    print("The optimizer correctly determines that offset 0 is optimal")
    print("because the cost differences are negligible.")
elif savings < 0:
    print("✓ Offset 0 is genuinely optimal - staying at base temperature is best.")
else:
    print(f"⚠️  Offset adjustments COULD save €{savings:.5f}/hour")
    print("But the optimizer chooses offset 0 anyway.")
    print()
    print("Possible reasons:")
    print("- Buffer constraints prevent offset changes")
    print("- Cumulative offset constraints")
    print("- Penalty for temperature changes")

print()
print("=" * 70)
print("RECOMMENDATIONS:")
print("-" * 70)
print()
print("1. INCREASE k_factor to 0.04-0.05 (typical for modern heat pumps)")
print("   This will make COP more sensitive to temperature changes.")
print()
print("2. OR: Verify your actual heat pump COP curve")
print("   - Record COP at different supply temperatures")
print("   - Calculate k_factor from real measurements")
print()
print("3. Current k_factor = 0.028 suggests:")
print("   - Either you have an exceptionally flat COP curve (rare)")
print("   - Or the value needs calibration")
print()
print("4. To test: Temporarily set k_factor = 0.045 and observe")
print("   if optimizer produces non-zero offsets")
print()
print("=" * 70)
