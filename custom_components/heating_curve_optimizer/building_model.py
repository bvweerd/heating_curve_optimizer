"""Thermal building physics model (1R1C) for the Heating Curve Optimizer.

The building is modelled the same way `battery_controller` models a battery:
one state variable (indoor temperature instead of state of charge), one
capacity (thermal mass instead of kWh), one loss term (UA instead of
round-trip inefficiency).

This module is deliberately independent of Home Assistant: no `hass`, no
config entries, no coordinators. It is pure physics, constructed from plain
values, so it can be unit tested without the HA test harness and reused
unchanged by `thermal_optimizer.py`'s DP loop (which calls
`next_indoor_temp` many thousands of times per optimization run).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import (
    CONF_AREA_M2,
    CONF_CEILING_HEIGHT,
    CONF_ENERGY_LABEL,
    CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
    CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
    CONF_INTERNAL_GAINS_W_PER_M2,
    CONF_TARGET_INDOOR_TEMP,
    CONF_THERMAL_MASS_CLASS,
    CONF_VENTILATION_TYPE,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_EMITTER_TYPE,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
    DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
    DEFAULT_INTERNAL_GAINS_W_PER_M2,
    DEFAULT_TARGET_INDOOR_TEMP,
    DEFAULT_THERMAL_MASS_CLASS,
    DEFAULT_VENTILATION_TYPE,
    EMITTER_EXPONENT_MAP,
    THERMAL_MASS_WH_PER_M2_K,
    calculate_htc_from_energy_label,
)


@dataclass
class BuildingConfig:
    """Physical parameters of the 1R1C building model.

    Thermal equivalent of `battery_model.BatteryConfig`:
    - `ua_w_per_k`            <-> round-trip inefficiency (how fast energy leaks)
    - `thermal_mass_kwh_per_k` <-> capacity_kwh (how much energy fits before
                                    the state moves)
    - `comfort_min`/`comfort_max` <-> min_soc_percent/max_soc_percent
    """

    area_m2: float
    energy_label: str = "C"
    ventilation_type: str = DEFAULT_VENTILATION_TYPE
    ceiling_height: float = DEFAULT_CEILING_HEIGHT
    thermal_mass_class: str = DEFAULT_THERMAL_MASS_CLASS

    comfort_min: float = 19.0
    comfort_max: float = 20.5
    # Continuous internal heat gains (people, appliances, lighting), in kW.
    internal_gain_kw: float = 0.0
    # Correction on the modelled window solar gain (learned by calibration).
    solar_factor: float = 1.0

    # Derived values (calculated in __post_init__)
    ua_w_per_k: float = field(init=False)
    thermal_mass_kwh_per_k: float = field(init=False)
    time_constant_hours: float = field(init=False)

    def __post_init__(self) -> None:
        """Calculate derived physical values from the configured inputs."""
        self.ua_w_per_k = calculate_htc_from_energy_label(
            self.energy_label,
            self.area_m2,
            ventilation_type=self.ventilation_type,
            ceiling_height=self.ceiling_height,
        )
        wh_per_m2_k = THERMAL_MASS_WH_PER_M2_K.get(
            self.thermal_mass_class,
            THERMAL_MASS_WH_PER_M2_K[DEFAULT_THERMAL_MASS_CLASS],
        )
        self.thermal_mass_kwh_per_k = self.area_m2 * wh_per_m2_k / 1000.0

        # tau = C / UA. C is in kWh/K (= kW per K), UA is in W/K, so UA/1000
        # puts both sides in kW/K before dividing.
        if self.ua_w_per_k > 0:
            self.time_constant_hours = self.thermal_mass_kwh_per_k / (
                self.ua_w_per_k / 1000.0
            )
        else:
            self.time_constant_hours = float("inf")

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> BuildingConfig:
        """Build a `BuildingConfig` from a merged config-entry dict.

        The comfort band is `target - hysteresis_lower` .. `target +
        hysteresis_upper`; pass a config dict that already has the live
        values from the number entities merged in.
        """
        target = float(config.get(CONF_TARGET_INDOOR_TEMP, DEFAULT_TARGET_INDOOR_TEMP))
        hysteresis_lower = float(
            config.get(
                CONF_INDOOR_TEMP_HYSTERESIS_LOWER,
                DEFAULT_INDOOR_TEMP_HYSTERESIS_LOWER,
            )
        )
        hysteresis_upper = float(
            config.get(
                CONF_INDOOR_TEMP_HYSTERESIS_UPPER,
                DEFAULT_INDOOR_TEMP_HYSTERESIS_UPPER,
            )
        )
        area_m2 = float(config.get(CONF_AREA_M2, 0.0))
        internal_w_per_m2 = float(
            config.get(CONF_INTERNAL_GAINS_W_PER_M2, DEFAULT_INTERNAL_GAINS_W_PER_M2)
        )
        return cls(
            area_m2=area_m2,
            energy_label=str(config.get(CONF_ENERGY_LABEL, "C")),
            ventilation_type=str(
                config.get(CONF_VENTILATION_TYPE, DEFAULT_VENTILATION_TYPE)
            ),
            ceiling_height=float(
                config.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)
            ),
            thermal_mass_class=str(
                config.get(CONF_THERMAL_MASS_CLASS, DEFAULT_THERMAL_MASS_CLASS)
            ),
            comfort_min=target - hysteresis_lower,
            comfort_max=target + hysteresis_upper,
            internal_gain_kw=area_m2 * internal_w_per_m2 / 1000.0,
        )

    def heat_loss_kw(self, indoor_temp: float, outdoor_temp: float) -> float:
        """Transmission + ventilation heat loss at the given delta-T, in kW.

        This IS the SoC-drain term of `battery_model`, expressed as a
        continuous flow (kW) rather than a per-step energy loss, because the
        indoor temperature (unlike SoC) keeps changing between DP steps from
        this loss alone even while the heat pump is off.
        """
        return self.ua_w_per_k * (indoor_temp - outdoor_temp) / 1000.0

    def next_indoor_temp(
        self,
        indoor_temp: float,
        *,
        outdoor_temp: float,
        heat_input_kw: float,
        solar_gain_kw: float = 0.0,
        internal_gain_kw: float | None = None,
        step_hours: float,
    ) -> float:
        """Advance the 1R1C state by one time step.

        Explicit Euler step of the energy balance: heat in (heat pump,
        solar, internal gains) minus transmission/ventilation loss, divided
        by thermal mass. `internal_gain_kw=None` uses the building's own
        configured internal gains.
        """
        if self.thermal_mass_kwh_per_k <= 0:
            return indoor_temp
        if internal_gain_kw is None:
            internal_gain_kw = self.internal_gain_kw
        loss_kw = self.heat_loss_kw(indoor_temp, outdoor_temp)
        net_kw = heat_input_kw + solar_gain_kw + internal_gain_kw - loss_kw
        delta_t = net_kw * step_hours / self.thermal_mass_kwh_per_k
        return indoor_temp + delta_t

    def discretize_states(
        self, resolution: float = 0.1, margin: float = 1.5
    ) -> list[float]:
        """Return the T_in state ladder used by the optimizer's DP table.

        Spans [comfort_min - margin, comfort_max + margin]: the margin lets
        the DP represent brief excursions outside comfort (and penalize
        them, see thermal_optimizer.py) rather than making such states
        simply unrepresentable.
        """
        lo = self.comfort_min - margin
        hi = self.comfort_max + margin
        if hi <= lo or resolution <= 0:
            return [self.comfort_min]
        n = max(1, round((hi - lo) / resolution))
        return [round(lo + i * (hi - lo) / n, 6) for i in range(n + 1)]


@dataclass
class EmitterConfig:
    """Heat emission characteristic of the installed radiators/floor heating.

    Physical role: how much thermal power can actually be pushed into the
    room at a given supply temperature. This is what makes the heating-curve
    offset a real decision instead of a free parameter: raising the offset
    raises the deliverable power (at a worse COP, see heatpump_model.py);
    lowering it does the opposite. Follows the standard emitter power law
        Q(T_sup) = Q_nominal * ((T_sup - T_in) / dT_nominal) ** exponent
    (EN 442 for radiators, exponent ≈ 1.3; underfloor/fan-coil are flatter).
    """

    exponent: float = 1.3
    nominal_delta_t: float = 25.0
    nominal_power_kw: float = 6.0

    def available_power_kw(self, *, supply_temp: float, indoor_temp: float) -> float:
        """Maximum thermal power (kW) the emitter can deliver at this ΔT."""
        delta_t = supply_temp - indoor_temp
        if delta_t <= 0 or self.nominal_delta_t <= 0:
            return 0.0
        ratio = delta_t / self.nominal_delta_t
        return float(self.nominal_power_kw * ratio**self.exponent)

    @classmethod
    def sized_to_building(
        cls,
        building: BuildingConfig,
        *,
        design_outdoor_temp: float,
        design_supply_temp: float,
        design_indoor_temp: float | None = None,
        emitter_type: str = DEFAULT_EMITTER_TYPE,
    ) -> EmitterConfig:
        """Derive an emitter sized to exactly cover the building's peak loss.

        Standard installer sizing assumption: radiators/floor loops are
        chosen so that at the heating curve's coldest design point
        (`design_outdoor_temp` -> `design_supply_temp`, i.e.
        CONF_HEAT_CURVE_MIN_OUTDOOR -> CONF_HEAT_CURVE_MAX), the emitter's
        nominal output exactly matches the building's peak heat loss. This
        gives a physically grounded emitter curve from config that already
        exists, without asking the user for a radiator schedule.
        """
        indoor = (
            design_indoor_temp
            if design_indoor_temp is not None
            else building.comfort_max
        )
        nominal_power_kw = building.heat_loss_kw(indoor, design_outdoor_temp)
        nominal_delta_t = design_supply_temp - indoor
        exponent = EMITTER_EXPONENT_MAP.get(
            emitter_type, EMITTER_EXPONENT_MAP[DEFAULT_EMITTER_TYPE]
        )
        return cls(
            exponent=exponent,
            nominal_delta_t=max(nominal_delta_t, 1.0),
            nominal_power_kw=max(nominal_power_kw, 0.1),
        )
