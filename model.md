```mermaid

flowchart LR
  %% === Nodes ===
  outdoor_temp(["Outdoor temperature"]):::sensor
  indoor_temp(["Indoor temperature"]):::sensor
  sun_intensity(["Sun intensity"]):::sensor
  consumption_price(["Consumption price"]):::sensor
  production_price(["Production price"]):::sensor

  heat_curve_min[/"Heat curve min"/]:::param
  heat_curve_max[/"Heat curve max"/]:::param
  heat_loss_coeff[/"Heat loss coeff"/]:::param
  glass_area_u[/"Glass area × U"/]:::param

  base_supply_temp["map_linear: Base supply temp"]:::op
  temp_delta["Σ: Tin - Tout"]:::op
  house_heat_loss["×: House heat loss"]:::op
  window_gain["×: Window heat gain"]:::op
  net_heat_loss["Σ: Net heat loss"]:::op

  subgraph PlanningWindow["Planning window"]
    plan_sum["sliding_window_sum (window=6h, step=1h)"]:::op
  end

  cop["COP curve"]:::op
  price_avg["weighted_avg_price"]:::op
  heating_curve_shift["controller: Heating curve shift"]:::op
  supply_setpoint["Σ: Supply setpoint"]:::op

  %% === Edges ===
  outdoor_temp --> base_supply_temp
  heat_curve_min --> base_supply_temp
  heat_curve_max --> base_supply_temp

  indoor_temp --> temp_delta
  outdoor_temp --> temp_delta
  temp_delta --> house_heat_loss
  heat_loss_coeff --> house_heat_loss

  sun_intensity --> window_gain
  glass_area_u --> window_gain

  house_heat_loss --> net_heat_loss
  window_gain --> net_heat_loss
  net_heat_loss --> plan_sum

  base_supply_temp --> cop
  outdoor_temp --> cop

  consumption_price --> price_avg
  production_price --> price_avg

  plan_sum --> heating_curve_shift
  cop --> heating_curve_shift
  price_avg --> heating_curve_shift

  base_supply_temp --> supply_setpoint
  heating_curve_shift --> supply_setpoint

  %% === Styling ===
  classDef sensor fill:#d1fae5,stroke:#065f46,color:#064e3b;
  classDef param  fill:#fef3c7,stroke:#92400e,color:#78350f,stroke-width:1px;
  classDef op     fill:#e0e7ff,stroke:#3730a3,color:#1e3a8a;
