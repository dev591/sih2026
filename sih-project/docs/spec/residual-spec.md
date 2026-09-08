# PRAMANA — Parity Residual Vector & Fault Incidence

**The contract between BE-1 (physics) and BE-2 (ML).** BE-1 guarantees these
eleven numbers; BE-2 builds every downstream model against them and never
touches a raw sensor value.

This document is also the isolation algorithm and one of the best slides in the
deck. It is the same object three times over.

---

## 0. Why residuals at all

A threshold system compares a sensor to a redline. A digital twin compares a
sensor to **what physics says it should read at this exact operating point**.
The gap between those two numbers is the entire product.

The parity vector **ρ ∈ ℝ¹¹** has three properties raw telemetry does not:

1. Its nominal value is **zero**
2. It is **operating-point invariant by construction**
3. Its noise distribution is characterised **once** on healthy data and reused everywhere

That is why the anomaly detector does not need to relearn the flight envelope,
and why it does not raise an alarm every time the throttle moves — which is the
single biggest reason naive builds false-alarm constantly.

---

## 1. Four paths to air mass flow

Air mass flow is the most informative quantity in the engine and is almost
never instrumented. It is recoverable four ways that **share no common failure
mode**.

| Path | Relation | Sensors used | Model dependency |
|---|---|---|---|
| **1 — Speed–density** | `ṁ = η_v · p_im·V_d·N / (R·T_im·120)` | MAP, IAT, crank speed | volumetric efficiency map |
| **2 — Compressor map** | `ṁ = ṁ_corr(π_c, N_tc/√θ₁) · δ₁/√θ₁` | turbo speed, comp. inlet/outlet p & T | compressor map |
| **3 — Fuel & λ** | `ṁ = λ · AFR_st · ṁ_f` | injector command, wideband UEGO | *none of consequence* |
| **4 — Intake restriction** | `ṁ = C_d·A·(p_us/√(R·T_us))·Ψ(p_ds/p_us)` | orifice ΔP, upstream p & T | discharge coefficient |

with the compressible-orifice function

```
Ψ(r) = √( 2γ/(γ−1) · (r^(2/γ) − r^((γ+1)/γ)) )        r  >  r_crit
Ψ(r) = √( γ · (2/(γ+1))^((γ+1)/(2(γ−1))) )            r  ≤  r_crit   (choked)
```

The `120` in Path 1 is two crank revolutions per cycle × 60 s/min. Path 3 has
essentially no model dependency, which makes it the most trustworthy reference
when the others disagree.

> **Path 4 availability, stated honestly.** Path 4 needs a throttle body or a
> metering restriction. **A FADEC-controlled aero-diesel is unthrottled**, so on
> the VRDE profile Path 4 exists only if a hot-film MAF or venturi is fitted.
> The framework degrades gracefully: with *n* available paths you get *n−1*
> independent relations, and the isolability analysis is recomputed for whatever
> sensor set the target engine carries. The engine YAML declares this in
> `parity_paths`. Do not fabricate ρ₃ on an engine that cannot produce it.

---

## 2. The eleven residuals

### ρ₁–ρ₃ — air path consistency

**With *n* estimates of one quantity, only *n−1* residuals are linearly
independent.** The third pairwise difference is the sum of the first two.
Choosing Path 1 as reference:

```
ρ₁ = ṁ_SD − ṁ_C        speed-density vs compressor
ρ₂ = ṁ_SD − ṁ_λ        speed-density vs fuel/lambda
ρ₃ = ṁ_SD − ṁ_R        speed-density vs restriction   (null if Path 4 unavailable)
```

**Stating this explicitly matters.** A system claiming six residuals from four
estimates is double-counting, and an evaluator familiar with parity-space
methods *will* check. Getting this right costs nothing and getting it wrong
costs the room's confidence in everything else.

### ρ₄ — energy closure (first law)

Chemical power in equals useful work out plus every loss path:

```
ρ₄ = ṁ_f·Q_LHV − (2πN/60)·T_b − (ṁ_a+ṁ_f)·c_p,ex·(T_egt−T_∞) − Σᵢ hA·(T_cht,i − T_cool)
     └─ fuel power ─┘   └─ brake ─┘   └──── exhaust enthalpy ────┘   └── heat rejection ──┘
```

Normalised as `ρ₄ / (ṁ_f·Q_LHV)`, this sits within a few per cent across the
envelope in a correct model. It is sensitive to any fault that changes *where
the fuel's energy goes* — which is most of them — making it **an excellent
detector but a poor isolator on its own.**

### ρ₅ — power closure without a torque sensor

Flight engines are not instrumented for shaft torque. **But a propeller is a
calibrated dynamometer:**

```
P_prop = C_P(J) · ρ · n³ · D⁵        J = V_∞ / (n·D),  n in rev/s

ρ₅ = (η_i·ṁ_f·Q_LHV − P_fric − P_pump) − C_P(J)·ρ·n³·D⁵
```

ρ₅ is **the primary channel for friction-related degradation** — bearing wear,
lubrication breakdown — because those faults consume shaft power without
altering the gas path at all. Nothing else in the vector sees them cleanly.

> Watch the gearbox. On a geared engine (Rotax 914, ratio 2.4295) `n` is
> *propeller* speed, not crank speed. Getting this wrong breaks ρ₅ silently and
> is the most likely bug in the cross-engine transfer demonstration.

### ρ₆–ρ₉ — per-cylinder thermal deviation

Deviation of each cylinder's EGT and CHT from the **conditional mean across
cylinders** at the same operating point. Conditional, not absolute — that is
what makes it operating-point invariant. Four independent thermal states are
what make per-cylinder localisation physically meaningful instead of a colour
assigned arbitrarily.

### ρ₁₀ — oil pressure model residual

Measured oil pressure minus the model prediction as a function of speed and oil
temperature. Note the Rotax limit is itself speed-dependent (2.0 bar above 3500
rpm, 0.8 bar below) — **a fixed threshold gets this wrong; a twin does not.**

### ρ₁₁ — 0.5-order crank ripple

Magnitude of the 0.5-engine-order component of crank angular velocity. In a
four-stroke, any one cylinder fires once per **720°** of crank rotation, so a
single-cylinder defect appears at half engine order. Zero for a balanced
engine, non-zero for any single-cylinder defect. This is the classical
automotive misfire discriminant, it is in patents from the early nineties, and
it localises a fault to a cylinder **from a sensor that is already fitted.**

---

## 3. Fault incidence matrix

Because each relation is built from a known subset of sensors and a known piece
of physics, the effect of any fault on any relation is **derivable, not
asserted from experience.**

| Fault mode | ρ₁ | ρ₂ | ρ₃ | ρ₄ | ρ₅ | ρ₆₋₉ | ρ₁₀ | ρ₁₁ |
|---|---|---|---|---|---|---|---|---|
| Ring wear / blow-by | ↑ | ↑ | ↑ | ↑ | ↑ | · | ↓ | · |
| Compressor fouling | ⇓ | · | · | ↑ | · | · | · | · |
| Injector fouling, cyl *i* | · | ⇓ | · | ↑ | · | ⇑ | · | ⇑ |
| Fuel filter / rail loss | · | ⇓ | · | ↑ | ↓ | · | · | · |
| Ignition misfire, cyl *i* | · | · | · | ⇑ | ↓ | ⇓ | · | ⇑ |
| Cooling degradation | · | · | · | ↑ | · | ↑ | ↑ | · |
| Oil leak / pump wear | · | · | · | · | ↑ | · | ⇓ | · |
| Bearing wear | · | · | · | ↑ | ⇑ | · | ↓ | · |
| Detonation / pre-ignition | · | · | · | ↑ | · | ⇑ | · | · |
| **MAP sensor drift** | ⇑ | ↑ | ↑ | ↑ | · | · | · | · |
| **EGT sensor drift, cyl *i*** | · | · | · | ↑ | · | ⇑ | · | · |
| **Lambda sensor drift** | · | ⇑ | · | · | · | · | · | · |

⇑ strong positive · ↑ weak positive · ⇓↓ negative · `·` unexcited.
**Component faults above the rule, instrumentation faults below it.**

### The isolation principle

> **Every fault has an *odd path*: the estimate that departs while the others
> hold. Identifying which path is the outlier, and with what sign, identifies
> the fault — without a classifier, and without ever having observed that fault
> before.**

Worked examples:

- **Ring wear** reduces *true* volumetric efficiency while the model retains its nominal value → Path 1 over-reads, the other three stay correct.
- **Compressor fouling** degrades the machine while the map stays nominal → Path 2 over-reads alone.
- **Injector fouling** makes delivered fuel fall below commanded; the mixture leans, measured λ rises → Path 3 over-reads **by exactly the ratio of commanded to delivered fuel.**
- **MAP drift reading high** corrupts Path 1 *upward* and Path 2 *downward simultaneously*, because the same pressure enters one as a density and the other as a pressure ratio. **This correlated double-effect is one no component fault can imitate** — it is the sensor-vs-engine discriminator, and it falls out of the structure for free.

Read the last three rows against the first three. An injector clog moves five
residuals in a physically coupled pattern. A drifting EGT sensor moves one,
with nothing corroborating it — no ripple, no CHT response, no fuel-flow
change. **That is the entire demo at 3:00 in the script.**

Cosine similarity between the live residual vector and each row gives an
explainable diagnosis with a confidence number attached, at essentially zero
model cost.

---

## 4. Isolability — what does NOT separate

Two faults are structurally isolable **if and only if their columns differ.**
Applying that test honestly:

**Strongly isolable from steady-state parity alone (7 modes)**
Compressor fouling, oil system faults, bearing wear, cooling degradation, ring
wear, fuel rail loss, lambda sensor drift — each produces a unique excitation
pattern.

**Isolable only with dynamic channels**
Injector fouling and ignition misfire *on the same cylinder* share their
steady-state signature. They separate on ρ₄ magnitude and on the harmonic
content of ρ₁₁ — **misfire removes a complete combustion event, injector
fouling attenuates it.**

**Weakly isolable — requires active resolution**
- Injector fouling cyl *i* vs **EGT sensor drift cyl *i***: differ structurally only through ρ₂ and ρ₁₁, both small at low severity — *exactly the regime where early detection is valuable.*
- Ring wear vs MAP drift: differ only in the *relative magnitude* of ρ₁ versus ρ₂, not in sign pattern.

**This last category is not a weakness to conceal. It is the derived
justification for the active diagnosis layer.** The system knows which
ambiguities its own structure cannot resolve, and probes only those.

---

## 5. Active diagnosis — asking the engine a question

Rather than guess, command a small, bounded, zero-mean perturbation `u(t)` of
cylinder *i* fuel trim and examine **the gain of the response, not its level.**

Under H₁ (injector delivers fraction κ<1 of command) the physical fuel
perturbation is `κ·u(t)` and EGT responds with attenuated amplitude. Under H₂
(healthy injector, drifting transducer) the physical response is full and the
sensor merely adds a constant offset `b`.

```
Ĝ = cov(y_egt,i , u) / var(u)      ⟹    Ĝ|H₁ ≈ κ·G₀ ,    Ĝ|H₂ ≈ G₀
```

**The bias `b` cancels identically** — it lives entirely in the DC term and
never appears in the alternating component. Level tells you nothing here; gain
tells you everything. A sequential probability ratio test on
`Λ = log[p(y|H₁)/p(y|H₂)]` continues the probe only until a pre-specified error
bound is met, typically a few seconds.

### Safety envelope — non-negotiable

Perturbing a flying engine requires an explicit safety case:

- Amplitude stays **inside the FADEC's existing trim authority (±3–5%)**, which the controller already exercises in normal closed-loop operation
- Duration bounded to a few seconds
- Armed **only** when posterior fault probability lies in the ambiguous band — never routine
- **Inhibited** during takeoff, climb, approach, and any single-engine condition
- Aborted immediately if any monitored parameter approaches a certified limit
- On a twin-engine aircraft, **one engine at a time**

> This capability is what changes the system's category. A monitor observes and
> infers. PRAMANA, when its own structure tells it that observation is
> insufficient, **designs and executes an experiment.**

---

## 6. Health parameters — the UKF state

Degradation parameters live *inside* the estimator's state vector, modelled as
a slow random walk `θ_{k+1} = θ_k + w_k`, `w_k ~ N(0, Q_θ)` with `Q_θ` small:

```
θ = [ η_v_scale , η_c_scale , (hA)_scale , C_d,inj^(1..4) , f_fric ]
```

Two consequences. First, the twin stays synchronised with a physically ageing
engine **without manual recalibration** — a twin calibrated once and never
revised is wrong within a few hundred hours, and when it disagrees with the
engine there is no way to know which of the two moved.

Second, and this is the point: **the parameter estimates are themselves the
health indicators, and they are physically interpretable.**

> *"Compressor efficiency scale 0.87, down from 0.94, standard deviation 0.02"*
> is a statement a propulsion engineer can accept or dispute.
> *"Health score 0.71"* is not.

The filter also supplies the rigorous form of sensor-fault discrimination. The
innovation `ν_k = y_k − h(x̂_{k|k−1})` measures surprise. **A component fault
produces innovation that is absorbed by an adjustment of θ; a transducer fault
produces innovation on one channel that no physically admissible parameter
change can explain.** Monitoring normalised innovation squared alongside the
parameter trajectory separates the two cases on principle rather than by
heuristic.
