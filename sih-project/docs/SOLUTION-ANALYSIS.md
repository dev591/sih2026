# PRAMANA — Complete Solution Analysis

**How the system works, how it would be used, and exactly what is and is not
built.**

Smart India Hackathon 2026 · Problem Statement SIH26054 · DRDO

---

## Contents

1. [The problem, precisely stated](#1-the-problem-precisely-stated)
2. [The solution mechanism, end to end](#2-the-solution-mechanism-end-to-end)
3. [Walking one fault through the whole system](#3-walking-one-fault-through-the-whole-system)
4. [Changing the engine — how portability actually works](#4-changing-the-engine--how-portability-actually-works)
5. [How DRDO would use this](#5-how-drdo-would-use-this)
6. [What is built, what is specified, what is claimed](#6-what-is-built-what-is-specified-what-is-claimed)
7. [Honest limitations](#7-honest-limitations)

---

## 1. The problem, precisely stated

An 18-hour surveillance mission, twin piston engines, 28 000 ft, and **nobody
on board**. All that reaches the ground is a stream of numbers over a link
shared with video and command traffic.

Today's answer is a **threshold monitor**: every channel has a redline, and
crossing it raises an alarm. DRDO's own problem statement says so — *"primarily
threshold-based and reactive in nature"* — which is them telling us what to
beat.

That approach fails three ways, and all three appear in normal operation:

| Failure | Why it happens |
|---|---|
| **Late by construction** | A limit is placed where damage begins, so the alarm announces damage that has *already started*. |
| **Blind to context** | 128 °C CHT is benign at max power at low level and serious at cruise at 20 000 ft. One number cannot encode both, so the limit is set for the worst case and loses sensitivity everywhere else. |
| **Cannot doubt its own input** | A limit exceedance and a failed transducer are indistinguishable to a threshold. With no crew to cross-check, a forty-dollar thermocouple aborts an eighteen-hour mission. |

There is a fourth thing worth noticing. The Rotax type certificate — the most
completely documented engine in this class — **publishes no EGT limit at all.**
EGT here is a *trend* parameter, not a *limit* parameter. A threshold system has
no way to use it. That is precisely the information a twin exists to exploit.

---

## 2. The solution mechanism, end to end

### 2.1 The one-sentence version

> A threshold system compares a sensor to a redline.
> A digital twin compares a sensor to **what physics says it should read at this
> exact operating point**.
> **The gap between those two numbers is the entire product.**

That gap is called a **residual**. Everything downstream consumes residuals and
never raw signals.

### 2.2 Why residuals, and not raw telemetry

This is the single most consequential design decision, and it is what will
separate this from most other submissions.

A neural network trained on raw sensor values learns the **flight profile**, not
the engine. Every throttle movement and every climb changes every channel, so
the model raises an alarm each time. That is the dominant failure mode of naive
implementations, and it usually surfaces on the last day.

Residuals have three properties raw telemetry does not:

1. **Nominal value is zero**
2. **Operating-point invariant by construction** — altitude, ambient and power setting are already removed by the physics
3. **Noise distribution characterised once** on healthy data and reused everywhere

So the learned components have an easy target: *healthy means near zero and
white*. The physics does the hard work of removing flight conditions; the ML
only has to notice that something is off.

### 2.3 Over-determination — the structural fact everything rests on

An instrumented engine carries **more measurements than it has independent
states**. Normally treated as a nuisance. It is the most valuable property the
system has.

Air mass flow is the most informative quantity in the engine and is almost never
instrumented. It is recoverable **four ways** that share no common failure mode:

| Path | Relation | Sensors | Model dependency |
|---|---|---|---|
| 1 — Speed–density | `ṁ = η_v · p_im·V_d·N / (R·T_im·120)` | MAP, IAT, crank speed | volumetric efficiency map |
| 2 — Compressor map | `ṁ = ṁ_corr(π_c, N_tc/√θ) · δ/√θ` | turbo speed, comp. p & T | compressor map |
| 3 — Fuel & λ | `ṁ = λ · AFR_st · ṁ_f` | injector command, UEGO | *essentially none* |
| 4 — Intake restriction | `ṁ = C_d·A·(p_us/√(R·T_us))·Ψ(r)` | orifice ΔP | discharge coefficient |

**With *n* estimates only *n−1* residuals are independent.** Claiming six from
four is double-counting, and an evaluator familiar with parity methods will
check.

> **Health is not distance from a limit. Health is the degree of mutual
> consistency among redundant estimates of the same quantity.** Degradation and
> instrumentation faults both destroy that consistency — in *different, derivable*
> patterns. The pattern identifies the cause.

### 2.4 The eleven residuals

| | Relation | What it catches |
|---|---|---|
| ρ₁ | speed-density vs compressor | air-path disagreement |
| ρ₂ | speed-density vs fuel/λ | mixture and injector condition |
| ρ₃ | speed-density vs restriction | *null where Path 4 does not exist* |
| ρ₄ | energy closure (first law) | anything that changes where fuel energy goes |
| ρ₅ | power closure via propeller | friction, bearings, lubrication |
| ρ₆–ρ₉ | per-cylinder thermal deviation from the **conditional mean** | per-cylinder localisation |
| ρ₁₀ | oil pressure model | oil system |
| ρ₁₁ | 0.5-order crank ripple | single-cylinder defects |

Two of these deserve emphasis because they cost nothing and no other team will
have them:

**ρ₅ — the propeller is a calibrated dynamometer.** Flight engines have no
torque sensor, but `P = C_P(J)·ρ·n³·D⁵` recovers shaft power from the propeller
map. It is the only channel that sees friction faults cleanly, because those
consume shaft power without touching the gas path.

**ρ₁₁ — 0.5 engine order.** In a four-stroke each cylinder fires once per 720°
of crank rotation, so any single-cylinder defect appears at *half* engine order
in the angular-velocity spectrum. Zero for a balanced engine. Recovered from the
crank sensor already fitted — no new hardware.

### 2.5 Fault isolation without a hand-authored lookup table

Because each relation is built from a known sensor subset and a known piece of
physics, the effect of any fault on any relation is **derivable**.

> **Every fault has an *odd path*: the estimate that departs while the others
> hold. Identify which path is the outlier, and with what sign, and you have
> identified the fault — without a classifier, and without ever having observed
> that fault before.**

The sharpest case: **a MAP transducer reading high corrupts Path 1 upward and
Path 2 downward simultaneously**, because the same pressure enters one as a
density and the other as a pressure ratio. No component fault can imitate that
correlated double-signature.

### 2.6 Engine fault vs sensor fault — the headline capability

- A **degrading component** perturbs several coupled relations in a physically consistent pattern.
- A **drifting transducer** perturbs only the relations that contain it. No ripple, no fuel-flow change, no energy-balance shift.

**That absence of corroboration is the evidence.** The problem statement lists
sensor drift as a fault class alongside engine faults — DRDO put it there
deliberately, and almost nobody will separate them.

### 2.7 When structure is not enough: active diagnosis

Some pairs genuinely cannot be separated from steady-state parity at low
severity — notably injector fouling on cylinder *i* against an EGT transducer
drift on cylinder *i*. Rather than guess, the system **runs an experiment**.

Command a small zero-mean fuel-trim perturbation and measure the **gain** of the
response, not its level:

```
Ĝ = cov(y_egt, u) / var(u)     ⟹    Ĝ|H₁ ≈ κ·G₀   (bad injector, attenuated)
                                     Ĝ|H₂ ≈ G₀     (bad sensor, full response)
```

**A sensor's constant bias lives entirely in the DC term and cancels identically
from the alternating component.** Level tells you nothing; gain tells you
everything. A sequential probability ratio test stops the probe as soon as a
pre-set error bound is met.

Safety-cased: within existing FADEC trim authority (±3–5%), seconds long, armed
only in the ambiguous band, inhibited on takeoff/climb/approach/single-engine,
aborted near any limit, one engine at a time.

> A monitor observes and infers. **PRAMANA, when its own structure tells it that
> observation is insufficient, designs and executes an experiment.**

### 2.8 Two channels nobody else exploits

**The second engine is a free reference.** Two nominally identical engines, same
tanks, same air, same profile, eighteen hours. `Δ = y_A − y_B` cancels
common-mode variation *exactly*, so the differential noise floor sits far below
either absolute channel.

> **Both engines drifting together is the environment or the fuel. One engine
> drifting alone is that engine.**

**The loiter orbit is a repeated calibration point.** Endurance missions are
hours of racetrack at fixed altitude and power. Each circuit returns the engine
to essentially the same operating point, so trend estimation compares like with
like instead of regressing across a varying envelope. An 18 h mission at a 20-min
orbit gives ~54 matched samples per flight, free.

### 2.9 The learning layer — three models, each with one job

| | Job | Note |
|---|---|---|
| **M1** physics-guided estimator | predict what every sensor should read, plus what cannot be measured | `ŷ = f_physics(u;θ) + g_NN(u)` — the network learns only the *discrepancy*, so it trains on little data and cannot go insane at an altitude it has not seen |
| **M2** LSTM autoencoder | flag *"this does not look healthy"* without being told the fault | eats residuals; threshold is the 99.5th percentile on held-out healthy data, never hand-picked |
| **M3** classifier + RUL | name the fault; estimate remaining life | reported alongside the incidence-matrix match — **the network gives accuracy, the matrix gives the why** |

**RUL uses two heads, always both:** a quantile network (p10/p50/p90) and a
physics head integrating damage forward. Report both, advise on the
conservative. Disagreement beyond the interval is itself surfaced as a warning.

### 2.10 The UKF — health parameters as states

Degradation parameters live *inside* the estimator's state vector as a slow
random walk:

```
θ = [ η_v_scale, η_c_scale, (hA)_scale, C_d,inj^(1..4), f_fric ]
```

Two consequences. The twin stays synchronised with an ageing engine without
manual recalibration. And — the point — **the parameter estimates are themselves
the health indicators, in physically interpretable units.**

> *"Compressor efficiency scale 0.87, down from 0.94, σ = 0.02"* is a statement a
> propulsion engineer can accept or dispute. *"Health score 0.71"* is not.

### 2.11 Knowing when it does not know

Each fault signature is a **direction** in the 11-dimensional residual space.
Project the live residual onto the span of all known faults and measure what is
left over:

```
ν = ‖ρ − proj_span(F) ρ‖ / ‖ρ‖
```

High ‖ρ‖ with high ν means something real is happening that the fault library
**cannot express** — an unmodelled failure mode, or a twin that has drifted from
the engine. The honest output is *low confidence*, not a confident wrong answer.

> A tool that never says **"I don't know"** cannot be trusted when it does
> answer.

### 2.12 From diagnosis to decision

The problem statement says *reliability **enhancement***. Detection is table
stakes.

Because the twin is a fast forward model and the estimator supplies a posterior
over degradation, the natural output is a probability: run the remaining mission
forward a few hundred times with sampled degradation states and count how many
finish within limits.

> **Continue 58% · Derate to 78% power 94% · Return to base 99%**
> *"Derating costs forty minutes on station."*

Plus a **point of no return** computed from remaining fuel and the *degraded*
BSFC, not the book figure.

**Reliability advice that ignores mission value is ignored advice.**

---

## 3. Walking one fault through the whole system

A concrete trace, which is also the demo.

| Stage | What happens |
|---|---|
| **0. Injection** | Injector discharge coefficient on cylinder 2 falls 0.045 /min. Nothing else is touched — **the fault is a parameter change, not a drawn signature.** |
| **1. Physics** | Less fuel delivered than commanded → mixture leans → measured λ rises → that cylinder burns late and puts more energy out of the port → EGT₂ rises → CHT₂ follows → torque contribution falls → 0.5-order ripple appears. **Nobody wrote "EGT goes up."** |
| **2. Residuals** | ρ₂ departs (Path 3 over-reads by exactly the commanded/delivered ratio), ρ₄ shifts, ρ₇ lifts, ρ₁₁ appears. ρ₁, ρ₅, ρ₁₀ stay flat. |
| **3. Detection** | Autoencoder reconstruction error crosses the 99.5th-percentile threshold with an N-of-M persistence rule. **Every redline is still green.** |
| **4. Ambiguity** | At low severity this is not separable from an EGT sensor drift on the same cylinder. The system **says so** and arms the probe. |
| **5. Active diagnosis** | ±4% fuel trim; gain comes back attenuated (κ ≈ 0.62) rather than full. Injector, not sensor. |
| **6. Isolation** | *"Injector fouling, cylinder 2, 93%"* — classifier and incidence matrix agreeing. Explain drawer shows which residuals moved and against which derived signature. |
| **7. Cross-check** | Engine B is flying the same profile through the same air. Δ on EGT₂ survives the subtraction; everything common cancels. **It is engine A.** |
| **8. Health state** | UKF reports `C_d,inj cyl2 = 0.91 ± 0.03` — a physical number, not a score. |
| **9. RUL** | Physics head 4.1 h, network head 3.7 h, band 2.4–4.1 h. Advise on 3.7. |
| **10. Decision** | Continue 58% / derate 94% / RTB 99%, derate costs 40 min on station, PNR marker slides. |
| **11. Contrast** | The threshold panel has said `ALL PARAMETERS GREEN` throughout. That is the argument. |

---

## 4. Changing the engine — how portability actually works

**Question: if DRDO wants to run this on a different engine, can they?**

**Yes, and it is a configuration change.** This matters enough that it is
enforced in the code rather than merely asserted in a diagram.

### 4.1 Where engine knowledge lives

```
sih-project/config/engine_vrde_180.yaml     ← primary: DRDO's own 180 hp aero-diesel
sih-project/config/engine_rotax_914.yaml    ← fallback: every limit citable from EASA TCDS E.122
sih-project/frontend/src/config/engines.ts  ← the same profiles for the UI
```

A profile carries geometry (cylinders, displacement, bore, stroke, gear ratio),
ratings, **limits**, fuel properties, nominal efficiencies, MVEM parameters,
propeller data, environment presets — and one block that matters more than it
looks:

```yaml
parity_paths:
  speed_density: true
  compressor_map: true
  fuel_lambda: true
  intake_restriction: false      # unthrottled FADEC diesel — no metering restriction
  independent_residuals: 2       # n-1, with n=3 available paths
```

### 4.2 What a profile change actually changes

Switching engines in the running dashboard re-derives, live:

- cylinder count, displacement, geometry
- **every redline on the threshold panel**
- critical altitude, and therefore the whole mission-risk argument
- fuel LHV and stoichiometric AFR
- nominal efficiencies and boost ratio
- the colour ramps in the 3D view (a Rotax redlines at 135 °C, the VRDE at 200 — a fixed anchor would make one of them permanently look hot)
- **which parity paths exist, and therefore how many independent residuals there are**

That last one is the interesting one. The VRDE is an unthrottled FADEC diesel:
no metering restriction, so **Path 4 does not exist** and ρ₃ is reported as
`null` — never fabricated. The Rotax is throttled, so Path 4 *is* available, ρ₃
is live, and the isolability analysis legitimately differs.

The header states it explicitly, and it changes when you switch:

```
VRDE 180 hp Aero-Diesel · 3 parity paths → 2 independent air-path residuals
Rotax 914 F/UL          · 4 parity paths → 3 independent air-path residuals
```

### 4.3 Every number carries its provenance

```yaml
limits:
  provenance: published
  source: "EASA TCDS E.122"
  cht_C: 135.0
```

Three markers: `published` (citable), `derived` (computed from published), and
`assumed` (our engineering estimate). **Anything marked `assumed` that reaches a
slide must be labelled as such out loud.** This is the cheapest way to keep an
evaluator's trust for the claims that actually matter.

### 4.4 What a third engine would cost

Write a profile. That is the intended answer, and it is true for everything
above. What it does **not** cover:

- a **compressor map** and **propeller C_P map** for the new machine — data files, not code, but they must exist
- a **volumetric efficiency map** if the new engine's breathing differs substantially
- the **health manifold** needs roughly ten healthy operating points from the new engine to remove a first-order bias (§7)

So: *a new engine of the same architecture is a config file plus three maps and
about ten healthy samples.* Not "free", and we should not say free — but it is
not a rewrite, and a run-to-failure campaign is not required.

---

## 5. How DRDO would use this

### 5.1 It does not go on a flying aircraft, and we should never imply it does

The full staircase is in `docs/research/deployment-path.md`. In short:

1. **Dynamometer with seeded faults** — where the synthetic-data argument stops being an argument and becomes a measurement
2. **Shadow mode on recorded flight data** — diagnoses nobody acts on, compared against what maintenance actually found
3. **On the airframe, read-only**
4. **Only then, anything that commands**

### 5.2 The distinction that governs everything

| | What it takes to field |
|---|---|
| **A monitor that advises a human** | Hard but ordinary. If wrong, a person disregards it. |
| **Anything that commands the engine** | Flight-critical. CEMILAC case, DO-178C-class assurance, years. |

The active diagnosis perturbs a running engine, so it is flight-critical **by
definition**. It is the cleverest capability in the design and the last that
would ever fly.

> *"The advisory path and the commanding path are deliberately separated.
> Everything we have shown can be fielded as an advisory system with no change
> to flight-critical software. The active diagnosis is specified with its full
> safety envelope so it is ready when the airworthiness case is made — but
> nothing else depends on it."*

### 5.3 The near-term product worth naming

Even if nothing flies, **step 2 stands alone.** Any organisation running these
engines has flight data in one place and maintenance records in another, and
nothing joins them. A system that replays recorded flights and reports
*"cylinder 2 was drifting for eleven hours before that injector was replaced"*
pays for itself in maintenance planning, with **no airworthiness case at all**.

That is the version that could be in use within a year, and it is the one a
procurement person can imagine buying.

### 5.4 Who sits in front of it

| User | What they take from it |
|---|---|
| **Mission commander, in flight** | continue / derate / RTB with probabilities, and the cost of the safe option in minutes on station |
| **Maintenance officer, after landing** | which component, how confident, how long it has left, and the false-alarm rate to judge it by |
| **Fleet engineer** | trends across airframes; which failure modes actually occur |
| **Programme office** | evidence that an indigenous engine is accumulating demonstrated reliability |

### 5.5 Why the FADEC matters

The VRDE engine is the right target specifically because it has an indigenously
developed FADEC. A FADEC publishes **commanded** quantities — commanded fuel,
commanded timing. The injector-fouling diagnosis is *commanded versus
delivered*. **Without the commanded number that residual does not exist**, and
roughly half the fault library gets weaker. The engine choice is not incidental.

---

## 6. What is built, what is specified, what is claimed

Being precise about this is what makes the rest credible.

### Built and running

- Ground control station: interactive 3D engine, strip charts with twin overlay, residual heatmap, explain drawer, global replay scrubber
- Mission risk map with draggable cruise altitude and point-of-no-return
- Twin-confidence / novelty channel
- Cross-engine differential with a common-mode disturbance to demonstrate cancellation
- **Interactive fault injection console with blind mode**
- Live engine-profile switching
- WebSocket client that upgrades from simulated to live the moment the backend publishes

### Specified, for the backend to implement

- Mean-value engine model (5 state groups), ISA atmosphere, ten-fault library
- Parity residual generator ρ₁–ρ₁₁
- UKF joint state–parameter estimation
- M1/M2/M3 and the N-CMAPSS benchmark
- CAN transport on `vcan0`
- Novelty projection with the SVD rank check

### Deliberately not claimed

- **No technique here is novel.** Mean-value modelling, parity-space FDI, joint state–parameter estimation, active fault diagnosis and life-extending control are all established with substantial literature. What is claimed is their coherent assembly plus an explicit answer to the validation problem.
- **Not a strict PINN.** A physics backbone with a learned correction and physically motivated loss terms — stated rather than blurred.
- **Cross-engine invariance is first-order** and needs a small bias correction.
- **Parity residuals are still thresholded.** Over-determination changes *what* is thresholded, not that a threshold exists.

---

## 7. Honest limitations

**Three fault pairs are not structurally isolable from steady-state parity.**
Named, not hidden — and they are the derived justification for the active
diagnosis layer.

**The novelty detector's margin is narrow.** After correcting the per-cylinder
structure of the incidence matrix, the fault signatures span **10 of 11**
residual dimensions, leaving **one** in which an unmodelled fault is still
visible. That is not a comfortable margin, and **it shrinks as the fault library
grows** — a limitation of the method, not of the implementation. It must also be
recomputed on the continuous sensitivity matrix, where it may go to zero, in
which case the claim gets dropped.

**Transfer costs about ten healthy samples**, not zero.

**The data is synthetic** — and so is C-MAPSS, which this field has benchmarked
on since 2008. Limits come from type certificates, fault signatures from
published failure-mode literature, degradation from Archard and Miner. The
pipeline is dataset-agnostic and runs unmodified on NASA's N-CMAPSS.

**The demo simulator is not the flight model.** The frontend's generator stands
in for the backend's MVEM until the socket is live. It obeys the same two rules
the real one must — faults are parameter perturbations, and a sensor fault
perturbs the measurement only — but it is a stand-in and should be described
that way.

> Volunteering these is not modesty. An evaluator who finds a limitation we
> concealed discounts everything else; an evaluator who finds every limitation
> already identified and bounded reads the remaining claims as reliable. In a
> technical evaluation that trade is strongly favourable.
