# VRDE 180 hp UAV engine — public-source dossier

Compiled 2026-09-15. Every entry carries the provenance vocabulary from
`docs/ENGINEERING-STANDARDS.md` §1, plus one extra marker this dossier needs:

- `published_comparable_engine` — a real, primary-source figure for a
  **different** engine of the same class. Usable to check model *form* and to
  set limits where VRDE publishes none. **Never quote it as VRDE data.**
- `unverified_community` — a claim from a named public source that is not
  DRDO/VRDE/Jayem and has not been confirmed by them. Adopted only where noted,
  and must be labelled on any slide.

## 1. Target engine — VRDE / Jayem 180 hp

| Item | Value | Provenance | Source |
|---|---|---|---|
| Take-off power hold | 180 hp constant to 11,000 ft | published | [DRDO product page][drdo] |
| High-altitude validation | Leh and Chang La, to 17,664 ft (power not published) | published | [DRDO][drdo] |
| Test hours | 1,100+ h on test profiles; dyno + thrust cradle for power, thrust, fuel consumption | published | [DRDO][drdo] |
| Beta design validation | 650 h completed in 2024; integrated on R-II AF-9 airframe | published (secondary press) | [Kodainya][kod], [X/ragebait][x1] |
| FADEC | Indigenous, dual-redundant | published | [DRDO][drdo], [idrw AeroDef 2026][idrw] |
| Architecture | 4-stroke, inline-4, diesel, turbocharged, common-rail DI | published | [idrw][idrw], [India's Defence 2015][eoi] |
| Displacement | 2.2 L | published | [idrw][idrw], [Indian Defence News 2025][idn] |
| Accessories | Propeller governor, 28 V self-starter, 5.6 kW alternator | published (2015 EOI spec) | [India's Defence 2015][eoi] |
| Gearbox | Integrated propeller reduction gearbox (ratio not published) | published | [idrw][idrw] |
| Fuel | Heavy fuel; Jet A-1 compatible. EOI: ATF K-50 primary, diesel secondary | published | [idrw][idrw], [eoi] |
| Operating ceiling | 32,000 ft | published (secondary press) | [Indian Defence News][idn] |
| Endurance class | 24–30 h missions | published (secondary press) | [idrw][idrw] |
| Variant | 220 hp prototype in development | published (secondary press) | [idrw][idrw] |
| Platforms | TAPAS-BH-201 (Rustom-II), Archer-NG; replaces imported Austro E4 / Rotax | published (secondary press) | [idrw][idrw], [TAPAS news][tapas] |
| Base block | Mahindra mHawk 2.2 (2179 cc), FADEC from Park Controls | **unverified_community** | [X/hukum2082][x2] |
| Dry weight | 195 kg | **unverified_community** | [X/hukum2082][x2] |
| Cooling | Liquid (the automotive mHawk base and every same-class aero-diesel are liquid-cooled; no source states otherwise) | derived | [mHawk][mhawk], [NZ CAA TAR][nz] |

### 1.1 VRDE's own programme requirement (2015 EOI)

VRDE Ahmednagar's Expression of Interest for joint development of a 200 hp
aero-diesel. **A requirement, not a measured result** — but it is DRDO's own
statement of the power-lapse the engine was designed to, which makes it the
best public calibration target that exists. Source: [India's Defence, May 2015][eoi];
EOI document listing on [Scribd][eoi-scribd].

| Altitude | Power band | Provenance |
|---:|---|---|
| Sea level | 195–205 hp | published (requirement) |
| 10,000 ft | 195–205 hp | published (requirement) |
| 20,000 ft | 145–155 hp | published (requirement) |
| 30,000 ft | 105–115 hp | published (requirement) |
| SFC, sea level | 210 g/kWh | published (requirement) |
| Installed weight | ~250 kg | published (requirement) |

### 1.2 VRDE's own modelling paper

Kamran H., Radhakrishna D., *Prediction of High Altitude Performance for UAV
Engine*, SAE Technical Paper 2015-26-0207. [SAE listing][sae]. Paywalled;
being obtained. If obtained, its data outranks every comparable-engine figure
below. D. Radhakrishna is also the named technical contact on the 2015 EOI.

## 2. Base automotive block — Mahindra mHawk 2.2 (adoption: unverified_community)

| Item | Value | Provenance | Source |
|---|---|---|---|
| Displacement | 2179 cc, inline-4, DI | published (automotive) | [mHawk museum page][mhawk] |
| Induction | Turbocharged and intercooled | published (automotive) | [mhawk] |
| Injection | Bosch common rail, multi-hole solenoid injectors | published (automotive) | [mhawk], [search summary][mhawk2] |
| Compression ratio | 15.5:1 (automotive tune; aero value unknown) | published (automotive) | [mhawk2] |
| Automotive ratings | 120 hp @ 4000 rpm / 140 hp @ 3750 rpm; 320 Nm 1500–2800 rpm | published (automotive) | [mhawk] |
| Bore / stroke | **Not found in any public source consulted** | — | stays `assumed` |

## 3. Comparable engine — Austro Engine E4 / E4P (AE300 / AE330)

The engine the VRDE unit replaces on TAPAS. Same class: automotive-derived
(Mercedes OM640) 2 L common-rail turbo-diesel, liquid-cooled, integrated
gearbox, dual-channel EECU. Primary sources: [EASA TCDS E.200 Issue 12][tcds],
[Austro factsheet 05/2024][fact], [NZ CAA TAR 20/21B/17][nz].
**All entries are `published_comparable_engine`.**

| Item | E4 (AE300) | E4P (AE330) |
|---|---|---|
| Displacement | 1991 cm³ | 1991 cm³ |
| Gearbox reduction | 1 : 1.69 | 1 : 1.69 |
| Dry weight | 185 kg (TCDS) / 186 kg (factsheet) | same |
| Take-off power | 123.5 kW @ 3880 rpm (2300 prop) | 132 kW @ 3880 rpm (2300 prop) |
| Max continuous | 123.5 kW @ 3880 rpm | 126 kW @ 3720 rpm (2200 prop) |
| Max torque | 512 Nm | 550 Nm |
| Overspeed | 4220 crank rpm (2500 prop) | same |
| Fuel flow | 35 L/h @100 %, 19 L/h @60 % | 39 L/h @100 %, 21 L/h @60 % |
| Oil temp | min 50 °C; normal 50–135 °C; max 140 °C | min 50; max 139 °C |
| Coolant temp | opens-up min 60 °C; max 105 °C | min 60; max 100 °C |
| Thermostat stages (factsheet/MM) | small circuit < 80 °C; mixed 80–95 °C; radiator > 95 °C | same |
| Gearbox temp | max 120 °C | max 120 °C |
| Fuel pressure, HP-pump inlet | 4–7 bar | 4–7 bar |
| Oil pressure | ≥ 0.9 bar idle; ≥ 2.5 bar MCP; ≤ 6.5 bar | same |
| Turbo containment speed | 172,000 rpm | 178,000 rpm |
| Max operating altitude | 18,000 ft | 20,000 ft |
| Min fuel temp (jet fuel) | −30 °C | −30 °C |
| Prop-governor drive | CCW, 2680 rpm @ 3880 crank, 40 Nm | same |
| Control | Dual-channel EECU, DO-178B level C, DO-160D tested | same |
| Construction | Cast-iron crankcase; aluminium gearbox and valve-train housing | same |
| Special condition | CS-E 40(d): flame-out after prolonged idle descent at low OAT, masked by windmilling; in-flight relight envelope required | E4P |
| Power increase E4→E4P | EECU software: higher manifold pressure and injection rate, plus gearbox-oil cooling | — |

## 4. Comparable engine — TEI PD180ST (secondary)

2.1 L inline-4 common-rail single-stage turbo FADEC; 182 hp max / 172 hp
continuous; 220 g/kWh SL BSFC; 170 hp @ 20,000 ft, 115 hp @ 30,000 ft, 75 hp
@ 40,000 ft. `published_comparable_engine`. [TEI][tei]. Kept as a secondary
lapse-shape check; the VRDE EOI curve (§1.1) supersedes it as the primary
altitude target.

## 5. Sensor-installation references

| Item | Value | Provenance | Source |
|---|---|---|---|
| EGT probe station | In each exhaust pipe, ~4–6 in (100–150 mm) from the cylinder head | published (GA installation practice) | [JPI install manual][jpi] |
| Diesel EGT probe τ | 350 ms (industrial diesel probe), 1200 ms (aviation EGT-DP series) | published (vendor) | [Sensor Connection][sc] |
| CHT probe | Spring-loaded bayonet Type K / J into head thermowell | published (GA practice) | [JPI][jpi-cht] |
| Junction trade-off | Grounded: faster response, more noise pickup; ungrounded: slower, isolated | published (vendor) | [sc] |

## 6. What is NOT publicly available (do not invent)

VRDE bore/stroke, compression ratio of the aero build, gearbox ratio, rated
crank rpm, turbocharger model or map, intercooler size, rail pressure, coolant
and oil limits, sensor list, any dyno trace, power at 17,664 ft, BSFC map.
Where the twin needs these it uses the comparable engine (§3) labelled as
such, or an `assumed` value with a reason.

[drdo]: https://drdo.gov.in/drdo/en/offerings/products/180-hp-diesel-engine-uav
[idrw]: https://idrw.org/jayem-automotives-unveils-indigenous-180hp-male-uav-engine-at-aerodef-india-2026-to-replace-imported-powerplants/
[idn]: https://www.indiandefensenews.in/2025/02/tapas-uav-to-get-new-vrade-jayem.html
[eoi]: http://indias-defence.blogspot.com/2015/05/vrde-developing-aero-diesel-engine-for.html
[eoi-scribd]: https://www.scribd.com/document/522022152/11052015-EOI
[sae]: https://www.sae.org/papers/prediction-high-altitude-performance-uav-engine-2015-26-0207/
[kod]: https://www.kodainya.com/blogs/tapas-bh-201-the-rustom2
[x1]: https://x.com/ragebaitop/status/1990052781705826648
[x2]: https://x.com/hukum2082/status/1899311334338273285
[tapas]: https://www.defenceguru.co.in/news/tapas-bh-201-uav-to-begin-flight-trials-with-indigenous-engine/
[mhawk]: https://mahindramuseum.in/2023/04/02/mhawk-diesel-engine/
[mhawk2]: https://bostonjaptech.com/product/mahindra-scorpio-bolero-mhawk-2-2-turbo-diesel-engine-gmd/
[tcds]: https://www.easa.europa.eu/en/downloads/7617/en
[fact]: https://www.diamondaircraft.com/fileadmin/diamondaircraft/products/austro-engine/AE_E4_Series_Factsheet_screen.pdf
[nz]: https://www.aviation.govt.nz/assets/aircraft/type-acceptance-reports/Austro-Engine-E4.pdf
[tei]: https://www.tei.com.tr/en/products/tei-pd180st-turbodiesel-aviation-engine
[jpi]: https://www.jpinstruments.com/wp-content/uploads/2012/08/SCANNER_INSTALL.pdf
[jpi-cht]: https://www.mynewsdesk.com/us/j-p-instruments/pressreleases/types-of-cht-probes-used-in-aircraft-2887566
[sc]: https://thesensorconnection.com/products/diesel-egt-probes
