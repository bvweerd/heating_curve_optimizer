def predict_indoor_temps(outdoor_forecast, solar_forecast, area, U, t_inside=20):
    capacity_kwh_per_k = 0.16 * area / 10  # Approximate building heat capacity
    results = []
    for t_out, solar in zip(outdoor_forecast, solar_forecast):
        q_loss = area * U * (t_inside - t_out) / 1000  # kWh loss
        q_gain = solar * area * 0.15 / 1000  # Approximate solar gain
        net_loss = (q_loss - q_gain)
        t_inside -= net_loss / capacity_kwh_per_k
        results.append(round(t_inside, 2))
    return results

def fallback_cop(t_supply, t_outdoor, a=6.0, b=0.1):
    return max(1.5, a - b * (t_supply - t_outdoor))

def estimate_cop_from_power(power, q_output):
    return q_output / power if power > 0 else None

def optimize_offsets(temp_forecast, solar_forecast, price_forecast, area, U, horizon, cop_base):
    base_offset = 0
    offsets = []
    for i in range(horizon):
        t_out = temp_forecast[i]
        solar = solar_forecast[i]
        price = price_forecast[i]
        t_supply = 35 + base_offset
        cop = fallback_cop(t_supply, t_out)
        q_loss = area * U * (21 - t_out) / 1000 - solar * area * 0.15 / 1000
        cost = max(0, q_loss / cop * price)
        offsets.append(round(base_offset, 2))
    return offsets