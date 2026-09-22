# Het herontworpen thermisch model

Dit document beschrijft de optimizer-kern die het redesign vanaf fase 1
toevoegt: `building_model.py`, `heatpump_model.py` en `thermal_optimizer.py`.
Voor de volledige diagnose van het oude model en de fasering, zie
[`docs/redesign/REDESIGN.md`](../redesign/REDESIGN.md). Dit document behandelt
alleen het nieuwe model zelf, op het niveau van `battery_controller`'s
`docs/algorithm.md`.

## Het gebouw is de accu

| battery_controller (`battery_model.py`) | heating_curve_optimizer (nieuw) |
|---|---|
| `capacity_kwh`, SoC | `thermal_mass_kwh_per_k`, binnentemperatuur `T_in` |
| round-trip inefficiëntie | `ua_w_per_k` (warmteverlies) |
| `min_soc_percent`/`max_soc_percent` | `comfort_min`/`comfort_max` |
| laad-/ontlaadvermogen (kW) | thermisch vermogen warmtepomp (kW) |
| efficiëntiecurve | `HeatPumpConfig.cop_at()` |
| — | `EmitterConfig`: hoeveel vermogen de radiatoren/vloer kunnen leveren |

## 1R1C-toestandsovergang

`BuildingConfig.next_indoor_temp()` is de exacte tegenhanger van
`battery_model`'s SoC-overgang:

```
Q_loss   = UA * (T_in - T_buiten) / 1000          [kW]
net_kW   = Q_hp + Q_zon + Q_intern - Q_loss
T_in[t+1] = T_in[t] + net_kW * step_hours / C
```

waarbij `C` (kWh/K) de thermische massa is, geschat uit vloeroppervlak en
bouwzwaarte (`THERMAL_MASS_WH_PER_M2_K`) totdat kalibratie (fase 4) de echte
waarde leert uit afkoelcurves. `UA` (W/K) komt uit de bestaande
`calculate_htc_from_energy_label()` — ongewijzigd overgenomen uit het oude
model.

Dit is een echte energiebalans: `heat_input_kw` is exact het vermogen dat de
warmtepomp die stap levert, niet een los `offset`-effect zoals in het oude
model (zie REDESIGN.md §2.1.A). Energie is dus per constructie behouden.

## De emitter-curve: waarom offset nu een echte keuze is

In het oude model bepaalde de offset alleen de COP — de geleverde warmte
bleef altijd exact gelijk aan de vraag. `EmitterConfig` dicht dat gat:

```
Q_beschikbaar(T_aanvoer, T_in) = Q_nominaal * ((T_aanvoer - T_in) / dT_nominaal) ^ exponent
```

met `exponent ≈ 1.3` voor radiatoren (EN 442), vlakker voor vloerverwarming.
`EmitterConfig.sized_to_building()` leidt `Q_nominaal` en `dT_nominaal` af uit
de bestaande curve-instellingen: de installateur dimensioneert radiatoren op
het koudste ontwerppunt van de curve (`heat_curve_min_outdoor` →
`heat_curve_max`), dus daar moet het nominale vermogen exact het piekverlies
van het gebouw dekken. Geen nieuwe configuratie nodig.

Resultaat: een hogere offset (hogere aanvoertemperatuur) levert **meer**
vermogen tegen een **slechtere** COP; een lagere offset het omgekeerde. Dat is
precies de fysische koppeling die het oude model miste.

## De DP-formulering

Toestand aan het begin van stap *t*: `(T_in[t], offset[t-1])`. De vorige
offset zit in de toestand omdat de ramp-rate-beperking (`offset_delta_t`)
weet moet hebben van waar je vandaan komt.

```
V[t](T_in, prev_offset) = min over offset[t] van:
    stap_kosten(T_in, prev_offset, offset[t]) + V[t+1](T_in', offset[t])
```

`offset[t]` wordt de `prev_offset`-context voor `V[t+1]` — de waardefunctie is
dus op elke stap, inclusief de terminale, geïndexeerd op `(toestand,
prev_offset)`. Voor de terminale stap is die afhankelijkheid triviaal (geen
ramp-beperking meer na de horizon), dus wordt de terminale waarde simpelweg
gedupliceerd over elke `prev_offset`-rij.

**Toestandsdiscretisatie**: `T_in` wordt gediscretiseerd over
`[comfort_min - marge, comfort_max + marge]` in stappen van 0.1°C (configureerbaar).
De marge (default 1.5°C) laat kortstondige excursies buiten de comfortband toe
— en bestraft ze — in plaats van ze onrepresenteerbaar te maken.

**Curve-grenzen per stap** (fix voor REDESIGN.md §2.1.D): de aanvoertemperatuur
wordt geklemd op `[water_min, water_max]`, per tijdstap, nooit globaal over de
hele horizon. Een stap die méér vraagt dan de installatie aankan krijgt
gewoon wat het systeem kan leveren — nooit een lege actieverzameling.

**Harde ondergrens** (fix voor REDESIGN.md §2.1.D/E): een toestand onder de
bodem van de discretisatie-ladder krijgt een zeer grote maar eindige straf
(`HARD_FLOOR_PENALTY_EUR = 1000`) in plaats van een harde uitsluiting. Zo
blijft de DP-tabel altijd compleet (geen gaten door een leeg actiedomein),
terwijl een comfortschending in de praktijk nooit de goedkoopste keuze is.

**Terminale waardefunctie** (fix voor REDESIGN.md §2.1.E): opgeslagen warmte
boven `comfort_min` is aan het eind van de horizon geld waard — geanalogeerd
aan battery_controller's `V[T][s] = -(soc_kwh × feed_in_price_T)`:

```
V[horizon](T_in) = -(T_in - comfort_min) * C * prijs[laatste_stap] / COP[laatste_stap]
```

Dit voorkomt dat de optimizer de opgeslagen warmte weggeeft in de laatste
stappen puur omdat de horizon eindigt.

**PV en terugleverprijs** (fix voor REDESIGN.md §2.1.G): optioneel
`pv_surplus_kw`/`feed_in_prices`. Warmte uit eigen PV-overschot wordt geprijsd
tegen de terugleverprijs (gederfde opbrengst), niet tegen de afnameprijs. Als
er wel PV-overschot maar geen terugleverprijs-forecast is, valt het terug op
`feed_in_price_fallback` (default €0,07/kWh) — nooit stilzwijgend op `None`,
dezelfde regel als battery_controller's CLAUDE.md voor exact dezelfde reden:
een ontbrekende terugleverprijs mag zonnearbitrage niet onopgemerkt
onrendabel maken.

## Schaduwprijs

Na de achterwaartse pas is `V[0](T_in, prev_offset)` bekend voor elke
toestand. De schaduwprijs is de marginale waarde van één extra kWh opgeslagen
warmte bij `t=0`:

```
λ = -(dV[0]/dT_in) / C          [€/kWh]
```

geschat met een centraal verschil over de buren van de startstaat in de
discretisatie-ladder, bij de daadwerkelijke `current_offset`. Analoog aan
battery_controller's `λ = -dV[0]/dSoC`. Deze waarde is bedoeld voor de
realtime PV-dump-regeling in fase 5 (`realtime_controller.py`).

## Validatie

- `tests/test_building_model.py` — energiebehoud van de 1R1C-overgang
  (inclusief een meerstaps-eigenschapstest), discretisatie, emitter-fysica.
- `tests/test_heatpump_model.py` — COP-monotoniciteit, elektrisch vermogen.
- `tests/test_thermal_optimizer.py`:
  - energiebehoud end-to-end langs het gekozen pad;
  - comfortband nooit geschonden onder normale omstandigheden;
  - harde ondergrens nooit geschonden, zelfs met een te kleine warmtepomp;
  - geen leegloop aan het eind van de horizon (terminale waardefunctie);
  - voorverwarmen vóór een dure periode en uitzakken erdoorheen (het
    lastverschuivingsgedrag dat het oude model niet kon uitdrukken);
  - monotoniciteit: een hogere prijs op één stap kan het opgenomen vermogen
    op die stap nooit verhogen;
  - **brute-force cross-check**: op een horizon van 3 stappen wordt elke
    toegestane offset-reeks expliciet doorgerekend en vergeleken met het
    DP-resultaat — bevestigt dat de achterwaartse inductie het werkelijke
    globale optimum vindt, naar het voorbeeld van battery_controller's
    `simulate/brute_force_step0.py`.

## Status

Dit is fase 1 uit het redesignplan: de fysica-kern staat, is getest, maar
draait nog **niet** aangesloten op de coordinators of sensors. Fase 2
(schaduwmodus) sluit hem aan als diagnostische sensor naast de bestaande
optimizer, vóórdat hij in fase 3 daadwerkelijk stuurgedrag overneemt.
