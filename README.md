# heatpump-optimizer

This custom component optimises the heat pump supply temperature based on the dynamic electricity price.  It requires only the price sensor, outdoor temperature sensor, supply temperature sensor and the maximum heat pump power.

When electricity is cheap relative to the following hours, the supply temperature is temporarily increased.  The decrease in COP due to the higher supply temperature is taken into account so that the extra power only runs when it is economically beneficial.

## Algorithm

1. Retrieve the electricity price forecast for the next hours.
2. Read the current outdoor and supply temperatures.
3. Calculate the COP for the current supply temperature and for increments of up to five degrees.
4. Compare the cost of running now at a higher supply temperature with the average future price.
5. The shift, expressed in degrees Celsius, is the increment with the lowest expected cost.
