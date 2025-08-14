```mermaid
flowchart LR

%% === Sensors & Parameters ===
  %% Lichtblauw: External sensor met voorspelling in attributes
  consumption_price(["Consumption price"]):::sensor_pred
  production_price(["Production price"]):::sensor_pred

  %% Groen: External sensors (1 of meer) geselecteerd in Configflow
  power_consumption_hist(["Power consumption history"]):::sensor
  power_production_hist(["Power production history"]):::sensor
  heatpump_power_hist(["Heatpump power history"]):::sensor

  %% Lichtgroen: External sensor geselecteerd in Configflow
  outdoor_temp(["Outdoor temperature"]):::sensor
  indoor_temp(["Indoor temperature"]):::sensor

  %% Geel: Waarden uit parameters in Configflow
  planning_window[/"Planning window"/]:::param
  time_base[/"Time base"/]:::param
  energy_label[/"Energy label\n(A+++, A++, A+, A, B... G)"/]:::param
  floor_sqm[/"Floor sqm"/]:::param
  glass_area[/"Glass surface area\nEast/West/South"/]:::param
  glass_uvalue[/"Glass U-value"/]:::param
  heat_curve_min_outdoor[/"Heat curve min outdoor temp"/]:::param
  heat_curve_max_outdoor[/"Heat curve max outdoor temp"/]:::param
  heat_curve_min_water[/"Heat curve min water temp"/]:::param
  heat_curve_max_water[/"Heat curve max water temp"/]:::param
  cop_curve_comp_factor[/"COP curve compensation factor"/]:::param
  cop_curve_formula[/"COP curve formula"/]:::param

  %% Roze: Output sensoren van component
  house_heat_loss["House heat loss"]:::output
  window_heat_gen["Window heat generation"]:::output
  net_heat_loss["Net heat loss"]:::output
  calc_supply_temp["Calculated supply temperature"]:::output
  heating_curve_shift["Heating curve shift +/-"]:::output
  available_energy["Available energy\nfor price x and price y"]:::output
  heat_buffered["Heat buffered by shift"]:::output

  %% Paars: Input van OpenMeteo API
  outdoor_temp_pred(["Outdoor temperature prediction"]):::input
  indoor_temp_pred(["Indoor temperature prediction"]):::input
  sun_intensity_pred(["Sun intensity prediction"]):::input

  %% Beige: Diagnostics
  cop_efficiency["+/- COP efficiency"]:::diag
  heat_generation["+/- Heat generation"]:::diag

%% === Flow relaties ===
  outdoor_temp_pred --> house_heat_loss
  indoor_temp_pred --> house_heat_loss
  energy_label --> house_heat_loss
  floor_sqm --> house_heat_loss

  sun_intensity_pred --> window_heat_gen
  glass_area --> window_heat_gen
  glass_uvalue --> window_heat_gen

  house_heat_loss --> net_heat_loss
  window_heat_gen --> net_heat_loss

  heat_curve_min_outdoor --> calc_supply_temp
  heat_curve_max_outdoor --> calc_supply_temp
  heat_curve_min_water --> calc_supply_temp
  heat_curve_max_water --> calc_supply_temp
  outdoor_temp --> calc_supply_temp
  calc_supply_temp --> net_heat_loss

  power_consumption_hist --> available_energy
  power_production_hist --> available_energy
  heatpump_power_hist --> available_energy

  available_energy --> consumption_price
  available_energy --> production_price

  consumption_price --> heating_curve_shift
  production_price --> heating_curve_shift

  planning_window --> heating_curve_shift
  time_base --> heating_curve_shift

  net_heat_loss --> heating_curve_shift
  cop_efficiency --> heating_curve_shift
  heat_generation --> heating_curve_shift
  heating_curve_shift --> heat_buffered
  heat_buffered --> heating_curve_shift

  cop_curve_comp_factor --> heating_curve_shift
  cop_curve_formula --> heating_curve_shift

%% === Stijlen ===
  classDef sensor_pred fill:#bae6fd,stroke:#0369a1,color:#0c4a6e;
  classDef sensor fill:#bbf7d0,stroke:#15803d,color:#14532d;
  classDef param fill:#fef3c7,stroke:#92400e,color:#78350f;
  classDef output fill:#f9a8d4,stroke:#9d174d,color:#831843;
  classDef input fill:#ddd6fe,stroke:#6b21a8,color:#581c87;
  classDef diag fill:#fde68a,stroke:#92400e,color:#78350f;
