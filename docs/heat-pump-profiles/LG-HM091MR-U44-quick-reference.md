# LG HM091MR.U44 - Quick Reference

> **TL;DR**: Snelle setup voor LG Therma V HM091MR.U44 Monoblock

## Aanbevolen Instellingen

```yaml
# Kopieer deze waarden in je configuratie
base_cop: 4.2
k_factor: 0.030
outdoor_temp_coefficient: 0.08
cop_compensation_factor: 0.90
heat_curve_min: 25  # vloerverwarming
heat_curve_max: 45  # vloerverwarming
```

## Wijzig Deze Na 2 Weken

1. **Monitor je werkelijke COP**:
   - Thermal output (kWh) / Elektrisch verbruik (kWh) = Werkelijke COP

2. **Als werkelijke COP lager is dan sensor meldt**:
   - Verlaag `cop_compensation_factor` met factor: `werkelijke_cop / berekende_cop`
   - Voorbeeld: Werkelijke = 3.6, Berekende = 4.2
     - Nieuwe factor = 0.90 × (3.6/4.2) = 0.77

3. **Als huis te warm wordt bij lage temperaturen**:
   - Verlaag `k_factor` naar 0.025

4. **Als huis te koud wordt bij lage temperaturen**:
   - Verhoog `k_factor` naar 0.035

## Veelvoorkomende Issues

| Probleem | Oplossing |
|----------|-----------|
| COP lijkt te hoog (>5.0) | Verlaag `base_cop` naar 4.0 |
| Huis wordt niet warm | Verhoog `heat_curve_max` naar 50°C |
| Te agressieve optimalisatie | Verhoog `cop_compensation_factor` naar 0.95 |
| Geen verschuiving naar goedkope uren | Check prijs forecast in elektriciteitsprijs sensor |

## Model Specificaties

| Specificatie | Waarde |
|--------------|--------|
| Capaciteit | 9 kW |
| COP @ A7/W35 | 4.18 |
| SCOP | 4.45 |
| Energielabel | A+++ @ 35°C, A++ @ 55°C |
| Koudemiddel | R-32 |
| Max aanvoer | 65°C |
| Min buiten | -25°C |

## Volledige Gids

📖 Lees de **[volledige configuratiegids](LG-HM091MR-U44.md)** voor:
- Gedetailleerde uitleg van elke parameter
- Stap-voor-stap calibratie proces
- Technische achtergrond en formules
- LG controller integratie
- Troubleshooting specifiek voor dit model

---

**Vragen?** Plaats ze in de [GitHub Discussions](https://github.com/bvweerd/heating_curve_optimizer/discussions) met tag `#lg-therma-v`
