#!/usr/bin/env python3
"""Compare optimization with different k_factor values."""

outdoor_temp_coefficient = 0.06
cop_compensation_factor = 0.98
DEFAULT_COP_AT_35 = 4.2

base_supply_temp = 26.2  # °C
outdoor_temp = 3.8  # °C
demand_kw = 1.471  # kW

price_high = 0.2474571  # EUR/kWh
price_low = 0.2192036  # EUR/kWh


def calculate_cop(supply_temp, outdoor_temp, k_factor, outdoor_coeff, comp_factor):
    """Calculate COP using the optimizer's formula."""
    cop = (
        DEFAULT_COP_AT_35
        + outdoor_coeff * outdoor_temp
        - k_factor * (supply_temp - 35)
    ) * comp_factor
    return max(0.5, cop)


def analyze_k_factor(k_factor, name):
    """Analyze optimization with given k_factor."""
    print(f"\n{'=' * 70}")
    print(f"{name}: k_factor = {k_factor}")
    print("=" * 70)

    # Calculate COP and costs for different offsets
    offsets = [-2, -1, 0, 1, 2]
    results = []

    for offset in offsets:
        supply_temp = base_supply_temp + offset
        cop = calculate_cop(
            supply_temp,
            outdoor_temp,
            k_factor,
            outdoor_temp_coefficient,
            cop_compensation_factor,
        )
        cost_high = demand_kw * price_high / cop
        cost_low = demand_kw * price_low / cop
        results.append((offset, supply_temp, cop, cost_high, cost_low))

    # Print results
    baseline_cop = results[2][2]  # offset 0
    print(
        f"\n{'Offset':<10} {'Supply':<10} {'COP':<8} {'COP Δ%':<10} "
        f"{'Cost High':<12} {'Cost Low':<12}"
    )
    print("-" * 70)

    for offset, supply_temp, cop, cost_high, cost_low in results:
        cop_diff_pct = ((cop - baseline_cop) / baseline_cop) * 100
        print(
            f"{offset:+3d}°C      {supply_temp:5.1f}°C    {cop:5.3f}   "
            f"{cop_diff_pct:+5.1f}%      €{cost_high:.5f}    €{cost_low:.5f}"
        )

    # Calculate optimal strategy savings
    # Best strategy: low offset at high price (high COP), high offset at low price
    cost_always_0 = (results[2][3] + results[2][4]) / 2  # offset 0

    # Try different strategies
    best_saving = 0
    best_strategy = None

    for i, (off_high, _, _, cost_h, _) in enumerate(results):
        for j, (off_low, _, _, _, cost_l) in enumerate(results):
            if (
                abs(off_high - off_low) <= 2
            ):  # Realistic constraint: max 2°C difference
                avg_cost = (cost_h + cost_l) / 2
                saving = cost_always_0 - avg_cost
                if saving > best_saving:
                    best_saving = saving
                    best_strategy = (off_high, off_low)

    print(f"\nBest strategy: offset {best_strategy[0]:+d} @ high price, "
          f"{best_strategy[1]:+d} @ low price")
    print(f"Savings: €{best_saving:.5f} per hour ({best_saving/cost_always_0*100:.2f}%)")
    print(f"Over 12 hours: €{best_saving * 12:.4f}")
    print(f"Over 24 hours: €{best_saving * 24:.4f}")
    print(f"Over 1 month (30 days): €{best_saving * 24 * 30:.2f}")
    print(f"Over 1 heating season (180 days): €{best_saving * 24 * 180:.2f}")

    return best_saving


print("=" * 70)
print("K_FACTOR COMPARISON ANALYSIS")
print("=" * 70)
print("\nThis analysis shows how different k_factor values affect")
print("the potential for cost optimization through offset adjustments.")
print()
print("Current conditions:")
print(f"  Base supply temperature: {base_supply_temp}°C")
print(f"  Outdoor temperature: {outdoor_temp}°C")
print(f"  Price difference: {(price_high - price_low)*1000:.1f} cents/kWh (12.9%)")

# Analyze different k_factors
current_savings = analyze_k_factor(0.028, "CURRENT (Too Low)")
typical_savings = analyze_k_factor(0.045, "TYPICAL (Recommended)")
conservative_savings = analyze_k_factor(0.035, "CONSERVATIVE")

print("\n" + "=" * 70)
print("SUMMARY COMPARISON")
print("=" * 70)
print()
print(f"{'K_Factor':<12} {'Hourly Savings':<18} {'Monthly Savings':<18} "
      f"{'Seasonal Savings'}")
print("-" * 70)
print(
    f"{0.028:<12.3f} €{current_savings:.5f}          "
    f"€{current_savings * 24 * 30:6.2f}           €{current_savings * 24 * 180:6.2f}"
)
print(
    f"{0.035:<12.3f} €{conservative_savings:.5f}          "
    f"€{conservative_savings * 24 * 30:6.2f}           €{conservative_savings * 24 * 180:6.2f}"
)
print(
    f"{0.045:<12.3f} €{typical_savings:.5f}          "
    f"€{typical_savings * 24 * 30:6.2f}           €{typical_savings * 24 * 180:6.2f}"
)

print("\n" + "=" * 70)
print("CONCLUSION")
print("=" * 70)
print()

if current_savings < 0.0001:
    print("❌ With k_factor = 0.028, optimization saves essentially NOTHING")
    print("   The optimizer correctly chooses offset 0.")
    print()

improvement_factor = typical_savings / current_savings if current_savings > 0 else float('inf')
if improvement_factor > 10:
    print(f"✅ Increasing k_factor to 0.045 would increase savings by {improvement_factor:.0f}x!")
    print(f"   From essentially nothing to €{typical_savings * 24 * 180:.2f} per heating season")
else:
    print(f"✅ Increasing k_factor to 0.045 would increase savings by {improvement_factor:.1f}x")
    print(f"   From €{current_savings * 24 * 180:.2f} to €{typical_savings * 24 * 180:.2f} per season")

print()
print("RECOMMENDED ACTION:")
print("-" * 70)
print("1. Start with k_factor = 0.035 (conservative)")
print("2. Observe optimizer behavior and actual COP changes")
print("3. If COP curve seems correct, increase to 0.045")
print("4. Monitor actual electricity savings vs predictions")
print()
print("=" * 70)
