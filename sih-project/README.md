# PRAMANA

**प्रमाण** — *the means by which a thing is known to be true*

An over-determined digital twin for aero piston engines in MALE UAVs.

**Smart India Hackathon 2026 · Problem Statement SIH26054 · DRDO · Software · Robotics & Drones**

---

## The thesis, in three sentences

Conventional engine health monitoring compares a measurement to a limit. A
digital twin compares a measurement to a physics-based prediction. **PRAMANA
compares every measurement to every other measurement** — because an internal
combustion engine is a massively over-determined system, the same physical
quantity is recoverable along several independent measurement paths, and health
is the *pattern of disagreement* between them.

Every capability here — virtual sensing, sensor-fault discrimination, fault
isolation, cross-engine transfer, and validation without ground truth — is a
consequence of that single structural fact.

---

## PS components → what we ship → who owns it

Evaluators score against a rubric derived from DRDO's lettered list. The
mapping is deliberately literal — folder names, slide headings and demo order
all follow it.

| § | DRDO asks for | We ship | Owner | Cut if short? |
|---|---|---|---|---|
| **A** | Digital twin core, synchronised with live data, modular | Mean-value engine model running in lockstep with the telemetry stream; engine parameters in a YAML profile so a second engine is a config file, not a rewrite | BE-1 | **Never** |
| **B** | Health monitoring: RPM, CHT, EGT, oil, fuel flow, vibration, battery/alternator, injection timing | Per-cylinder CHT/EGT, oil P&T, fuel flow and rail pressure, MAP, turbo speed, vibration RMS + order spectrum, bus voltage and alternator load, injection timing advance | BE-1 | **Never** |
| **C** | Fault detection: misfire, injector, lubrication, sensor drift, combustion instability, overheating, vibration | Ten-fault physics-injected library + residual signature matrix for isolation | BE-1 / BE-2 | Ship all 10 |
| **D** | AI/ML: anomaly detection, RUL, trend analysis, maintenance advice | M1 physics-guided residual estimator → M2 LSTM autoencoder → M3 classifier → two-headed RUL with uncertainty band | BE-2 | **Never** |
| **E** | Simulation & replay: mission replay, environment simulation, high-altitude and endurance scenarios | Timeline scrubber driving the whole UI; ISA atmosphere; Leh-profile and 18-hour endurance presets | Frontend | Keep replay |
| **F** | Dashboard: health, alerts, efficiency trends, advisories, mission reports | React GCS with a live interactive 3D engine that lights the faulted cylinder, plus an auto-generated post-flight PDF | Frontend | **Never** |

---

## Team

| Role | Person | Owns |
|---|---|---|
| **Frontend + team lead** | — | Layer 5 GCS, three.js engine, uPlot strips, replay scrubber. PS **E**, **F** |
| **BE-1 — physics** | — | Layers 0–1: MVEM, ISA, fault injector, CAN. PS **A**, **B**, **C** |
| **BE-2 — ML** | — | Layers 2–3: M1/M2/M3, N-CMAPSS benchmark. PS **D** |
| **R1 — research** | — | PS decode, domain research, judge Q&A sheet |
| **R2 — pitch** | — | Deck, demo script, **a fresh backup video at the end of every day** |
| **R3 — QA** | — | QA, repo/CI/`make` targets, and sole authority to reject anything not in the demo script |

R3's veto is what stops the Day-4 rebuild urge. Take it seriously.

---

## Architecture — six layers, each independently demoable

That last property matters on day five when something breaks: you want to be
able to drop a layer and still have a story.

```
LAYER 0 · TRUTH              Mean-value engine model + fault injector
                             manifold · crank · turbo · 4x thermal states
                             10 faults as parameter perturbations

LAYER 1 · TRANSPORT          SocketCAN vcan0, CANaerospace-style frames
                             CAN -> MQTT -> WebSocket
                             fast lane 20 kHz on-board · slow lane 10 Hz down

LAYER 2 · EDGE (ON THE UAV)  M1 physics-guided predictor · M2 LSTM autoencoder
                             residual generation · UKF joint state-parameter
                             vibration order features · runs at 10 Hz

LAYER 3 · GROUND ANALYTICS   M3 classifier + signature matrix
                             RUL p10/p50/p90 · damage integrator
                             fleet history in Parquet

LAYER 4 · DECISION           forward-propagate twin over remaining route
                             P(complete) · derate advisory · RTB call

LAYER 5 · GROUND CONTROL     React + three.js, interactive 3D engine
                             replay scrubber · post-flight PDF · risk map
```

**Layers 0 and 1 are the only components that know the data is simulated.**
Replace them with a physical ECU and a real datalink and nothing above them
changes. That boundary is the deployment story, and it is worth a slide of its
own.

---

## Repo layout

```
sih-project/
  config/
    engine_vrde_180.yaml       # PRIMARY — DRDO's own 180 hp aero-diesel
    engine_rotax_914.yaml      # FALLBACK — every limit citable from EASA TCDS E.122
  docs/
    spec/
      telemetry-schema.md      # FROZEN Day 0 — the three-lane contract
      residual-spec.md         # rho1-rho11, fault incidence matrix, isolability
    pitch/
      demo-script.md           # the four minutes the whole build serves
    research/                  # R1
    qa/                        # R3
  backend/                     # BE-1 physics + BE-2 ML
  frontend/                    # React GCS
```

---

## The six-day rule

Six days is a different game from a twelve-hour sprint, and the danger
inverts. In twelve hours you lose by over-scoping. In six days you lose by
**spreading** — ten things at 60% on day six and nothing that survives a full
demo run.

> ### Build the twelve-hour version first. Finish it by end of Day 2. Tag it. Record a demo video of it.
>
> Everything after that is **additive on a base that already works.**

That single decision means you can never be caught with nothing. From Day 3
onward you are choosing to improve a working system rather than praying an
unfinished one converges. The Day-2 video becomes your worst-case submission,
and every later recording replaces it.

**Day 5 pm is a hard feature freeze.** No new code after that point, no
exceptions, including the team lead's. Anything unfinished gets reverted to the
last working commit — not "quickly fixed."

**Day 6 is rehearsal only.** Run the full demo ten times, a different person
driving each time.

---

## What six days buys that twelve hours doesn't

Not more features. **Measurement.** Almost every team at this level ships a
prototype with zero numbers attached, because they ran out of time before they
could measure anything.

1. **The transfer result** — our stack running unmodified on NASA N-CMAPSS, with a score. The only hard proof the ML isn't memorising our own simulator.
2. **False alarm rate** — 100 healthy simulated flights, spurious alerts per flight hour. The first question a maintenance officer asks.
3. **The ablation** — same autoencoder on raw signals vs on residuals. One bar chart. Proves the central design claim instead of asserting it.
4. **Edge latency, measured** — a real millisecond figure kills the "can this fly?" question dead.
5. **All ten faults** — with a confusion matrix across all of them.

---

## Never cut

- The residual concept — it is the whole thesis
- The sensor-drift discrimination demo
- Per-cylinder resolution in the 3D view
- The RUL uncertainty band
- The mission decision panel
- The replay scrubber

## Cut without hesitation

- The graph neural network
- Real PDE-residual PINN training
- Any database that isn't Parquet or SQLite
- Auth, user accounts, multi-tenancy
- Docker Compose with six services
- A trained model for anything

---

## Honesty policy

Every claim in the deck carries its provenance. Engine limits are quoted from
published type-certificate data; platform figures from published DRDO and open
sources; performance figures generated from our simulation environment are
**identified as such**, every time.

> An evaluator who finds a limitation we concealed discounts everything else.
> An evaluator who finds every limitation already identified and bounded reads
> the remaining claims as reliable.

In a technical evaluation that trade is strongly favourable. Volunteering the
limits of our own data is how we stop a judge from "catching" us with it — and
it costs nothing, because the mechanism is real even where the numbers are
synthetic.
