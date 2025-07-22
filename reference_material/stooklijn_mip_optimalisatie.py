# MIP-based stooklijn optimalisatie met dummydata
# Run dit script met Python 3 + pulp geïnstalleerd

from pulp import *
import numpy as np

# === Data ===
T_out = [5, 4, 3, 2]  # Buitentemperatuur
Solar = [50, 30, 20, 10]  # Wh/m² zoninstraling
Price = [0.10, 0.12, 0.18, 0.22]  # €/kWh
Area = 120  # m²
U = 0.8  # W/m²K (energielabel B)
T_in = 21
eta_solar = 0.15
base_supply_temp = 35
H = len(T_out)

# === Berekeningen ===
Q_loss = [Area * U * (T_in - T_out[t]) / 1000 for t in range(H)]
Q_solar = [Solar[t] * Area * eta_solar / 1000 for t in range(H)]
Q_netto = [max(0, Q_loss[t] - Q_solar[t]) for t in range(H)]

model = LpProblem('StooklijnOptimalisatie', LpMinimize)
offsets = LpVariable.dicts('offset', range(H), -4, 4, cat='Integer')
deltas = LpVariable.dicts('delta', range(1, H), 0, 8, cat='Integer')

costs = []
for t in range(H):
    t_supply = base_supply_temp + offsets[t]
    delta_t = t_supply - T_out[t]
    cop = 6 - 0.1 * delta_t
    q = Q_netto[t]
    costs.append((q / cop) * Price[t])

model += lpSum(costs)

# Constraints
for t in range(H):
    model += (base_supply_temp + offsets[t]) >= 28
    model += (base_supply_temp + offsets[t]) <= 45

for t in range(1, H):
    model += offsets[t] - offsets[t-1] <= deltas[t]
    model += offsets[t-1] - offsets[t] <= deltas[t]
    model += deltas[t] <= 1

model.solve()

optimal_offsets = [int(value(offsets[t])) for t in range(H)]
total_cost = value(model.objective)

print('Optimal Offsets per hour:', optimal_offsets)
print('Total Expected Cost (€): {:.4f}'.format(total_cost))