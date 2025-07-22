# heatpump-optimizer

De heatpump optimizer stuurt de warmtepomp aan op basis van de warmteverlies van je woning en de energieprijzen van een dynamisch energiecontract. 

De instellingen van de sensoren is hetzelfde als bij het referentiemateriaal van de dynamic energy contract calculator. De gas sensoren en alle prijscalculaties van die integratie vervallen. De keuze voor de dynamische elektriciteitsprijs moet behouden blijven.

Er moet aan de instellingen een groep toegevoegd worden met de verwachte opbrengst van de zonnepanelen. Er kunnen net als bij de energiesensoren meerdere zonnepanelen sets worden gekozen. Je kunt kiezen uit de entiteiten van de integratie Forecast.Solar.

Ook moet je een buitentemperatuur sensor kunnen kiezen en een supply temperatuur.

In de map referentiemateriaal staan formules voor de COP vs buitentemperatuur en supply temperatuur. neem deze grafieken op en maak de correctiefactor beschikbaar als instelling

Dan moet er nog een input komen voor het warmteverlies van de woning. dit is op basis van het rappport in het referentiemateriaal. Maak hiervoor ook de juiste inputs voor aan.

Op basis van bovenstaand materiaal kan je een optimalisatieberekening maken die een nieuwe output berekent. Analyseer of dit moet met een MIP model, of dat er een ander algoritme aan te bevelen is. Je maakt dus gebruik van de warmtebuffer van de woning om eerder of later warmte toe te voegen aan de woning. Je hebt hierbij een instelbare planningshorizon van bijvoorbeeld 6...24 uur (instelbaar) en afhankelijk van de informatie die je uit de externe sensoren kunt halen.


De output is een sensor met de verschuiving van de stooklijn van de warmtepomp om de kosten van het energieverbruik zo laag mogelijk te houden.

Genereer de nieuwe custom component in de map custom_components/dynamic-energy-heatpump-optimizer. gebruik het voorbeeld uit het referentiemateriaal voor de basis.

## Huidige aanpak

De berekende verschuiving wordt bepaald met een lichtgewicht heuristiek. Een
volwaardig mixed integer programming model is voor dit doel overbodig. De
sensor kijkt naar de stroomprijs en verwachte zonneproductie voor de komende
uren in combinatie met COP- en warmteverliesdata. Vervolgens wordt het meest
voordelige uur gekozen en wordt de stooklijn dienovereenkomstig verschoven.