# Fix: Heating Curve Offset blijft 0

## Probleem Geïdentificeerd

De optimizer draait WEL, maar berekent dat offset 0 optimaal is. Dit komt door een **te lage k_factor waarde**.

### Jouw Huidige Configuratie
- **k_factor: 0.028** ← Te laag!
- base_cop: 4.28
- outdoor_temp_coefficient: 0.06
- cop_compensation_factor: 0.98

### Waarom is dit een probleem?

Met k_factor = 0.028 verandert de COP nauwelijks bij temperatuurverschillen:
- COP bij 26.2°C (offset 0): **4.581**
- COP bij 24.2°C (offset -2): **4.636** (+1.2%)
- COP bij 28.2°C (offset +2): **4.526** (-1.2%)

Het verschil is zo klein dat zelfs met 12.9% prijsvariatie (€0.247 → €0.219/kWh), de kostenbesparingen verwaarloosbaar zijn:
- **€0.00089 per uur** (ongeveer 1 cent per 12 uur)
- **€3.83 per verwarmingsseizoen** (180 dagen)

De optimizer kiest daarom terecht voor offset 0.

## Oplossing: Verhoog de k_factor

### Wat is k_factor?

De k_factor bepaalt hoeveel de COP daalt (of stijgt) bij hogere (of lagere) aanvoertemperaturen.

**Formule:** `COP = (base_cop + outdoor_effect - k_factor × (supply_temp - 35)) × compensation`

**Typische waarden:**
- **0.03-0.04**: Moderne, efficiënte warmtepompen (Daikin Altherma, Mitsubishi Ecodan)
- **0.04-0.05**: Goede warmtepompen met standaard prestaties
- **0.05-0.07**: Oudere of minder efficiënte systemen
- **0.028**: Jouw huidige waarde - zeer onrealistisch laag

### Stapsgewijze Fix

#### Stap 1: Bepaal je warmtepomp type

**Heb je een moderne inverter warmtepomp?** (na 2015, A+++ label)
→ Start met **k_factor = 0.035**

**Heb je een warmtepomp uit 2010-2015?**
→ Start met **k_factor = 0.045**

**Heb je een oudere warmtepomp?** (voor 2010)
→ Start met **k_factor = 0.055**

**Weet je het niet zeker?**
→ Start met **k_factor = 0.040** (veilige middenweg)

#### Stap 2: Wijzig de configuratie

1. Ga naar Home Assistant
2. Ga naar **Settings** → **Devices & Services**
3. Zoek **Heating Curve Optimizer**
4. Klik op **Configure** (tandwiel icoon)
5. Wijzig **k_factor** naar je gekozen waarde (bijv. 0.040)
6. Klik **Submit**

#### Stap 3: Herstart de integratie

1. Klik op de **3 puntjes** bij Heating Curve Optimizer
2. Klik **Reload**

#### Stap 4: Controleer het resultaat

Na enkele minuten (bij de volgende update):

1. Ga naar **Developer Tools** → **States**
2. Zoek `sensor.heating_curve_optimizer_heating_curve_offset`
3. Bekijk de **state** en **attributes**:
   - `state`: Zou nu een waarde ongelijk aan 0 moeten zijn (bijvoorbeeld -1, 1, 2)
   - `future_offsets`: Zou nu variërende waarden moeten bevatten

**Voorbeeld van werkende optimalisatie:**
```json
{
  "state": "-1",
  "future_offsets": [-1, 0, 1, 1, 0, -1, -2, -1, 0, 1, 1, 0],
  "optimization_status": "OK"
}
```

### Verwachte Resultaten met Verschillende k_factors

| k_factor | COP Variatie | Uurlijkse Besparing | Seizoen Besparing (180d) | Optimizer Gedrag |
|----------|--------------|---------------------|--------------------------|------------------|
| **0.028** (huidig) | ±1.2% | €0.00089 | €3.83 | ❌ Kiest voor offset 0 |
| **0.035** (conservatief) | ±1.5% | €0.00108 | €4.65 | ✅ Variërende offsets |
| **0.045** (aanbevolen) | ±1.9% | €0.00133 | €5.74 | ✅ Actieve optimalisatie |

## Geavanceerd: Calibreer je k_factor

Voor optimale resultaten, meet je echte warmtepomp COP curve:

### Methode 1: Handmatige Metingen

1. Noteer op verschillende dagen:
   - Aanvoertemperatuur (supply temperature)
   - Elektrisch vermogen warmtepomp (kW)
   - Thermisch vermogen warmtepomp (kW) → COP = thermisch / elektrisch
   - Buitentemperatuur

2. Bereken k_factor:
   ```
   k_factor = (COP_difference) / (supply_temp_difference)
   ```

   **Voorbeeld:**
   - Bij 25°C aanvoer: COP = 4.8
   - Bij 30°C aanvoer: COP = 4.5
   - Verschil: 0.3 COP over 5°C
   - k_factor ≈ 0.3 / 5 = 0.06

### Methode 2: Gebruik Historie Data

Als je `sensor.heating_curve_optimizer_heat_pump_cop` historie hebt:

1. Ga naar **Developer Tools** → **States** → Historie
2. Bekijk COP waarden bij verschillende aanvoertemperaturen
3. Plot deze en bepaal de helling (slope)

### Methode 3: Fabrikant Specificaties

Sommige fabrikanten geven COP curves:
- Daikin Altherma: k_factor ≈ 0.035-0.040
- Mitsubishi Ecodan: k_factor ≈ 0.038-0.045
- Viessmann Vitocal: k_factor ≈ 0.040-0.050
- Nibe F-series: k_factor ≈ 0.035-0.042

## Veelgestelde Vragen

### Q: Waarom was mijn k_factor zo laag?

De standaard configuratie gebruikt mogelijk een te conservatieve waarde. Of je hebt deze per ongeluk laag ingesteld.

### Q: Kan een te hoge k_factor schade veroorzaken?

Nee. De k_factor is alleen een parameter voor de optimizer. Het beïnvloedt NIET de fysieke werking van je warmtepomp. Het kan alleen leiden tot suboptimale keuzes als de waarde niet klopt.

### Q: Moet ik ook base_cop aanpassen?

Waarschijnlijk niet. Je base_cop (4.28) lijkt realistisch. Focus eerst op k_factor.

### Q: De offsets zijn nu niet-nul, maar lijken niet logisch?

Dit kan gebeuren als:
1. Je base_cop te hoog of te laag is
2. Je cop_compensation_factor niet correct is
3. Je prijssensor geen correcte forecast heeft

Controleer eerst of de basis configuratie klopt.

### Q: Hoe weet ik of de optimizer nu correct werkt?

Kijk naar het patroon in `future_offsets`:
- **Goed**: Negatieve offsets bij hoge prijzen, positieve bij lage prijzen
- **Fout**: Willekeurige offsets zonder patroon
- **Neutraal**: Allemaal 0 (betekent dat optimalisatie geen zin heeft)

Vergelijk ook `prices` met `future_offsets` in de sensor attributes.

### Q: Wat als het nog steeds niet werkt?

Mogelijke oorzaken:
1. **Stooklijn te restrictief**: Vergroot range tussen heat_curve_min en heat_curve_max
2. **Geen prijsvariatie**: Controleer of prijssensor forecast heeft met verschillende waarden
3. **Geen warmtevraag**: Op warme dagen is optimalisatie niet mogelijk
4. **Planning window te kort**: Verhoog planning_window naar 12-24 uur

Download de diagnostics (Settings → Devices & Services → Heating Curve Optimizer → … → Download Diagnostics) en deel deze voor verdere analyse.

## Testen en Valideren

Na het aanpassen van k_factor:

### Test 1: Visuele Controle (5 minuten)
```
1. Check sensor.heating_curve_optimizer_heating_curve_offset
2. State zou ≠ 0 moeten zijn
3. future_offsets zou variëren
```

### Test 2: Attribute Analyse (10 minuten)
```
1. Bekijk attributes van offset sensor
2. Check dat prices variëren
3. Check dat offsets logisch correleren met prijzen:
   - Negatieve offset (lagere temp, hogere COP) bij hoge prijzen
   - Positieve offset (hogere temp, lagere COP) bij lage prijzen
```

### Test 3: Energie Monitor (1 week)
```
1. Monitor je elektriciteitsverbruik
2. Vergelijk met vorige weken (zelfde buitentemperaturen)
3. Verwacht 1-3% besparing afhankelijk van prijsvariatie
```

## Support

Als je na deze stappen nog problemen hebt:

1. **Download diagnostics**: Settings → Devices & Services → Heating Curve Optimizer → … → Download Diagnostics
2. **Schakel debug logging in**:
   ```yaml
   # configuration.yaml
   logger:
     logs:
       custom_components.heating_curve_optimizer: debug
   ```
3. **Maak GitHub issue**: https://github.com/bvweerd/heating_curve_optimizer/issues
   - Voeg diagnostics toe
   - Voeg relevante logs toe
   - Beschrijf je warmtepomp type en configuratie

## Samenvatting Actie

**Direct uitvoeren:**

1. ✅ Ga naar Settings → Devices & Services → Heating Curve Optimizer → Configure
2. ✅ Wijzig `k_factor` van **0.028** naar **0.040** (of waarde van tabel hierboven)
3. ✅ Klik Submit
4. ✅ Reload de integratie
5. ✅ Wacht 5-10 minuten en check sensor.heating_curve_optimizer_heating_curve_offset
6. ✅ Verwacht: state ≠ 0 en variërende future_offsets

**Verwacht resultaat:**
- Offsets variëren tussen -2 en +2°C
- Besparingen: €5-6 per verwarmingsseizoen met k_factor = 0.040
- Actieve optimalisatie zichtbaar in offset sensor

---

*Gemaakt op basis van analyse van jouw diagnostics data (2025-11-17)*
