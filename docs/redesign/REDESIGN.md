# Redesign Heating Curve Optimizer

> Ontwerpdocument. Referentie-architectuur: `bvweerd/battery_controller` (v2.0.0, quality_scale
> platinum). Dit document beschrijft wat er mis is met het huidige ontwerp, hoe het eruit zou
> zien als we opnieuw zouden beginnen, en in welke volgorde we daar komen zonder de
> bestaande HACS-gebruikers te breken.

> **Implementatiestatus (2026-09):** fase 0 t/m 5 uit §4 zijn gebouwd, getest en op `main`
> uitrolbaar. `select.control_mode` (de tijdelijke schakelaar tussen het oude en het nieuwe
> algoritme) is inmiddels weer verwijderd: de nieuwe thermal optimizer is de enige motor,
> `optimizer.py`/`select.py`/de schaduw-sensoren zijn weg, en een mislukte cyclus faalt nu
> zichtbaar in plaats van terug te vallen op het oude algoritme. Details en concrete
> afwijkingen van dit plan staan in `docs/algorithm/redesign-thermal-model.md` en
> `quality_scale.yaml`. Fase 6 (afronding) is in uitvoering; twee onderdelen zijn bewust
> uitgesteld met reden: `realtime_controller.py` (ontbrekende live PV/net-meting) en
> subentries voor meerdere zones (zie §5).

---

## 1. Samenvatting

De kern van het probleem is niet de code-organisatie maar het **fysische model**. De huidige
optimizer beslist over een `offset` (−4…+4 °C) die wél de COP (en dus de kosten) beïnvloedt,
maar **niet de geleverde warmte**. De buffer groeit ondertussen met
`offset × demand × 0.15` zonder dat die energie ergens betaald wordt. Er wordt dus energie uit
het niets gemaakt, en het echte mechanisme van prijssturing — *nu niet stoken, straks
inhalen* — kan het model helemaal niet uitdrukken.

battery_controller heeft dit probleem al opgelost, voor precies hetzelfde wiskundige
probleem: een buffer met verliezen, een variabele conversie-efficiëntie, variabele prijzen en
een eindhorizon. Het gebouw *is* een accu. De redesign bestaat er in de kern uit dat we die
1-op-1 mapping expliciet maken.

Daarnaast is er een flinke achterstand op infrastructuur (runtime_data, migraties,
strings.json, quality_scale, coverage, CI) die in battery_controller al is ingelopen en hier
grotendeels kan worden overgenomen.

---

## 2. Diagnose van het huidige ontwerp

### 2.1 Fysica en DP-correctheid (blokkerend)

**A. De energiebalans klopt niet.** In `optimizer.py` is de stapkost:

```python
effective_demand = max(float(demand[t]), 0.0)
step_cost = effective_demand * step_hours * prices[t] / cop
```

De geleverde warmte is altijd exact `demand[t]`, ongeacht de offset. De offset werkt alleen via
de COP-noemer. Tegelijk verandert de buffer met:

```python
buffer_kwh = prev_buffer + off * demand[t] * DEFAULT_THERMAL_STORAGE_EFFICIENCY * step_hours
```

De buffer wint dus energie die in de kostenfunctie nooit is ingekocht. Omgekeerd levert een
negatieve offset "gratis" bufferonttrekking op terwijl de volle vraag toch betaald wordt. Kosten
en energie zijn twee losse boekhoudingen die elkaar niet raken. Het gevolg in de praktijk: de
optimizer kan alleen de ~10 % COP-variatie over het offsetbereik uitbuiten, niet de veel grotere
winst van *lastverschuiving*.

**B. De buffer is een DP-payload, geen toestandsdimensie.** De tabel is
`dp[t][offset][cumulative_sum] -> (cost, prev_offset, prev_sum, buffer)`. Van twee paden die in
dezelfde toestand uitkomen overleeft alleen de goedkoopste — óók als die een lagere buffer heeft
en verderop tegen `max_buffer_debt` aanloopt, terwijl het duurdere pad wel haalbaar was. Dat
breekt het optimaliteitsprincipe van Bellman: de toekomstige haalbaarheid hangt af van de buffer,
dus de buffer *moet* in de toestand zitten. battery_controller doet dit goed: SoC is de
toestandsdimensie, met `SOC_RESOLUTION_WH = 10`.

**C. `cumulative_offset_sum` is een dode dimensie.** `new_sum` wordt bijgehouden en in de
toestandssleutel gebruikt, maar nergens gelezen — niet in de kosten, niet in een constraint. Het
vermenigvuldigt de toestandsruimte zonder iets te doen.

**D. `allowed_offsets` wordt globaal over de hele horizon bepaald:**

```python
if all(water_min <= base_temps[t] + o <= water_max for t in range(horizon)):
```

Eén koud uur in het venster schrapt het hele positieve bereik voor álle uren. Is de lijst leeg,
dan komt er stilletjes een reeks nullen terug. Dit hoort een constraint per stap te zijn.

**E. Voorwaartse inductie met een ad-hoc eindstraf** (`buffer_penalty_weight = 0.01`) in plaats
van een echte terminale waardefunctie. Daardoor is het gedrag aan het einde van de horizon
willekeurig, en de eenheid van die 0.01 is niets. battery_controller gebruikt
`V[T][s] = -(soc_kwh × feed_in_price_T)` — een economisch betekenisvolle restwaarde — en werkt
achterwaarts.

**F. De horizon van 6 uur is te kort** voor gebouwmassa. Een woning met een tijdconstante van
20–50 uur moet over minstens 24, liefst 48 uur worden geoptimaliseerd, anders is het dal van
vanavond niet zichtbaar.

**G. De PV-forecast wordt berekend maar niet geprijsd.** Warmte die uit eigen PV-overschot komt
kost de *terugleverprijs* (gederfde opbrengst), niet de afnameprijs. In battery_controller is dit
juist het punt waar de meeste winst zit, inclusief de expliciete waarschuwing in CLAUDE.md dat
nooit `None` teruggegeven mag worden voor de feed-in prijs. Hier ontbreekt het concept.

**H. Een integer offset in stappen van 1 °C is een ongelukkige beslissingsvariabele.** De offset
is een *uitvoer* (het verschil tussen gekozen en nominale aanvoertemperatuur), geen natuurlijke
keuze. De natuurlijke keuze is de aanvoertemperatuur zelf, of het thermisch vermogen.

### 2.2 Home Assistant-architectuur

| Bevinding | Huidig | battery_controller |
|---|---|---|
| Runtime-opslag | `hass.data[DOMAIN][entry_id]` dict | `entry.runtime_data` met `@dataclass BatteryControllerData` |
| Gedeelde number-state | `hass.data[DOMAIN]["runtime"][CONF_KEY]` — **niet per entry gekeyed**; twee config entries overschrijven elkaars target-temperatuur | per-entry, via options + runtime_data |
| Unload-opruiming | pop't `entry.entry_id` uit `runtime`, maar de keys zijn `CONF_*` — die tak draait leeg | n.v.t. |
| Migraties | geen `async_migrate_entry` | aanwezig, met `test_migration.py` |
| Opties wijzigen | altijd volledige reload | `_NO_RELOAD_KEYS` voor tuning-waarden |
| Eerste run | `asyncio.sleep(5)` in een losse task | `async_config_entry_first_refresh()` |
| `strings.json` | ontbreekt (HA vereist het) | aanwezig, kopie van `en.json` |
| `icons.json`, brands | ontbreken | aanwezig |
| `quality_scale.yaml` | ontbreekt; manifest claimt `silver` | aanwezig, onderbouwd, `platinum` |
| `requirements` | `["aiohttp"]` — aiohttp is een core-dependency | `[]` |
| Versie | manifest `1.0.2`, `sw_version="2.0.0"` hardcoded in `__init__.py` | uit manifest gelezen bij import |
| Services | geen | 3 reset-services + `services.yaml` |
| Basis state_class | `SensorStateClass.TOTAL` voor álle sensors in `entity.py` — fout voor temperatuur/COP | per sensor gezet |

### 2.3 Tests en tooling

- `setup.cfg` meet coverage over **`tests/`** in plaats van over de integratie. Het getal is
  betekenisloos. battery_controller heeft dit expliciet gecorrigeerd, mét `fail_under = 90`.
- `--maxfail=1` verbergt alles achter de eerste fout. battery_controller heeft dit bewust
  verwijderd en legt in een comment uit waarom.
- Geen `filterwarnings = error:coroutine .* was never awaited` — een niet-awaited coroutine is
  daar een stille no-op geweest, hier zou dat net zo goed kunnen.
- mypy staat op `python_version = 3.13` zonder `strict = true`; geen mypy-stap in CI.
- Geen `conftest.py`/`harness.py`, geen brute-force cross-check, geen simulator.
- Losse rommel in de repo-root: `test_defrost_standalone.py`, `coverage.json`, `history.csv`,
  `ANALYSIS.md`, `ALGORITHM_ANALYSIS.md`.

### 2.4 Wat wél goed is en blijft

- De cascade Weather → Heat → Optimization is dezelfde vorm als battery_controller en klopt.
- `extract_price_forecast_with_interval` met intervaldetectie (15/30/60 min) is goed gedacht.
- Defrost-modellering (`calculate_defrost_factor`) is een echte toevoeging die battery_controller
  niet nodig heeft; die blijft.
- Het gebouwmodel-invoerpad (energielabel → U-waarde, glasoppervlak per oriëntatie,
  ventilatietype) is een bruikbare eerste schatting voor een koude start.

---

## 3. Het herontwerp

### 3.1 De centrale herformulering: het gebouw is de accu

| battery_controller | heating_curve_optimizer (nieuw) |
|---|---|
| SoC (Wh), resolutie 10 Wh | binnentemperatuur `T_in` (°C), resolutie 0.05–0.1 °C |
| laad-/ontlaadvermogen (W), stap 100 W | thermisch vermogen warmtepomp `Q_hp` (kW), of aanvoertemperatuur |
| RTE, gesplitst als √RTE per richting | `COP(T_aanvoer, T_buiten, defrost)` |
| max laad/ontlaad bij SoC | modulatiebereik warmtepomp + curvegrenzen |
| degradatiekosten per cyclus | comfortstraf + compressor-cyclingstraf |
| netprijs / terugleverprijs | idem |
| DC-gekoppelde PV (97 %) | PV-overschot → warmte (zelfconsumptie tegen terugleverprijs) |
| `V[T][s] = -(soc × feed_in_T)` | restwaarde van opgeslagen warmte boven de comfortondergrens |
| schaduwprijs `λ = -dV[0]/dSoC` | marginale waarde van opgeslagen warmte (€/kWh-thermisch) |
| `zero_grid_controller` (~5 s) | realtime-lus: PV-overschot in het gebouw dumpen |
| SoC-sensor faalt → laatste bekende | binnentemperatuursensor faalt → RC-model extrapoleert |

Zodra dit expliciet is, verdwijnen de problemen uit §2.1 vanzelf: energie is behouden omdat de
toestandsovergang een fysische balans is, de buffer is een echte toestandsdimensie, en
lastverschuiving is de natuurlijke uitkomst in plaats van een niet-uitdrukbare wens.

### 3.2 De nieuwe optimizer

**Toestand:** `(t, T_in)` — de binnentemperatuur, gediscretiseerd over de comfortband plus een
marge, typisch `[T_min − 1.5, T_max + 1.5]` in stappen van 0.1 °C (≈60 toestanden).

**Actie:** aanvoertemperatuur `T_sup ∈ [curve_min, curve_max]` in stappen van 0.5 °C, afgekapt op
wat de warmtepomp bij die buitentemperatuur kan leveren. De gepubliceerde `offset` is de
*afgeleide*: `offset = T_sup_gekozen − T_sup_curve(T_buiten)`.

**Overgang (1R1C, uitbreidbaar naar 2R2C):**

```
Q_hp    = emitter_vermogen(T_sup, T_in)          # afgifte-karakteristiek radiator/vloer
Q_gain  = Q_zon + Q_intern
Q_loss  = UA · (T_in − T_buiten)
T_in[t+1] = T_in[t] + (Q_hp + Q_gain − Q_loss) · Δt / C
```

`UA` (W/K) en `C` (kWh/K) zijn de twee parameters die de kalibratie leert (§3.5).

**Kosten per stap:**

```
P_elek      = Q_hp / COP(T_sup, T_buiten, defrost)
pv_dekking  = min(P_elek, PV_overschot[t])
kosten      = (P_elek − pv_dekking) · prijs_afname[t] · Δt
            + pv_dekking · prijs_teruglever[t] · Δt        # gederfde opbrengst
            + comfortstraf(T_in[t+1])                       # kwadratisch buiten de band
            + cyclingstraf(Q_hp[t], Q_hp[t−1])              # aan/uit en grote sprongen
```

**Oplossing:** achterwaartse inductie, `V[t][s] = min_a (stapkosten + V[t+1][s'])`, met

```
V[T][s] = -(T_in[s] − T_min_comfort) · C · gemiddelde_prijs_T / COP_ref
```

zodat warmte die aan het eind van de horizon in het gebouw zit een echte waarde heeft en het
model niet in het laatste uur leegloopt. Daarna een voorwaartse pass die het plan uitleest, en
een schaduwprijs `λ = -dV[0]/dT_in` die de realtime-lus gebruikt.

**Horizon:** 48 uur op 60 min, of 24 uur op 15 min. Toestandsruimte ≈ 48 × 60 × 50 acties ≈
144 k transities — ruim binnen wat battery_controller al in een executor draait.

**Constraints per stap, niet globaal:** curvegrenzen, modulatiebereik, en een harde
comfortondergrens waaronder geen enkele toestand toegestaan is.

### 3.3 Modulestructuur

Voorstel: **terug naar platte modules per platform**, zoals battery_controller, en de fysica in
eigen modules. De `sensor/weather/…`, `sensor/heat/…`-boom was een reactie op het oude
3485-regels-bestand; battery_controller laat zien dat één `sensor.py` met een gedeelde basisklasse
en korte subklassen prima werkt (1125 regels, 28 klassen). Voor één maintainer die tussen twee
repo's heen en weer springt weegt consistentie zwaarder dan de submapstructuur. Dit is een
aanbeveling, geen voorwaarde — als je de boom wilt houden, blijft de rest van dit plan geldig.

```
custom_components/heating_curve_optimizer/
├── __init__.py                 # runtime_data dataclass, services, migratie-reexport
├── const.py                    # config keys, resoluties, defaults
├── building_model.py           # ≈ battery_model.py — RC-model, UA/C, comfortband, emitterkromme
├── heatpump_model.py           # ≈ efficiency_curve.py — COP, modulatie, defrost, DHW-blokkade
├── optimizer.py                # backward-induction DP over T_in
├── coordinator.py              # dunne re-export (zoals battery_controller's 14-regelige versie)
├── coordinator_weather.py      # open-meteo: temperatuur, straling, vochtigheid, wind
├── coordinator_forecast.py     # warmteverlies, zonwinst, PV, prijsmodel
├── coordinator_optimization.py # DP-aansturing, toestandsbehoud, schaduwprijs
├── realtime_controller.py      # ≈ zero_grid_controller.py — snelle lus, PV-overschot → warmte
├── calibration.py              # ≈ efficiency_calibration.py — UA/C/COP leren, Store-backed
├── helpers.py                  # prijsextractie + resampling (porten uit battery_controller)
├── config_flow.py              # sections + subentries
├── sensor.py / number.py / select.py / switch.py / binary_sensor.py
├── climate.py                  # optioneel, zie §3.6
├── diagnostics.py / services.yaml / strings.json / icons.json
└── translations/{en,nl}.json
```

**Subentries** (het patroon dat battery_controller voor accu's en PV-strings gebruikt):
- *verwarmingscircuit / zone* — eigen curve, oppervlak, emittertype, eigen binnentemperatuursensor;
- *PV-array* — oriëntatie, kWp, tilt (nu nog platte `CONF_PV_EAST_WP`-velden).

Dat maakt meerdere zones mogelijk zonder het hoofdschema op te blazen, en het geeft per zone een
eigen HA-device.

### 3.4 Helpers delen met battery_controller

`helpers.py` van battery_controller is 1077 regels en bevat de rijpere versie van wat hier in 280
regels staat: prijsextractie met tijdstempels, verstreken perioden weggooien, eenheidsschaal
(ct/kWh vs €/kWh), `resample_forecast`/`resample_to_steps`, zonnestand en POA-instraling. Die code
hoort niet twee keer te bestaan.

Twee opties, in volgorde van voorkeur:
1. **Kopiëren met bronvermelding** en de twee bestanden bewust gelijk houden (dezelfde discipline
   als de "drie DP-implementaties synchroon"-regel in battery_controller's CLAUDE.md). Geen
   nieuwe dependency, HACS blijft simpel.
2. Een gedeeld PyPI-pakket `hass-energy-helpers`. Netter, maar het breekt
   `dependency-transparency` (extra `requirements`) en geeft twee release-cycli. Alleen doen als
   er een derde integratie komt.

Aanbeveling: optie 1, met een `# Ported from battery_controller/helpers.py @ <sha>`-kop en een
test die de beide versies op dezelfde fixtures vergelijkt zodra ze uit elkaar dreigen te lopen.

### 3.5 Kalibratie — de grootste functionele winst

Vandaag komt `UA` uit een energielabel-tabel en `C` bestaat niet; `DEFAULT_THERMAL_STORAGE_EFFICIENCY
= 0.15` is een verzonnen getal. `calibration_sensor.py` (727 regels) geeft vooral tekstadvies.

battery_controller doet het anders: het *meet* de laad-/ontlaadefficiëntie uit energietellers,
bewaart de correctie in `.storage`, toont aantal samples en laatste resultaat als sensor, en biedt
reset-services omdat een slechte kalibratie anders alleen met de hand uit `.storage` te krijgen is.

Dezelfde aanpak, drie parameters:

| Parameter | Bron | Methode |
|---|---|---|
| `UA` (W/K) | recorder-historie | regressie van geleverde warmte tegen `T_in − T_buiten` in stationaire perioden (weinig zon, constante aanvoer) |
| `C` (kWh/K) | afkoelcurves | exponentiële fit op `T_in(t)` wanneer de warmtepomp uit staat en de zon weg is: `τ = C/UA` |
| COP-correctie | elektriciteits- vs. warmtemeter | verhouding gemeten/voorspelde COP per bucket van (`T_aanvoer`, `T_buiten`) |
| zonwinstfactor | residu op zonnige dagen | schaalt het glasmodel bij |

Elk met: sample-telling, betrouwbaarheidsindicatie, `applied`-vlag met drempel, `Store`-persistentie,
een resetservice en een diagnostische sensor. Dit is wat de integratie van "aardige schatting" naar
"past zich aan jouw huis aan" brengt.

### 3.6 Bedieningsoppervlak

Dezelfde filosofie als battery_controller: **de integratie publiceert setpoints, de gebruiker
automatiseert het wegschrijven.** Geen directe service-calls naar de warmtepomp — te veel merken,
te veel manieren om iets te slopen.

- `select.control_mode`: `off` | `follow_curve` (pure doorgeef, meetbaar als nulmeting) |
  `optimize` | `comfort_priority`
- `switch.optimization_enabled`, `switch.pv_dump_enabled`
- `number.manual_offset`, `number.target_temperature`, `number.comfort_band_*`
- `sensor.optimal_supply_temperature` (het eigenlijke setpoint), `sensor.heating_curve_offset`,
  `sensor.shadow_price`, `sensor.planned_schedule` (attribuut met het hele plan),
  `sensor.optimization_status` (met `last_failure_reason`)
- `climate`-entiteit: optioneel, maar wél de HA-native vorm. Doel-temperatuur, huidige
  temperatuur, presets (`eco`/`comfort`/`boost`). Aanbeveling: erbij, maar pas in fase 4 en
  standaard uitgeschakeld tot het gedrag bewezen is.
- Services: `set_offset`, `recalculate`, `reset_calibration` (per parameter).

**Realtime-lus** (`realtime_controller.py`, ~30 s in plaats van battery_controller's ~5 s, want
thermische traagheid): als er PV-overschot is en de schaduwprijs zegt dat opslaan loont, verhoog
de aanvoertemperatuur tot het overschot opgegeten is; met deadband tegen pendelen.

### 3.7 Kwaliteit en tooling — parity met battery_controller

- `entry.runtime_data` met een `HeatingOptimizerData`-dataclass; alle gedeelde state per entry.
- `async_migrate_entry` + `test_migration.py`; config-versie 1 → 2 voor de nieuwe sleutels.
- `strings.json` (kopie van `en.json`), `icons.json`, brands, `quality_scale.yaml` met
  onderbouwing per regel.
- `manifest.json`: `requirements: []`, versie als enige bron van waarheid (lezen bij import,
  zoals battery_controller doet).
- `setup.cfg`: coverage over de integratie, `fail_under = 90`, `--maxfail=1` eruit,
  `filterwarnings = error:coroutine .* was never awaited`, `mypy strict = true` op 3.14.
- CI overnemen: lint+mypy-job, testmatrix 3.13/3.14, coverage-samenvatting in de job summary,
  wekelijkse scheduled run tegen een ongepinde `pytest-homeassistant-custom-component` (zodat een
  HA-release die de integratie breekt zichtbaar wordt vóór de volgende issue).
- `.claude/rules/{architecture,testing,code-style}.md` + skills + session-start hook overnemen.
- Repo-root opruimen: `test_defrost_standalone.py` → `tests/`, `coverage.json`/`history.csv` weg,
  `ANALYSIS.md`/`ALGORITHM_ANALYSIS.md` → `docs/`.

### 3.8 Teststrategie

Wat battery_controller heeft en hier ontbreekt, en wat het nieuwe model nodig heeft:

1. **Energiebehoud als property-test**: over een willekeurig plan moet gelden
   `Σ(Q_hp + Q_gain − Q_loss)·Δt ≈ (T_in[T] − T_in[0])·C`. Precies de bug uit §2.1A die dit had
   gevangen.
2. **DP vs. brute force** op korte horizon (3–4 stappen, grove discretisatie) — mirror van
   `simulate/brute_force_step0.py`. De enige echte controle op een DP.
3. **Monotoniciteitstests**: hogere prijs in uur *k* mag het geplande vermogen in uur *k* nooit
   verhogen; hogere buitentemperatuur mag de totale kosten nooit verhogen.
4. **Terminale-waardetest**: geen leegloop in de laatste stappen.
5. **Comfort-nooit-geschonden-test**: de harde ondergrens wordt in geen enkel scenario doorbroken.
6. **Kalibratietests** met gesimuleerde historie: het model moet een bekende `UA`/`C` terugvinden.
7. `conftest.py` + `harness.py` met fixtures, snapshottests op diagnostics.

### 3.9 Docs en simulator

battery_controller heeft `docs/analyzer/` — een JS-herimplementatie van de DP met een
browser-UI, jest-tests en een `test_cross_impl.py` die Python en JS op dezelfde invoer vergelijkt.
Dat is echte onderhoudslast (de CLAUDE.md-regel "drie DP-implementaties synchroon houden"), maar
het is ook het enige gereedschap waarmee je een gebruikersmelding kunt naspelen zonder hun HA.

Aanbeveling: **ja, maar pas in fase 5**, en dan meteen mét de cross-impl-test — een tweede
implementatie zonder die test is een tweede bron van bugs. `docs/algorithm.md` schrijven we wel
meteen in fase 1, samen met het model.

---

## 4. Fasering

De integratie draait bij gebruikers. Daarom in-place op een branch, met configmigratie, en met een
fase waarin oud en nieuw naast elkaar draaien.

| Fase | Inhoud | Risico | Gebruiker merkt |
|---|---|---|---|
| **0. Fundament** | `runtime_data`, migratiehandler, `strings.json`, `icons.json`, `quality_scale.yaml`, `requirements: []`, versie uit manifest, per-entry runtime-state (de bug uit §2.2), setup.cfg + CI + `.claude/rules` overnemen, repo-root opruimen | laag | niets |
| **1. Fysica** | `building_model.py`, `heatpump_model.py`, nieuwe `optimizer.py` (backward induction over `T_in`), `docs/algorithm.md`. Pure Python, volledig offline getest. Oude optimizer blijft staan | laag — niets aangesloten | niets |
| **2. Shadow mode** | Coordinator-split (`coordinator_weather/forecast/optimization`), nieuwe optimizer draait mee en publiceert als *diagnostische* sensor; oude blijft sturen. Vergelijkingssensor: verwachte besparing nieuw vs. oud | midden | extra diagnostische entiteiten |
| **3. Omschakeling** | `select.control_mode` met `follow_curve`/`optimize`, nieuwe optimizer wordt de bron van `optimal_supply_temperature`; oude code weg | hoog — hier zit de gedragswijziging | ander stookgedrag; `follow_curve` als terugvaloptie |
| **4. Kalibratie** | `calibration.py` met `UA`/`C`/COP-leren, Store-persistentie, resetservices, kalibratiesensoren. `calibration_sensor.py` vervangen | midden | model past zich aan het huis aan |
| **5. Oppervlak** | Subentries (zones, PV-arrays), `climate.py`, `realtime_controller.py`, PV-dump | midden | nieuwe entiteiten, configmigratie |
| **6. Afronding** | `docs/analyzer/` + jest + cross-impl-test, `simulate/`, volledige docs, quality_scale naar platinum, bump naar 3.0.0 | laag | documentatie |

Fase 0 en 1 zijn onafhankelijk en kunnen parallel. Fase 2 is de belangrijkste: daar zie je op
echte data of het nieuwe model beter is, vóórdat het iets aanstuurt.

---

## 5. Open beslissingen

Deze moeten vóór fase 1 vastliggen, want ze bepalen de vorm van het model:

1. **Modelorde.** 1R1C (één binnentemperatuur) is genoeg voor radiatoren; vloerverwarming heeft
   echt een tweede capaciteit (dekvloer) nodig, anders is de vertraging van 2–4 uur niet
   gemodelleerd. Voorstel: 1R1C in fase 1, 2R2C als optie in fase 4 zodra de kalibratie de extra
   parameters kan schatten.
2. **Toestandsdiscretisatie.** 0.1 °C over 6 °C bandbreedte = 60 toestanden. Fijner kan, maar
   battery_controller laat zien dat de randgevallen (boundary actions voor restcapaciteit)
   belangrijker zijn dan de resolutie.
3. **Wel of geen `climate`-entiteit.** Native HA-integratie versus het risico dat gebruikers hem
   als thermostaat gaan gebruiken terwijl de echte thermostaat er ook nog is.
4. **Platte modules of de `sensor/`-boom houden.**
5. **Helpers kopiëren of een gedeeld pakket** (§3.4).
6. **Analyzer erbij of niet** — echte waarde, echte onderhoudslast.
7. **Warm tapwater.** Nu volledig afwezig, terwijl een tapwatercyclus de aanvoertemperatuur
   kaapt en de COP verpest. Minimaal: een blokkade-invoer (binary_sensor) zodat de optimizer weet
   dat die uren niet van hem zijn.

---

## 6. Wat we concreet overnemen uit battery_controller

Directe ports, weinig denkwerk:
- `helpers.py` — prijsextractie, tijdstempels, eenheidsschaal, resampling, zonnestand/POA
- `__init__.py` — `runtime_data`-dataclass, `_NO_RELOAD_KEYS`, serviceregistratie, versie uit manifest
- `config_flow.py` — `section()`-patroon, subentry-flows, migratiehandler
- `efficiency_calibration.py` — de hele vorm: `Store`, samples, `applied`-drempel, reset, sensor
- `zero_grid_controller.py` — deadband + modusresolutie als blauwdruk voor de realtime-lus
- `setup.cfg`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `quality_scale.yaml`
- `.claude/rules/`, skills, session-start hook
- `tests/conftest.py`, `harness.py`, `test_migration.py`, `test_cross_impl.py`

Patronen, geen code:
- achterwaartse inductie met terminale waardefunctie
- schaduwprijs als koppeling tussen planning en realtime
- kalibratie met persistentie én escape hatch
- "publiceer setpoints, stuur niets rechtstreeks aan"
- elke niet-triviale keuze als comment mét de reden erbij (zoals in hun `setup.cfg` en `ci.yml`)
