# LG Therma V HM091MR.U44 Monoblock - Optimale Instellingen

## Overzicht

De **LG HM091MR.U44** is een moderne 9kW R32 monoblock lucht-water warmtepomp met uitstekende prestaties:

- **Model**: LG Therma V Monobloc S (Gen 2)
- **Capaciteit**: 9 kW (7,740 kcal/h)
- **COP**: 4.18 @ A7/W35
- **SCOP**: 4.45
- **Energielabel**: A+++ bij 35°C aanvoertemperatuur
- **Koudemiddel**: R-32
- **Max aanvoertemperatuur**: 65°C
- **Werkbereik**: -25°C tot +35°C buitentemperatuur

## Aanbevolen Configuratie voor Heating Curve Optimizer

### 1. Basisinstellingen

| Parameter | Aanbevolen Waarde | Toelichting |
|-----------|-------------------|-------------|
| **Vloeroppervlak** | Jouw waarde (m²) | Werkelijke bewoonbare oppervlakte van je woning |
| **Energielabel** | Jouw label | Bepaalt de warmteverliescoëfficiënt |

### 2. Heat Pump COP Parameters

#### **Base COP** (base_cop)
- **Aanbevolen**: `4.2` - `4.5`
- **Standaard gebruik**: `4.2`
- **Optimistische schatting**: `4.5` (dichter bij SCOP)

De LG HM091MR.U44 heeft een getest COP van 4.18 bij A7/W35 condities. Voor jaarrond optimalisatie is het SCOP van 4.45 een betere indicatie.

**Advies**: Begin met `4.2` en pas aan op basis van je werkelijke energieverbruik.

#### **K-factor** (k_factor)
- **Aanbevolen**: `0.028` - `0.032`
- **Standaard gebruik**: `0.030`

De k-factor bepaalt hoeveel de COP daalt per graad Celsius stijging van de aanvoertemperatuur boven 35°C.

**Waarom lager dan de standaard 0.11?**
- Moderne R32 monoblocks presteren beter bij hogere temperaturen
- LG Therma V heeft A++ rating bij 55°C (vs A+++ bij 35°C), wat een geleidelijke degradatie aangeeft
- Testing van vergelijkbare modellen toont k-factor tussen 0.025-0.035

**Berekening check**:
```
COP @ 35°C: 4.2
COP @ 55°C met k=0.03: 4.2 - 0.03 * (55-35) = 4.2 - 0.6 = 3.6

Dit komt overeen met typische A++ prestaties bij 55°C.
```

#### **Outdoor Temperature Coefficient** (outdoor_temp_coefficient)
- **Aanbevolen**: `0.08`
- **Bereik**: `0.06` - `0.10`

Deze parameter bepaalt hoeveel de COP stijgt per graad Celsius stijging van de buitentemperatuur.

**Standaard**: `0.08` is geschikt voor de meeste installaties.

#### **COP Compensation Factor** (cop_compensation_factor)
- **Aanbevolen**: `0.85` - `0.95`
- **Standaard gebruik**: `0.90`

Deze factor compenseert voor real-world verliezen versus theoretische COP:
- Warmteverlies in leidingen
- Circulatiepomp verbruik
- Ontdooicycli in winter
- Niet-ideale installatie-omstandigheden

**Advies**:
1. Start met `0.90`
2. Monitor je werkelijke energieverbruik gedurende 2-4 weken
3. Bereken: `werkelijke_cop = thermische_output / elektrisch_verbruik`
4. Pas aan: `nieuwe_factor = (werkelijke_cop / berekende_cop) * huidige_factor`

### 3. Aanvoertemperatuur Limieten

#### **Minimum Aanvoertemperatuur** (heat_curve_min)
- **Aanbevolen**: `25°C` (vloerverwarming) of `30°C` (radiatoren)
- **Standaard**: `20°C`

#### **Maximum Aanvoertemperatuur** (heat_curve_max)
- **Aanbevolen**: `45°C` (vloerverwarming) of `55°C` (radiatoren)
- **Standaard**: `45°C`

**Let op**: Hogere temperaturen verlagen de COP significant. Probeer onder 50°C te blijven voor optimale efficiency.

### 4. Optimalisatie Instellingen

#### **Planning Window** (planning_window)
- **Aanbevolen**: `6` uur (dynamische prijzen) of `12` uur (zeer variabele prijzen)
- **Standaard**: `6` uur

#### **Time Base** (time_base)
- **Aanbevolen**: `60` minuten
- **Standaard**: `60` minuten

Match dit met je elektriciteitsprijzen interval (meestal 60 minuten voor Nederlandse leveranciers).

### 5. Glas Configuratie (Zonnewinst)

Configureer je raamoppervlakten voor nauwkeurige zonnewinst berekening:

| Oriëntatie | Oppervlakte | G-waarde |
|------------|-------------|----------|
| **Oost** | Jouw m² | Afhankelijk van glas type |
| **Zuid** | Jouw m² | Meestal 0.6-0.7 voor dubbel glas |
| **West** | Jouw m² | 0.5-0.6 voor HR++ glas |

**Tip**: Meet alleen het **glasoppervlak**, niet het kozijnoppervlak.

## Complete Configuratie Voorbeeld

```yaml
# Voorbeeld configuratie in Home Assistant
heating_curve_optimizer:
  area_m2: 150
  energy_label: "C"

  # LG HM091MR.U44 specifieke COP parameters
  base_cop: 4.2
  k_factor: 0.030
  outdoor_temp_coefficient: 0.08
  cop_compensation_factor: 0.90

  # Temperatuur limieten (vloerverwarming)
  heat_curve_min: 25
  heat_curve_max: 45

  # Glas configuratie
  glass_east_m2: 8
  glass_south_m2: 15
  glass_west_m2: 8
  glass_u_value: 1.2

  # Optimalisatie
  planning_window: 6
  time_base: 60
```

## Calibratie Stappenplan

### Stap 1: Start met Aanbevolen Waarden
Configureer de optimizer met bovenstaande aanbevolen waarden.

### Stap 2: Monitor Gedurende 2 Weken
Let op:
- Sensor: `sensor.heating_curve_optimizer_quadratic_cop`
- Sensor: `sensor.heating_curve_optimizer_heat_pump_thermal_power`
- Je werkelijke elektriciteitsmeter
- LG ThinQ app (indien beschikbaar) voor werkelijke COP

### Stap 3: Vergelijk Theoretische vs Werkelijke COP

**Voorbeeld berekening**:
```
Dag 1:
- Thermische output: 45 kWh (uit sensor)
- Elektrisch verbruik: 12 kWh (uit meter)
- Werkelijke COP: 45 / 12 = 3.75

Optimizer berekent:
- Gemiddelde berekende COP: 4.2

Compensatie factor aanpassing:
- Nieuwe factor: 0.90 × (3.75 / 4.2) = 0.80
```

### Stap 4: Fine-tuning K-factor

Als je merkt dat de optimizer:
- **Te lage aanvoertemperaturen kiest bij kou**: verhoog k_factor naar 0.035
- **Te hoge aanvoertemperaturen kiest bij kou**: verlaag k_factor naar 0.025

### Stap 5: Valideer met LG ThinQ App

Als je de LG ThinQ app gebruikt:
1. Vergelijk de door optimizer voorspelde COP met LG's gerapporteerde COP
2. Check of aanvoertemperaturen binnen verwachte bereik liggen
3. Monitor of de warmtepomp de gewenste aanvoertemperaturen kan bereiken

## Veelvoorkomende Issues & Oplossingen

### Issue: COP lijkt te hoog
**Symptoom**: Optimizer berekent COP > 5.0
**Oorzaak**: k_factor te laag of base_cop te hoog
**Oplossing**:
- Verlaag base_cop naar 4.0
- Verhoog k_factor naar 0.035

### Issue: Warmtepomp blijft te lang op hoge temperatuur
**Symptoom**: Aanvoertemperatuur constant boven 50°C
**Oorzaak**: Verkeerde stooklijn of te lage k_factor
**Oplossing**:
- Check je stooklijn in LG controller (moet geijkt zijn)
- Verhoog k_factor om hogere temperaturen te ontmoedigen

### Issue: Huis wordt niet warm genoeg
**Symptoom**: Binnentemperatuur daalt ondanks optimalisatie
**Oorzaak**: Te agressieve optimalisatie of verkeerd energielabel
**Oplossing**:
- Verhoog `heat_curve_max` naar 50°C of 55°C
- Check of energielabel correct is ingesteld
- Verhoog cop_compensation_factor naar 0.95 (minder agressieve optimalisatie)

### Issue: Optimizer kiest altijd voor huidige tijd
**Symptoom**: Geen verschuiving naar goedkopere uren
**Oorzaak**: Prijs forecast niet beschikbaar of thermal storage te laag
**Oplossing**:
- Check of elektriciteitsprijs sensor forecast attributen heeft
- Verhoog thermal_storage_efficiency (code aanpassing nodig)

## Technische Achtergrond: COP Formule

De optimizer gebruikt deze formule voor COP berekening:

```
COP = (base_cop + α × T_outdoor - k × (T_supply - 35)) × f × defrost_factor

Waarbij:
- base_cop = 4.2 (COP bij A7/W35)
- α = 0.08 (outdoor_temp_coefficient)
- k = 0.030 (k_factor)
- T_outdoor = Buitentemperatuur (°C)
- T_supply = Aanvoertemperatuur (°C)
- f = 0.90 (cop_compensation_factor)
- defrost_factor = 0.92-1.0 (automatisch berekend bij T < 5°C en hoge luchtvochtigheid)
```

**Voorbeeld**:
```
Condities: 0°C buiten, 40°C aanvoer, 85% luchtvochtigheid

COP = (4.2 + 0.08 × 0 - 0.030 × (40 - 35)) × 0.90 × 0.95
    = (4.2 + 0 - 0.15) × 0.90 × 0.95
    = 4.05 × 0.90 × 0.95
    = 3.46

Dit is realistisch voor deze condities met ontdooien.
```

## LG Controller Integratie

### Stooklijn Instelling in LG Controller

De optimizer berekent de **optimale offset**, maar je LG controller moet een correcte basisstooklijn hebben:

**Aanbevolen basis stooklijn** (zonder offset):
- **Vloerverwarming**: 0.3 - 0.4
- **Radiatoren**: 0.5 - 0.7

De offset van de optimizer (-4°C tot +4°C) wordt toegevoegd aan deze basis stooklijn.

### Home Assistant Integratie

Als je de LG ThinQ integratie gebruikt in Home Assistant, configureer deze sensoren:

```yaml
# Configuratie voor LG sensoren (voorbeeld)
sensor:
  - platform: lg_thinq
    # Gebruik deze sensoren als input voor optimizer:
    # - sensor.lg_heat_pump_supply_temperature
    # - sensor.lg_heat_pump_outdoor_temperature
    # - sensor.lg_heat_pump_power_consumption
```

## Bronnen & Referenties

- **Datasheet**: [LG Therma V HM091MR.U44 Technical Specifications](https://www.lg.com/uk/business/heating/air-to-water-heat-pumps/monobloc/hm091mr-u44/)
- **COP Rating**: 4.18 @ A7/W35 (EN14511 test conditions)
- **SCOP**: 4.45 (seasonal average, medium temperature application)
- **Energy Label**: A+++ @ 35°C, A++ @ 55°C

---

## Updates & Feedback

Heb je deze configuratie gebruikt? Help mee om deze gids te verbeteren:

1. **Deel je calibratie waarden** - Wat werkt voor jouw systeem?
2. **Meld afwijkingen** - Zien de COP berekeningen er realistisch uit?
3. **Verbeter de gids** - Mis je informatie?

Plaats je bevindingen in de [GitHub Discussions](https://github.com/bvweerd/heating_curve_optimizer/discussions) met tag `#lg-therma-v`.

---

**Laatste update**: 2025-11-17
**Model**: LG HM091MR.U44 Monoblock
**Versie**: 1.0.2
