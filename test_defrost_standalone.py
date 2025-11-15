#!/usr/bin/env python3
"""Standalone test script for defrost factor calculation."""


def _calculate_defrost_factor(outdoor_temp: float, humidity: float = 80.0) -> float:
    """Calculate COP degradation due to defrost cycles for air-source heat pumps.

    Based on research for air-source heat pumps in humid climates (like Netherlands).
    Frosting occurs when outdoor temperature is between -10°C and 6°C with sufficient humidity.
    The worst frosting occurs around 0-3°C with high humidity (70-90%).

    Args:
        outdoor_temp: Outdoor temperature in °C
        humidity: Relative humidity in % (default 80% for Dutch maritime climate)

    Returns:
        Multiplier (0.60-1.0) to apply to base COP accounting for defrost losses

    Research references:
    - Frosting occurs at 100% RH below 3.1°C, at 70% RH below 5.3°C
    - COP degradation: typical 10-15%, worst case up to 40%
    - Most critical range: 0-7°C in humid climates
    """
    # No frosting above 6°C - heat pump operates at full efficiency
    if outdoor_temp >= 6.0:
        return 1.0

    # No frosting below -10°C (air too dry, insufficient moisture to freeze)
    if outdoor_temp <= -10.0:
        return 1.0

    # Calculate humidity-dependent frosting threshold
    # At 100% RH: frosting starts at 3.1°C
    # At 70% RH: frosting starts at 5.3°C
    # Linear interpolation for other humidity levels
    frosting_threshold = 3.1 + (humidity - 100) * (5.3 - 3.1) / (70 - 100)
    frosting_threshold = max(min(frosting_threshold, 6.0), -10.0)

    # No frosting if temperature is above the humidity-dependent threshold
    if outdoor_temp >= frosting_threshold:
        return 1.0

    # Frosting zone: calculate defrost penalty
    if outdoor_temp >= 0:
        # Worst frosting zone: 0-3°C
        # COP loss increases as we approach 0-2°C
        if outdoor_temp <= 3:
            # Maximum penalty at 0-2°C: 15-40% depending on humidity
            base_penalty = 0.25  # 25% base COP loss in worst conditions
            temp_factor = (
                1.0 - (outdoor_temp / 3.0) * 0.4
            )  # Reduces penalty as temp increases
        else:
            # Moderate frosting zone: 3-6°C
            # Linear reduction in penalty from 3°C to frosting threshold
            base_penalty = 0.15  # 15% COP loss
            temp_factor = (frosting_threshold - outdoor_temp) / (
                frosting_threshold - 3.0
            )
    else:
        # Below freezing: -10 to 0°C
        # Moderate frosting, less severe than near-zero temperatures
        base_penalty = 0.12  # 12% COP loss
        temp_factor = (outdoor_temp + 10) / 10.0

    # Adjust for humidity (Dutch climate typically 75-90% RH in winter)
    # Higher humidity = more frost formation = worse COP degradation
    humidity_factor = min(1.0, max(0.5, humidity / 80.0))  # Normalized to 80% baseline

    # Calculate final defrost penalty
    defrost_penalty = base_penalty * temp_factor * humidity_factor

    # Return COP multiplier (1.0 = no loss, 0.6 = 40% loss in worst case)
    cop_multiplier = 1.0 - defrost_penalty

    return max(0.60, cop_multiplier)  # Minimum 60% efficiency (40% max loss)


# Test defrost factor at various temperatures and humidity levels
print("Testing defrost factor calculation:")
print("=" * 70)
print(f"{'Temp (°C)':<12} {'Humidity (%)':<15} {'Defrost Factor':<18} {'COP Loss (%)'}")
print("=" * 70)

test_cases = [
    # Temperature, Humidity
    (-15, 80),  # Too cold for frosting
    (-5, 80),  # Below freezing
    (0, 80),  # Freezing point - worst case
    (1, 80),  # Near freezing
    (2, 90),  # Worst zone with high humidity
    (3, 80),  # Worst zone
    (4, 70),  # Moderate frosting
    (5, 80),  # Light frosting
    (6, 80),  # Above frosting threshold
    (10, 80),  # No frosting
    (2, 50),  # Near freezing, lower humidity
    (2, 100),  # Near freezing, very high humidity
]

for temp, humidity in test_cases:
    factor = _calculate_defrost_factor(temp, humidity)
    cop_loss = (1 - factor) * 100
    print(f"{temp:<12.1f} {humidity:<15.0f} {factor:<18.3f} {cop_loss:.1f}%")

print("=" * 70)
print("\nKey insights:")
print("- Factor 1.0 = No COP loss (no defrost needed)")
print("- Factor 0.85 = 15% COP loss (typical for Dutch winter)")
print("- Factor 0.60 = 40% COP loss (worst case)")
print("\nDutch winter typical: 2-6°C with 75-90% humidity")
print("Expected COP loss: 10-25% depending on conditions")
print("\n✓ All calculations completed successfully!")
