# BE-2 — Start Here

**Machine Learning. Layers 2–3. PS component D.**

The full picture is in `work-breakdown.md`. This is what to do **today**, in
order. Do not read the 29-page document first — do task 1, then read.

---

## You are not blocked on BE-1, and you should not wait

Your models consume the **residual vector**, not raw telemetry. That vector is
specified in `docs/spec/residual-spec.md` and you can generate synthetic
residuals yourself in an afternoon. Start now.

More importantly, your **highest-value task of the entire week does not involve
our data at all** — so do it first.

---

## Task 1 — N-CMAPSS baseline, BEFORE touching our simulator (target: today)

**This is the highest-value 90 minutes of your week and it is task one for a
reason.**

Get a RUL score on NASA's turbofan degradation data. Both datasets live in the
NASA Prognostics Center of Excellence repository — *"Turbofan Engine Degradation
Simulation"* is C-MAPSS, *"…Simulation-2"* is **N-CMAPSS**, which is the better
one because it carries real flight conditions.

```
ml/benchmarks/ncmapss_baseline.ipynb
```

### Why this comes first

It is **the only thing that proves our ML is not memorising our own simulator.**
A DRDO panel will ask *"your data is synthetic, why should we believe any of
it?"* and the answer ends with *"and here it is running unmodified on NASA's
flight-condition data, with the score."* That sentence is worth building for.

It also de-risks your whole week. If everything else goes wrong, you still have
the single most credible slide in the deck.

**Done means:** a number on a slide, and the notebook committed.

### Report the field's own metrics, not just RMSE

RMSE alone reads like a course assignment. Add:

- **NASA asymmetric score** — `Σ e^(−d/13) − 1` if `d < 0`, else `Σ e^(d/10) − 1`. Late predictions punished harder than early. Field standard.
- **Prognostic horizon** and **α–λ accuracy** — standard PHM measures; using them signals you know the discipline.
- **False alarms per flight hour** — *the question a maintenance officer asks first.* Quoting it unprompted is a strong signal.

---

## Task 2 — The rule that governs everything you build

> **Every model you build eats the residual vector ρ. Never raw sensor values.**

This is not a stylistic preference and you will be asked to defend it.

An autoencoder trained on raw signals learns the **flight profile**, not the
engine. Then every throttle movement and every climb changes every channel, and
it screams. **That is the single biggest reason naive builds false-alarm
constantly**, and a lot of teams will discover it on the last day.

The residual vector is **nominally zero** and **operating-point invariant by
construction**, so your autoencoder learns a genuinely simple target — *healthy
means near zero and white* — and stays quiet through a climb.

You should be able to say that paragraph from memory. It answers at least two
judge questions.

**And prove it:** same autoencoder trained on raw signals vs on residuals, one
bar chart. That is the **ablation** (day 4), and it converts our central design
claim from an assertion into a measured result. One afternoon.

---

## Task 3 — Three models, each with one job (days 2–3)

Resist building five model families because the problem statement mentions
several.

### M1 — physics-guided residual estimator

```
ŷ = f_physics(u; θ) + g_NN(u)
     └─ MVEM ─┘       └─ small correction ─┘
```

The network learns only the **discrepancy**, so it trains on very little data
and **cannot produce absurd predictions at operating points outside its training
set** — which matters, because your training data will be mid-altitude cruise
and the aircraft flies to 28 000 ft.

Soft physical constraints in the loss:
```
L = L_data + λ₁‖ρ₄‖² + λ₂‖ṁ_a+ṁ_f−ṁ_ex‖² + λ₃‖min(0, ∂T_cht/∂ṁ_f)‖
              energy      mass continuity        monotonicity
```

**On PINNs, honestly:** do the *soft* version. A strict PDE-residual PINN is a
research project that may not converge in our window, and if it doesn't we have
nothing. What we claim — physics backbone, learned correction, physically
motivated loss terms — is what we deliver, and the docs say so plainly.

**Virtual sensors this gives you**, and DRDO asks for virtual sensing by name:
peak cylinder pressure, knock margin, per-cylinder combustion efficiency,
turbo shaft speed, oil film thickness.

### M2 — LSTM autoencoder on residuals

Train on healthy windows only. Threshold is the **99.5th percentile of
reconstruction error on held-out healthy data** — *never* a hand-picked
constant, and be ready to say that out loud. Add an N-of-M persistence rule to
suppress transients.

### M3 — classifier + RUL

1D-CNN or GRU over the residual window → softmax across the ten fault classes.
Labels are free because BE-1 generates the faults.

**Run it alongside the incidence-matrix match and show both agreeing.** The
network supplies accuracy; the matrix supplies the explanation. *Agreement
between two independent mechanisms is itself evidence*, and judges remember the
why.

**RUL — two heads, always both:** a quantile network (p10/p50/p90) and a physics
head integrating damage forward. Report both, advise on the conservative.
*"Physics says 4.1 hours, the network says 3.7, we advise on 3.7."* Disagreement
beyond the interval is surfaced as a warning in its own right.

---

## Task 4 — Novelty detection (day 3) — NEW, and the strongest thing you own

Not in the original scope. About **forty lines**. It answers the most dangerous
question a panel can ask:

> *"You have ten faults in your library. What happens when the engine does
> something that isn't one of them?"*

Every classifier is forced to pick from its list, so the usual answer is that it
confidently names the closest wrong thing.

Each fault signature is a **direction** in the 11-dimensional residual space.
Stack them into `F` and split the live residual:

```
ρ̂  = proj_span(F) ρ      explained
ρ⊥ = ρ − ρ̂               UNEXPLAINED
ν  = ‖ρ⊥‖ / ‖ρ‖
```

High `‖ρ‖` with high `ν` means something real that the library **cannot
express**. The honest output is *low confidence*, not a confident wrong answer.

**Reference implementation to port:** `frontend/src/analysis/novelty.ts`.
It works and it is running in the dashboard now.

### Four things that will bite you, all already found

1. **Use the SVD.** Not normal equations — `F` is ill-conditioned by construction.

2. **rho6..rho9 is NOT one direction.** The incidence matrix lists them as a group, which is right for a printed table and wrong as a direction in residual space. A single-cylinder fault lifts **its own** thermal deviation and pushes the other three slightly negative, because each is measured against the **conditional mean**. Sum-to-zero is baked into the residual definition.

   Treated as a block, a genuine in-library fault does not lie in span(F) at all. Measured:

   ```
                                          nu BEFORE   nu AFTER
   EGT sensor drift, cyl 4 (in library)     0.932      0.000
   Injector fouling, cyl 2 (in library)     0.610      0.000
   Unmodelled fault (null space)            0.000      1.000
   ```

   **It was crying wolf on two thirds of its own library while scoring the genuinely unknown fault at zero — exactly backwards.** Expand per-cylinder faults into one column each, thermal part shaped as `(e_i − mean)`.

3. **Weight ν by residual significance.** Unweighted it reads ~50% confidence on a *healthy* engine, because isotropic noise puts √(3/11) of itself in the null space by construction. A confidence channel that panics at idle gets ignored when it matters.

4. **RECOMPUTE THE RANK ON THE SENSITIVITY MATRIX.** Everything above comes from the coarse glyph matrix (entries 0, ±1, ±2). The physically correct object is the **Jacobian of each residual with respect to each fault parameter**, with continuous entries. On the coarse matrix, correcting item 2 already took the null space from 3 dimensions to **1**. **There is a real chance it goes to 0 on the continuous matrix — in which case we drop the claim and say why.** Check before anyone builds a slide on it.

Full spec: `docs/spec/novelty-detection.md`.

---

## Definition of done, per day

| Day | Done means |
|---|---|
| **1** | **N-CMAPSS baseline score.** Notebook committed. |
| **2** | M1 + M2 trained on synthetic residuals, threshold calibrated on healthy held-out data. |
| **3** | M3 classifier + confusion matrix. Two-headed RUL with p10/p50/p90. Novelty projection ported **with the SVD rank report**. |
| **4** | **Measurement day:** false-alarm rate over 100 healthy flights, detection lead time, the raw-vs-residual ablation. |
| **5** | Edge latency benchmark. **Pre-trained weights committed to the repo.** |

---

## What you must not do

- **Do not** train on raw sensor values. Ever. Residuals only.
- **Do not** hand-pick an anomaly threshold. Calibrate on healthy held-out data and be able to say how.
- **Do not** report a bare RUL number without its uncertainty band.
- **Do not** attempt a strict PDE-residual PINN. Scoped out deliberately, and we say why.
- **Do not** train during the demo. Weights are committed.
- **Do not** put the rank-10 figure on a slide until you have recomputed it on the sensitivity matrix.
- **Do not build the GNN** unless day 4 closed completely clean — and then only as a second isolation head *beside* the signature matrix, never replacing it.

---

## Where to ask

- The residual vector you consume → `docs/spec/residual-spec.md`
- Novelty maths → `docs/spec/novelty-detection.md`
- Why any of this → `docs/SOLUTION-ANALYSIS.md` §2.9–2.11
