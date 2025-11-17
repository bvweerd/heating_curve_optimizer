# Diagnostische Stappen: Heating Curve Offset blijft 0

Dit document helpt je te identificeren waarom de heating curve offset 0 blijft.

## Snelle Checks in Home Assistant

### 1. Controleer de sensor attributes
Ga naar Developer Tools → States en zoek `sensor.heating_curve_offset`.

Bekijk de attributes, specifiek:
- `future_offsets`: Dit zou een lijst moeten zijn met offset waarden
- `demand`: De voorspelde warmtevraag per tijdstap
- `prices`: De elektriciteitsprijzen per tijdstap
- `optimization_reason`: Als deze aanwezig is, staat hier waarom optimalisatie NIET plaatsvindt

### 2. Controleer de Net Heat Loss sensor
Ga naar `sensor.net_heat_loss` en controleer:
- Is de sensor beschikbaar? (niet "unavailable" of "unknown")
- Heeft de sensor een `forecast` attribute?
- Zijn er positieve waarden in de forecast?

**Voorbeeld forecast attribute:**
```json
"forecast": [1.5, 2.3, 1.8, 2.0, 1.7, 1.9]
```

### 3. Controleer de prijssensor
Ga naar je elektriciteitsprijs sensor (bijvoorbeeld `sensor.nordpool_kwh_nl_eur_3_10_025`) en controleer:
- Is de sensor beschikbaar?
- Heeft de sensor forecast data in één van deze attributes:
  - `raw_today` / `raw_tomorrow`
  - `forecast_prices`
  - `net_prices_today` / `net_prices_tomorrow`

### 4. Controleer heating curve instellingen
Ga naar Settings → Devices & Services → Heating Curve Optimizer → Configure

Controleer:
- **Heat Curve Min** (standaard 28°C): Minimum aanvoertemperatuur
- **Heat Curve Max** (standaard 45°C): Maximum aanvoertemperatuur
- **Min Outdoor Temperature** (standaard -20°C)
- **Max Outdoor Temperature** (standaard 15°C)

**Belangrijke check:** Als de huidige buitentemperatuur en heating curve resulteren in een base_temp die weinig ruimte laat voor offset, wordt optimalisatie overgeslagen.

**Voorbeeld probleem:**
- Buitentemperatuur: 10°C
- Base supply temp: 32°C (berekend via heating curve)
- Heat Curve Min: 28°C → offset kan maximaal -4°C zijn (32 - 4 = 28°C)
- Heat Curve Max: 35°C → offset kan maximaal +3°C zijn (32 + 3 = 35°C)

Als de range te klein is (≤ 1 offset mogelijkheid), wordt optimalisatie overgeslagen.

## Mogelijke Oorzaken en Oplossingen

### Oorzaak 1: Negatieve of nulle warmtevraag
**Symptoom:** `optimization_reason` bevat "totale warmtevraag in het venster is niet positief"

**Mogelijke redenen:**
- Het is te warm buiten → geen verwarming nodig
- Solar gain compenseert alle warmteverliezen
- Verkeerde configuratie van oppervlak of energy label

**Oplossing:**
- Dit is normaal gedrag op warme dagen
- Controleer `sensor.net_heat_loss` - deze zou positief moeten zijn als er verwarmingsvraag is
- Controleer `sensor.heat_loss` en `sensor.window_solar_gain` individueel

### Oorzaak 2: Geen forecast beschikbaar
**Symptoom:** Sensor wordt "unavailable" of heeft lege `demand` in attributes

**Mogelijke redenen:**
- `sensor.net_heat_loss` heeft geen forecast attribute
- `sensor.outdoor_temperature` heeft geen forecast data

**Oplossing:**
1. Controleer of `sensor.outdoor_temperature` forecast data heeft (dit komt van open-meteo.com API)
2. Herstart de integratie: Settings → Devices & Services → Heating Curve Optimizer → … → Reload
3. Check logs: Settings → System → Logs, zoek naar "heating_curve_optimizer"

### Oorzaak 3: Stooklijn te restrictief
**Symptoom:** `optimization_reason` bevat "ingestelde stooklijn laat geen afwijking toe"

**Mogelijke redenen:**
- Heat Curve Min en Max staan te dicht bij elkaar
- Huidige base_temp ligt aan de rand van de toegestane range

**Oplossing:**
1. Verhoog de range tussen Heat Curve Min en Max:
   - Bijvoorbeeld: Min = 25°C, Max = 50°C (in plaats van 28-45°C)
2. Pas outdoor temperature range aan als deze niet klopt voor je klimaat
3. Herstart de integratie na aanpassing

### Oorzaak 4: Alle prijzen zijn gelijk
**Symptoom:** `optimization_reason` niet aanwezig, maar offset blijft 0

**Mogelijke redenen:**
- Elektriciteitsprijs heeft geen variatie (alle uren zelfde prijs)
- Prijssensor heeft geen forecast data

**Oplossing:**
- Controleer of je prijssensor daadwerkelijk variabele prijzen heeft
- Voor NordPool: Zorg dat je een dynamische prijs sensor gebruikt
- Als prijzen niet variëren, heeft optimalisatie geen zin (elke offset heeft zelfde kosten)

### Oorzaak 5: Planning window te kort
**Symptoom:** Alleen 1 of 2 waarden in `future_offsets`

**Mogelijke redenen:**
- Planning window configuratie is te kort
- Time base configuratie komt niet overeen met forecast data

**Oplossing:**
1. Check configuratie: planning_window_hours (standaard 6 uur)
2. Check configuratie: time_base_minutes (standaard 60 minuten)
3. Herstart integratie na aanpassing

## Debug Logging Inschakelen

Voor gedetailleerde diagnose, schakel debug logging in:

1. Voeg toe aan `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.heating_curve_optimizer: debug
```

2. Herstart Home Assistant

3. Bekijk logs: Settings → System → Logs

Zoek naar:
- "Offset sensor demand=" - toont de demand forecast en prijzen
- "Calculated offsets=" - toont de berekende offsets
- "Geen optimalisatie:" - toont waarom optimalisatie overgeslagen wordt
- "tijd basis" warnings - geeft aan als forecast intervals niet matchen

## Veelvoorkomende Log Berichten

### Normale werking:
```
Offset sensor demand=[1.5, 2.3, 1.8, 2.0] consumption_prices=[0.25, 0.30, 0.28, 0.26] ...
Calculated offsets=[2, 1, 0, -1] buffer_evolution=[0.0, 0.0, 0.0, 0.0]
```

### Geen warmtevraag:
```
Offset sensor demand=[0.0, 0.0, 0.0, 0.0] ...
Geen optimalisatie: de totale warmtevraag in het venster is niet positief.
```

### Sensor unavailable:
```
OutdoorTemperatureSensor niet beschikbaar: geen geldige temperatuur data ontvangen van API
```

### Forecast mismatch:
```
net_heat: forecast uses 30 min steps (expected 60)
```

## Contact en Support

Als je na deze stappen nog steeds problemen hebt:

1. Exporteer diagnostics: Settings → Devices & Services → Heating Curve Optimizer → … → Download Diagnostics
2. Maak een GitHub issue: https://github.com/bvweerd/heating_curve_optimizer/issues
3. Voeg toe:
   - Diagnostics export (JSON)
   - Relevante log excerpts (met debug level)
   - Je configuratie (area, energy label, sensors, etc.)
