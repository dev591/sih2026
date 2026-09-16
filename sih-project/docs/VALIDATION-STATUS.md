# Validation status — public evidence only

Last updated: 2026-09-15 (see the section at the end for the drivetrain update;
the tables directly below are the 2026-09-12 baseline, kept for comparison)

## What is directly validated for the target engine

The target is the DRDO/VRDE 180 hp UAV aero-diesel. DRDO publicly states that
it produces 180 hp take-off power constant through 11,000 ft, was tested for
power, thrust and fuel consumption on a dynamometer/thrust cradle, and was
validated at Leh and Changla to 17,664 ft. [DRDO product page](https://drdo.gov.in/drdo/en/offerings/products/180-hp-diesel-engine-uav)

| VRDE public anchor | Current twin result | Status |
|---|---:|---|
| 180 hp at sea level | 156.2 hp | -13.2%; calibration gap |
| 180 hp at 11,000 ft | 143.9 hp | -20.1%; calibration gap |
| Validated at 17,664 ft | 106.9 hp at ISA+20 scenario | Operating point only: DRDO does not publish power there |

These are intentionally not hidden behind a pass/fail gate. The public source
does not include a dyno trace, test uncertainty, ambient conditions, or a
17,664-ft power value, so an acceptance tolerance would be invented.

## Public comparable-engine check

TEI's PD180ST is a public comparator, **not a source of VRDE measurements**:
it is a 2.1 L inline-four, common-rail, single-stage-turbo, FADEC aviation
diesel rated 182 hp maximum / 172 hp continuous. TEI publishes 220 g/kWh
sea-level BSFC and altitude power of 170 hp at 20,000 ft, 115 hp at 30,000 ft,
and 75 hp at 40,000 ft. [TEI PD180ST specifications](https://www.tei.com.tr/en/products/tei-pd180st-turbodiesel-aviation-engine)

This checks whether the model's power-lapse behaviour is in the right broad
engineering regime. It cannot calibrate the VRDE model; only VRDE test-cell or
flight data can do that.

| Altitude | Twin power | TEI power | Twin / TEI | Twin lapse from 20k | TEI lapse from 20k |
|---:|---:|---:|---:|---:|---:|
| 20,000 ft | 109.3 hp | 170 hp | 64.3% | 100.0% | 100.0% |
| 30,000 ft | 77.9 hp | 115 hp | 67.8% | 71.3% | 67.6% |
| 40,000 ft | 49.1 hp | 75 hp | 65.5% | 45.0% | 44.1% |

Interpretation: the absolute power level needs target-engine calibration, but
the normalized high-altitude lapse closely follows this comparable public
turbo-diesel. This is evidence for the model's altitude-response *form*, not
evidence that it has been calibrated to VRDE.

## Reproduce

```bash
cd /Volumes/dev/sih2026/sih-project/backend
source .venv/bin/activate
python validate_vrde.py
python validate_rotax.py
```

## Cross-engine transfer result

The same MVEM, measurement, and parity code is loaded with the Rotax 914
profile—no engine-specific code branch. The sea-level take-off validation is
85.65 kW at 5,673 rpm against the published 84.5 kW / 5,800 rpm rating
(+1.4% power, -2.2% speed). CHT is 124.9 C against the 135 C EASA limit, oil
temperature is 106.5 C against 130 C, and oil pressure is within the published
2–5 bar running range. `validate_rotax.py` also proves the healthy parity
vector remains centred at a cross-engine 10,000-ft / 72% operating point.
That profile records a fixed commissioning baseline for the repeatable healthy
probe/model offsets; it is subtracted before sigma scoring and is not adapted
online, so fault departures remain observable.

For the presentation, say: *“We use DRDO's published VRDE anchors as target
evidence. Where detailed VRDE data is unavailable, a clearly labelled public
comparable engine checks model form—not the target-engine calibration.”*

## Scope decisions for competition day (2026-09-12)

Deliberate, low-risk calls made this session — recorded so the next person
doesn't reopen them under time pressure:

- **Gate 6 stays a shape check** (`gates_check.py`, "flat then falls"), not a
  strict comparison against the DRDO 180 hp anchor. The twin is honestly
  13-20% low against that anchor (table above) because no real VRDE dyno
  trace, test uncertainty band, or ambient conditions are public — turning
  Gate 6 into a strict pass/fail against that anchor would fail the gate for
  a reason unfixable without data DRDO hasn't published, days before a
  competition. The gap itself is not hidden: it's the table above, and is the
  correct honest answer if a judge asks "does it match the real engine
  exactly."
- **Rotax cross-engine validation is kept exactly as committed and is not
  being extended.** It is genuine supporting evidence (same MVEM/measurement/
  parity code, no engine-specific branch, +1.4%/-2.2% agreement against a
  real published rating) but is not the target engine, so no further effort
  went into digitizing its full power/torque/BSFC curves this session — there
  is no source PDF in this repo to digitize from, and finding a scanned
  original online was not pursued further once the target-engine (VRDE) work
  was prioritized as lower-risk to finish first.
- **A real digitized VRDE-specific compressor map was not pursued.** No
  public turbocharger part number or map is attached to VRDE's own
  publications; `config/compressor_map.csv` remains the existing
  analytical Ellipse-model surrogate, already labelled `provenance: assumed`
  per `ENGINEERING-STANDARDS.md` §1. Do not silently upgrade its label without
  an actual sourced map.

Net effect: all three verification suites remain green (`test_sensors.py`
11/11, `verify.py` all passed, `gates_check.py` 6/6) and nothing in this
session's scope review changed a single line of physics or gate logic.

## 2026-09-15 — geometry, gearbox, constant-speed propeller (`drdo-fidelity`)

Sources for every new number: `docs/research/VRDE-PUBLIC-DOSSIER.md`. New
anchors added to `validate_vrde.py`: VRDE's own 2015 EOI power-lapse
requirement (compared as lapse — it was a 200 hp programme) and the Austro
E4P/AE330 type certificate (a comparable engine, never quoted as VRDE data).

| Anchor | 2026-09-12 | 2026-09-15 |
|---|---:|---:|
| DRDO 180 hp, sea level | −13.2 % | **−7.7 %** |
| DRDO 180 hp, 11,000 ft | −20.1 % | **−12.5 %** |
| EOI lapse, 10,000 ft | −0.3 % outside | inside |
| EOI lapse, 20,000 ft | −1.0 % outside | inside |
| EOI lapse, 30,000 ft | −2.6 % outside | −1.6 % outside |
| Sea-level BSFC vs EOI 210 g/kWh | 178 (−15.1 %) | 181 (−13.7 %) |
| Propeller helical tip Mach | ~0.96 at SL (supersonic tip speed 328 m/s) | 0.68 SL / 0.69 cruise / 0.79 at 32,000 ft |

What changed and why: 2179 cc (mHawk base, unverified community source);
1.69 reduction gearbox and 3880/2300 rpm (AE330 comparable); constant-speed
propeller with a PI governor (VRDE's spec lists a governor); airspeed, prop
speed, blade angle and gearbox oil became sensed channels; the wastegate gained
integral action (P-only sat 33–48 mbar above its own target everywhere).

**Still open, stated plainly:**
- Power is still 8–13 % low against DRDO's 180 hp and BSFC is ~14 % too good,
  consistent with an over-efficient, air-starved model (η_i = 0.50, no
  intercooler). Phase 3 (intercooler, liquid cooling) and Phase 6 (calibration)
  address it; no constant was tuned to close it here.
- **ρ₅ is weaker on a constant-speed propeller.** Its propeller-dynamometer
  path depends on blade angle (−5.7 % of ρ₅ per 0.3°) and airspeed (+4.3 % per
  m/s). With commissioning calibration of both, a 60 % friction fault is
  1.46σ per sample and **3.03σ on the 32-sample detector window — marginal**.
  ρ₁₀ (oil pressure) is now the primary friction channel; ρ₅ corroborates.
- Rotax 914 transfer validation is bit-identical to 2026-09-12.

Suites: `test_sensors.py` 12/12, `verify.py` 5/5 (new drivetrain test),
`gates_check.py` 6/6, sigma regenerated, live websocket frame healthy.

## 2026-09-16 — liquid cooling loop and intercooler (Phase 3)

The engine is an automotive-derived liquid-cooled diesel, but the twin held
coolant temperature FIXED and used air-cooled fin area. Replaced with a real
loop: coolant state, thermostat (80/95 °C stages, AE300 comparable), ram-air
radiator, coolant-pump-driven head conductance (Dittus–Boelter Re^0.8), and an
air-to-air intercooler. Head-to-coolant conductance is numerically identical to
the old fin term (126 W/K), so head temperature behaviour is continuous.

| Anchor | after Phase 2 | after Phase 3 |
|---|---:|---:|
| DRDO 180 hp, sea level | −7.7 % | −7.7 % |
| DRDO 180 hp, 11,000 ft | −12.5 % | **−7.7 %** |
| EOI lapse 10k / 20k / 30k ft | inside / inside / −1.6 % outside | **inside / inside / inside** |
| Sea-level BSFC vs EOI 210 g/kWh | 181 (−13.7 %) | 181 (−13.7 %) |

**Power is now flat from sea level to 11,000 ft (166.2 hp at both)** — the
shape DRDO publishes — because the intercooler's denser charge holds mass flow
as pressure falls. Nothing was tuned to achieve it.

Cooling behaviour, measured (500 s to steady state):

| Case | Coolant | Thermostat | CHT | Charge air |
|---|---:|---:|---:|---|
| Sea-level take-off | 91.8 °C | 79 % | 171.5 °C | 117.9 → 51.0 °C |
| Hot-day take-off, ISA+20 | 99.5 °C | 100 % | 179.2 °C | 137.9 → 71.0 °C |
| 18,000 ft cruise | 87.4 °C | 49 % | 151.5 °C | 90.0 → 18.1 °C |

The radiator was re-sized (0.067 → 0.086 m²) after the first sizing — done for
an ISA-standard day — ran the coolant to 117.7 °C at ISA+20. Aircraft cooling
is sized for the hot day; the sweep behind the new value is in the profile.

**Two cooling faults now have distinct signatures**, where the old abstract
`hA_scale` had one: a fouled radiator (50 %) raises coolant 91.8 → 134.3 °C and
CHT 171.5 → 213.7 °C; a degraded coolant pump (50 %) raises CHT by 59 °C while
leaving coolant essentially unchanged.

**Open items, stated plainly:**
- **The turbo overspeed clamp is now binding at every operating point** —
  sea level through 20,000 ft, cruise included. Boost is therefore limited by an
  assumed 110,000 rpm ceiling rather than by the compressor map. Raising it to
  the published comparable containment speed (172,000 rpm) pushes critical
  altitude to ~16,000 ft against DRDO's published 11,000 ft, so compressor and
  turbine sizing must be calibrated in Phase 6 for critical altitude to emerge
  from the physics. Do not quote "critical altitude" as an emergent result until
  then.
- **Gate 6 checks MAP shape, which is a proxy.** DRDO's published claim is about
  POWER, and power is flat to 11,000 ft; manifold pressure now falls steadily
  from sea level because the intercooler trades pressure for density. Phase 6
  converts this gate to the published power bands.
- **ρ₅ no longer carries friction at 3σ** (1.42 per sample, 2.55 on the
  detector window), so Gate 4 now judges friction on **ρ₁₀ (53.7σ)** and reports
  ρ₅ as corroboration. Measured across 10–240 s settle times, σ₅ does not move,
  so this is sensor accuracy, not a warm-up transient.
- σ(ρ₁) fell 0.0085 → 0.0056: Path 2 now reads its own compressor-delivery
  sensor, so Paths 1 and 2 share no sensor at all. σ(ρ₄) rose 1.23× (the energy
  closure now uses measured coolant temperature). Others within ±5 %.
- Power still 7.7 % low and BSFC ~14 % better than the EOI requirement.
- Rotax 914 transfer remains bit-identical throughout.

Suites: `test_sensors.py` 12/12, `verify.py` 6/6 (new cooling test),
`gates_check.py` 6/6, sigma regenerated.
