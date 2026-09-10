# PRAMANA — ML Layer, Deep Dive

**Owner: BE-2. Audience: BE-2, and anyone presenting this to the panel.**

`BE-2-START-HERE.md` tells you what to build and in what order. This document
answers the question that guide deliberately doesn't spend time on: **why this
model and not the obvious alternative**, for every choice in the ML layer —
with the field's own literature behind each answer, not just team assertion.

Nothing here changes an architectural decision. M1/M2/M3, the UKF, and the
novelty projection are locked, with reasons already recorded in
`docs/spec/residual-spec.md`, `docs/spec/novelty-detection.md`, and
`docs/SOLUTION-ANALYSIS.md` §2.9–2.12. This document synthesizes those reasons,
adds the "why not X instead" comparisons an evaluator will actually ask about,
and cites where each choice sits in the published literature. Treat it as the
document you reread the night before you present, not as a second spec.

---

## Contents

1. [Why the residual vector, not raw telemetry](#1-why-the-residual-vector-not-raw-telemetry)
2. [Why three models, not one end-to-end network](#2-why-three-models-not-one-end-to-end-network)
3. [M1 — physics-guided residual estimator](#3-m1--physics-guided-residual-estimator)
4. [M2 — LSTM autoencoder](#4-m2--lstm-autoencoder)
5. [M3 — classifier + two-headed RUL](#5-m3--classifier--two-headed-rul)
6. [The UKF — health parameters as state](#6-the-ukf--health-parameters-as-state)
7. [Novelty detection — knowing when it doesn't know](#7-novelty-detection--knowing-when-it-doesnt-know)
8. [N-CMAPSS — why it comes before our own data](#8-n-cmapss--why-it-comes-before-our-own-data)
9. [What we do not claim](#9-what-we-do-not-claim)
10. [Anticipated judge questions](#10-anticipated-judge-questions)

---

## 1. Why the residual vector, not raw telemetry

### The failure mode this avoids

Train an autoencoder on raw sensor channels — EGT, CHT, MAP, RPM — and it does
not learn "the engine." It learns **the flight profile**, because throttle
position dominates the variance of almost every raw channel far more than any
plausible fault does. Climb, and every temperature and pressure moves at once.
The network has never seen that particular climb rate at that particular
altitude in training, reconstruction error spikes, and the system raises an
alarm on a perfectly healthy engine doing something perfectly normal.

This is **the single biggest reason naive builds false-alarm constantly**, and
it is the failure mode a large fraction of the other teams working this
problem statement will discover on the last day, after the demo is already
built around raw signals.

### What the residual vector has that raw telemetry doesn't

The parity residual vector ρ ∈ ℝ¹¹ (`residual-spec.md` §0, §2) is not a
transformation of convenience — it has three properties raw telemetry
structurally cannot:

1. **Nominal value is zero.** A healthy engine at any operating point produces
   ρ ≈ 0. A raw EGT reading of 720 °C is meaningless without knowing you're at
   cruise; ρ₄ ≈ 0 means the same thing at idle, cruise, or climb.
2. **Operating-point invariant by construction.** Each residual is the
   difference between two independent estimates of the same physical quantity
   (§1: air mass flow recoverable four ways). Both estimates move together
   with throttle and altitude; the *difference* does not, because both track
   the same physics. This is not a statistical property you hope holds — it's
   a structural consequence of how the residual is defined.
3. **Noise characterized once, reused everywhere.** Because ρ is nominally
   zero and stationary, you calibrate its healthy-data distribution once and
   the threshold holds across the whole flight envelope. A raw-signal model
   needs its normal range re-established at every operating point, which is
   equivalent to needing a model of normal *operation* before you can even
   start modelling *health*.

### The literature behind this

This is not a novel idea — it is established practice in model-based fault
diagnosis, generally attributed to Gertler and Isermann's work on
**parity-space** and **structured residual** methods for fault detection and
isolation (FDI). The core result: residuals built from redundant analytic
relationships (not from redundant sensors) are nominally zero under the
system's own equations, and *structured* residual design — making each
residual, or subset, sensitive to a known set of faults and insensitive to
others — is what makes fault **isolation**, not just detection, possible
[Gertler, "Fault Detection and Isolation Using Parity Relations", *Control
Engineering Practice*; Chen & Patton, *Robust Model-Based Fault Diagnosis for
Dynamic Systems*].

**Say this out loud if asked "why not just anomaly-detect on the raw
signals":** *"An autoencoder on raw telemetry learns the flight profile, not
the engine — that's the single biggest source of false alarms in naive
builds. We instead build eleven parity residuals that are zero by construction
regardless of operating point, so our model only has to learn one thing:
healthy is near-zero and white."* That is `residual-spec.md` §0 and
`BE-2-START-HERE.md` Task 2, and you should be able to say it from memory —
both documents say exactly that.

---

## 2. Why three models, not one end-to-end network

The problem statement's language ("health monitoring," "fault prediction,"
"reliability enhancement") invites building one large network that ingests
everything and outputs everything. Resist it. Three reasons, in order of how
often a judge asks about them:

**Explainability collapses in one network, and is free with three.** A single
end-to-end model that outputs "fault: injector fouling cylinder 2, RUL 3.7 h"
gives no path back to *why*. Splitting the job means the classifier's answer
(M3) is checked against an independent, hand-derivable mechanism — the fault
incidence matrix (`residual-spec.md` §3) — and **agreement between two
independent mechanisms is itself evidence** (§2.9, §5 of this doc). One
network has nothing to agree with but itself.

**Each model needs a realistic amount of training data, and one network needs
all of it at once.** M1 (the physics correction) needs relatively little data
because the MVEM does the heavy lifting. M2 (the autoencoder) needs only
healthy data. M3 (the classifier) needs labelled fault data, which is exactly
where our synthetic generator earns its keep because *labels are free*
(`work-breakdown.md`, BE-2 section, M3). Folding all three into one network
means the weakest data source — real fault examples — bottlenecks the whole
system instead of just the one piece that needs it.

**Failure isolation.** If the autoencoder threshold is miscalibrated, the
classifier and the RUL heads still work — they consume the same ρ, not the
autoencoder's output. A monolithic network fails as one unit; three models
fail (and are debuggable) independently.

This mirrors the field's general move away from single black-box PHM models
toward modular pipelines with a physics or structured-residual front end
feeding independent, purpose-built heads — the same shape M1/M2/M3 takes here.

---

## 3. M1 — physics-guided residual estimator

### The job

Predict what every sensor *should* read at the current operating point, plus
several quantities no sensor measures at all (peak cylinder pressure, knock
margin, per-cylinder combustion efficiency).

```
ŷ = f_physics(u; θ) + g_NN(u)
    └─ MVEM ─┘        └─ small correction ─┘
```

### Why hybrid, not pure physics and not pure network

**Pure physics (MVEM alone)** is not wrong, but it is a lumped-parameter,
mean-value model — it cannot capture every second-order effect (heat-transfer
correlations at off-design conditions, transient thermal lag, manufacturing
variance between individually built engines). Those residual errors are small
but systematic, and a system whose "prediction" carries unmodelled bias reports
that bias as a permanent, meaningless offset in every downstream ρ.

**Pure network (no physics)** trains on whatever operating points your
simulated missions happen to visit — dominated by mid-altitude cruise, because
that's where an aircraft spends most of its time. But the VRDE-180 profile
flies to a critical altitude near 11,000 ft and the platform's mission profile
reaches roughly 28,000 ft (`config/engine_vrde_180.yaml`, `residual-spec.md`
turbo section). A pure network extrapolating into thin-data territory can
produce **physically absurd** outputs there — exactly where compressor
degradation is most visible and matters most (`BE-1-START-HERE.md` Task 2, §5:
"invisible at sea level and mission-limiting above critical altitude").

**The hybrid avoids both failure modes.** The network only ever learns the
*residual on top of physics*, `g_NN(u)`, which is a much smaller and smoother
function than the raw sensor mapping. It needs far less data to fit well, and
because the physics term dominates, an out-of-distribution operating point
still gets a physically sane baseline prediction even where the correction
term hasn't been trained.

### The soft physical constraints

```
L = L_data + λ₁‖ρ₄‖² + λ₂‖ṁ_a+ṁ_f−ṁ_ex‖² + λ₃‖min(0, ∂T_cht/∂ṁ_f)‖
              energy      mass continuity        monotonicity
```

Each term costs one gradient penalty and buys behaviour the data alone would
not guarantee: the energy term keeps the network's output consistent with the
first law (ρ₄ is meant to be small in a correct model, per `residual-spec.md`
§2); the mass-continuity term stops the correction from inventing or losing
mass across the intake-to-exhaust boundary; the monotonicity term rules out a
learned correction that predicts CHT *falling* as fuel flow rises, which is
physically impossible and would otherwise be a valid local minimum of pure
data loss on a noisy, finite dataset.

### On PINNs, precisely

This architecture is **physics-informed**, not a strict Physics-Informed
Neural Network in the sense of Raissi, Perdikaris & Karniadakis (2019) — a
network trained purely by minimizing PDE-residual loss at collocation points,
with no physics model in the forward pass at all. We deliberately do the
*soft* version — a physics model in the loop (the MVEM) plus a learned
correction and soft physical penalties in the loss, not a strict PDE-residual
formulation with hard-enforced constraints.

This distinction is worth stating precisely because the field itself argues
about it: research comparing soft-constrained vs. hard-constrained PINN
training finds hard constraints remove weighting hyperparameters and guarantee
exact constraint satisfaction, but at the cost of a harder, more constrained
optimization problem — and soft-constraint training with adaptive weighting
generally avoids the numerical stiffness that hard-constraint formulations can
introduce. For a lumped-parameter ODE system solved on a laptop in a six-day
build, the hard/strict formulation is a research project that may simply not
converge in the time available, and if it doesn't converge, we have nothing.
The soft formulation is well-understood, converges reliably, and delivers what
we actually need: a physically sane correction term. **State the distinction
rather than blur it** — claiming "PINN" without qualification invites an
evaluator who knows the term to ask exactly this question.

### Virtual sensors — tied to the PS by name

DRDO's problem statement asks for virtual/analytical sensing explicitly. This
is the payoff of M1: peak cylinder pressure, knock margin, per-cylinder
combustion efficiency, turbocharger shaft speed, oil film thickness — none
measured directly, all recoverable because the physics model that predicts
them is calibrated against everything that *is* measured.

---

## 4. M2 — LSTM autoencoder

### The job

Flag *"this does not look like a healthy engine"* without being told what the
fault is — the first line of defense before the classifier even runs.

### Why a sequence model, not a static (feed-forward) autoencoder

A static autoencoder scores one instant of ρ against a learned healthy
manifold. But several of the signatures that matter are **dynamic**, not
instantaneous: ρ₁₁ (crank ripple) is a spectral quantity that only exists over
a window, and the incidence-matrix worked example distinguishing injector
fouling from ignition misfire on the same cylinder explicitly separates on
"the harmonic content of ρ₁₁" (`residual-spec.md` §4) — a property of a
sequence, not a sample. An LSTM autoencoder reconstructs a *window* of ρ, so
it is sensitive to exactly this class of signature, and it also naturally
absorbs short transients that a static model would flag as instantaneous
anomalies.

### Why LSTM, not a Transformer, here specifically

A Transformer autoencoder is the obvious "more modern" choice, and it is the
wrong one for this specific problem, for reasons worth being able to state
rather than just asserting "simpler is better":

- **Data volume.** Transformers are data-hungry relative to recurrent models,
  particularly for the self-attention layers to learn useful temporal
  structure rather than approximately uniform attention. Our healthy-data
  budget is what a six-day synthetic generator plus (at best) a short real
  bench run produces — closer to the regime where an LSTM's stronger inductive
  bias toward local, sequential dependence pays off, and further from the
  regime where attention's flexibility is worth its data cost.
- **Window length.** ρ windows here are short (seconds, driven by 1 Hz health
  frames) — well inside the range where recurrent architectures are not at a
  representational disadvantage. Transformers' main structural edge is
  modelling very long-range dependencies; our signatures are local (misfire's
  0.5-order ripple, a thermal transient over a handful of seconds), not the
  regime that motivates attention in the first place.
- **Latency budget.** The health frame runs at 1 Hz and needs an inference
  cheap enough to run continuously alongside two other models and a UKF. An
  LSTM over a short window is a smaller, faster model to run and to explain in
  a demo than a Transformer, for no accuracy the extra data we don't have
  would let it realize anyway.

This is a data-and-latency argument, not a "Transformers are overkill"
dismissal — worth stating that way if asked, because the honest answer is
"our data regime and window length don't favor attention," not "Transformers
are bad."

### Threshold discipline

Threshold = **99.5th percentile of reconstruction error on held-out healthy
data.** Never a hand-picked constant. Add an **N-of-M persistence rule** (the
schema's `anomaly.persistence: {n, of, met}`) so a single noisy sample doesn't
trip the alarm.

**Why this specific discipline matters to a judge:** any fixed numeric
threshold invites the question "how did you pick that number, and would it
still work on a different engine?" A percentile calibrated on held-out data
answers both at once — it's derived, not chosen, and re-deriving it is the
entire cost of porting to a new engine profile. This is the same discipline
`novelty-detection.md` uses for the novelty threshold (§7 below) and that
consistency across the two thresholds is itself worth pointing out.

---

## 5. M3 — classifier + two-headed RUL

### The classifier

1D-CNN or GRU over a residual window → softmax across the ten fault classes
(`work-breakdown.md`, BE-1's fault table). Labels are free because BE-1's
generator injects each fault as a labelled parameter perturbation — this is
the one place in the whole pipeline where supervised learning is cheap, purely
because the physics simulator produces ground truth for free.

### Why run it *alongside* the incidence-matrix match, not instead of it

`residual-spec.md` §3 already gives a **zero-training-cost** diagnosis method:
cosine similarity between the live residual vector and each row of the fault
incidence matrix. It is derivable from physics, needs no fault examples to
build, and generalizes to any fault whose mechanism is understood even if it
was never in the training set.

The network and the matrix are **two mechanisms of different kinds** — one
learned from labelled examples, the other derived from first principles — but
they are **not statistically independent**, and the distinction matters when a
panel probes it. Our training residuals are generated *around* the incidence
signatures (`ml/data/synthetic.py`), so a classifier that reproduced the matrix
exactly would prove nothing. Training samples are therefore jittered off the
nominal columns, which makes the classifier learn a region rather than the
matrix itself, and makes the agreement rate a measured quantity — reported in
`ml/weights/m3_classifier_report.json` — instead of an identity. Quote the
measured rate; do not claim independence. `SOLUTION-ANALYSIS.md` §2.9: "the
network gives accuracy, the matrix gives the why." A judge who asks "how do
you know the network isn't just pattern-matching something spurious" gets
answered by "because an independently-derived physical mechanism agrees with
it," which a bare confusion matrix cannot answer on its own.

### RUL — why two heads, always both

A single point estimate of remaining useful life invites exactly one
follow-up question: *"how confident are you in that number?"* Two heads with
different failure characteristics answer it:

- **Quantile-regression network** (p10/p50/p90) — learned from data, captures
  whatever uncertainty the training distribution encodes, but degrades however
  a learned model degrades outside its training distribution.
- **Physics head** — integrates the identified damage rate forward from the
  current UKF-estimated health parameters. Model-based, so it extrapolates
  more gracefully to conditions the network hasn't seen, at the cost of
  whatever the physics model itself doesn't capture.

Report both and advise on the conservative one — *"physics says 4.1 hours, the
network says 3.7, we advise on 3.7."* **Disagreement beyond the predictive
interval is surfaced as a warning in its own right**, because two independent
estimators of the same physical quantity landing far apart is itself
diagnostic information (the same "odd path" logic that drives the parity
residuals in the first place, applied one layer up).

---

## 6. The UKF — health parameters as state

### Why joint state-parameter estimation at all

A twin calibrated once and never revised is wrong within a few hundred hours
of an engine that is physically ageing, and once it disagrees with the real
engine there is no way to tell which of the two has moved. Putting degradation
parameters *inside* the estimator's state vector — as a slow random walk,
`θ_{k+1} = θ_k + w_k` — solves this: the twin tracks the real engine
continuously, with no separate recalibration step, and **the parameter
estimates are themselves the health indicators**, in physically interpretable
units (`residual-spec.md` §6).

```
θ = [ η_v_scale , η_c_scale , (hA)_scale , C_d,inj^(1..4) , f_fric ]
```

> *"Compressor efficiency scale 0.87, down from 0.94, standard deviation
> 0.02"* is a statement a propulsion engineer can accept or dispute.
> *"Health score 0.71"* is not.

### Why UKF over EKF specifically

Both filters solve the same joint state-parameter estimation problem; the
question is how they handle the engine model's nonlinearity. The EKF
linearizes the nonlinear state-transition and observation functions around the
current estimate via a first-order Taylor expansion (the Jacobian), which is
adequate when nonlinearity is mild but degrades — sometimes badly — when it
isn't. The MVEM's induction and turbocharger relations are meaningfully
nonlinear (speed-density's dependence on N and p_im, the compressor map's
dependence on pressure ratio and corrected speed), and deriving and
maintaining the Jacobians by hand for every one of those relations is itself
an error-prone exercise separate from the estimation problem.

The UKF instead propagates a small deterministic set of **sigma points**
through the *actual* nonlinear model — no linearization, no Jacobians — and
reconstructs the propagated mean and covariance from how those points land.
For a system with genuine, structured nonlinearity and a moderate state
dimension (our θ vector plus the engine states, well within the sigma-point
count that stays computationally cheap at 1 Hz), the UKF captures the
nonlinear propagation more faithfully without the Jacobian-derivation
liability the EKF carries.

### The bonus: rigorous sensor-fault discrimination for free

The filter's innovation, `ν_k = y_k − h(x̂_{k|k-1})`, measures surprise. **A
component fault produces innovation that is absorbed by an adjustment of θ** —
the filter explains the discrepancy by moving a physically meaningful
parameter. **A transducer fault produces innovation on one channel that no
physically admissible parameter change can explain** — nothing in θ moves,
because nothing in θ *should* move; the sensor is lying, not the engine.
Monitoring normalized innovation squared alongside the parameter trajectory
separates the two cases on principle, not heuristic — this is a second,
independent route to the same sensor-vs-engine discriminator that the parity
residuals already provide structurally.

---

## 7. Novelty detection — knowing when it doesn't know

### The question this answers

*"You have ten faults in your library. What happens when the engine does
something that isn't one of them?"* Every classifier is forced to pick from
its list, so the honest failure mode of most systems is confidently naming the
closest wrong thing. This is worse than useless to a maintenance officer, and
it is a question a DRDO panel is likely to ask precisely because it's the
obvious hole in any fault-classification system.

### The mechanism

Each fault signature is a **direction** in the 11-dimensional residual space.
Stack the normalized signature columns into `F ∈ ℝ^(11×K)`, then split any
live residual into what the library can explain and what it cannot
(`novelty-detection.md`):

```
ρ̂  = F F⁺ ρ            explained   — lies in the span of known faults
ρ⊥ = ρ − ρ̂             UNEXPLAINED — orthogonal to every fault we modelled
ν  = ‖ρ⊥‖ / ‖ρ‖         novelty index, in [0,1]
```

`F⁺` is the Moore–Penrose pseudoinverse, computed **via the SVD, not normal
equations** — `F`'s columns are deliberately collinear (several fault
signatures excite the same thermal channels), which makes `FᵀF`
ill-conditioned. The SVD handles that degeneracy directly; normal equations
would amplify it into numerical garbage. Port `frontend/src/analysis/novelty.ts`
— it's a working reference implementation, but its rank computation is
deliberately marked TODO for a proper SVD; that's the one thing you must not
copy as-is.

**High ‖ρ‖ with high ν** means something real that the fault library cannot
express — either an unmodelled failure mode, or the twin itself has drifted
from the real engine. Either way, the honest output is *low confidence in the
diagnosis*, not a confident wrong answer.

### Why subspace projection, not open-set / out-of-distribution classification

The more common ML answer to "what if it's not in my classes" is an open-set
recognition method — an OOD score from a discriminative classifier, or a
rejection option on a softmax. Those work, but they are opaque: the score is
"the network is unsure," with no derivable reason why, which is a much weaker
answer under cross-examination than "the excitation pattern does not lie in
the span of any known fault signature, verified by projecting onto a subspace
we can name column by column." Subspace projection also costs essentially
nothing to compute (one pseudoinverse), needs no additional training, and
degrades explainably — a judge can be shown *which* residual dimension carries
the unexplained component (`novelty.unexplained` in the schema), something a
softmax rejection score cannot offer. The trade-off: it only works because we
have physically derived fault signatures to project onto in the first place —
a system without a structured residual vector has no `F` to build.

### The rank check — the load-bearing validation step, and why the bugs are evidence, not embarrassment

With ~13 fault modes in an 11-dimensional space, `F` could in principle span
all of ℝ¹¹, leaving zero null space — in which case the entire claim collapses
(everything becomes "explainable" and the detector can never say "I don't
know"). It doesn't, but *expected is not measured*: compute the SVD, report
the singular values, state the effective rank at a stated tolerance
(σᵢ/σ₁ > 0.05), and put `effective_rank` / `null_space_dim` on the slide.

**Two bugs already found on the frontend's reference run, and both belong in
the presentation, not hidden:**

1. **Treating ρ₆–ρ₉ as one block column is wrong.** A single-cylinder fault
   lifts *its own* thermal deviation and pushes the other three slightly
   negative — because each is measured against the **conditional mean across
   cylinders**, sum-to-zero is a property of the residual's own definition, not
   an accident. Treated as a block, a genuine in-library single-cylinder fault
   doesn't lie in `span(F)` at all, and the detector cried "unrecognized" on
   two known faults while scoring a genuinely novel fault at *zero*:

   ```
                                          nu BEFORE   nu AFTER
   EGT sensor drift, cyl 4 (in library)     0.932      0.000
   Injector fouling, cyl 2 (in library)     0.610      0.000
   Unmodelled fault (null space)            0.000      1.000
   ```

   Fixed by expanding per-cylinder faults into one column each, thermal part
   shaped as `(eᵢ − mean)`. Null space shrank from 3 dimensions to **1**.

2. **Unweighted ν reads ~50% "confidence" on a perfectly healthy engine**,
   because isotropic noise distributes evenly across 11 dimensions and puts
   √(3/11) ≈ 0.52 of itself in the null space *by construction*. A channel
   that panics at idle gets ignored exactly when it matters. Fixed with
   significance weighting:

   ```
   significance = clamp((‖ρ‖ − ρ_floor) / (ρ_full − ρ_floor), 0, 1)
   confidence   = 1 − ν · significance
   exceeded     = ν > threshold  AND  significance > 0.5
   ```

**Present these as the reason the number is trustworthy, not as a flaw to
gloss over.** "We checked, found the matrix was wrong in a specific and
explainable way, and fixed it" is a materially stronger statement to a
technical panel than presenting a number with no visible provenance — this is
the project's honesty-as-credibility strategy applied to the ML layer
specifically (see §9).

### The caveat that still stands

Everything above runs on the **coarse** incidence matrix (entries 0, ±1, ±2).
The physically correct object is the **sensitivity matrix** — the Jacobian of
each residual with respect to each fault parameter, continuous entries.
**Recompute the rank there before anyone builds a slide on it.** Given the
correction above already took the null space from 3 to 1, there is a real
chance it goes to **0** on the continuous matrix — in which case the claim is
dropped and we say why. That is not a hypothetical caveat; check it before
committing to the number.

---

## 8. N-CMAPSS — why it comes before our own data

### The argument

Everything above — M1, M2, M3, the UKF, the novelty projection — is validated,
so far, entirely on data our own simulator produced. **That is circular, and a
sharp evaluator will say so.** The one thing that breaks the circularity is
running the exact same pipeline, unmodified, on a dataset we did not generate.

Two NASA benchmark datasets exist for exactly this purpose:
**C-MAPSS** ("Turbofan Engine Degradation Simulation") is the original,
synthetic and widely benchmarked since the 2008 PHM data challenge. **N-CMAPSS**
("...Simulation-2") is the better choice here specifically because it records
full run-to-failure trajectories from a healthy start to failure under **real
recorded flight conditions**, rather than synthetic operating-condition
sequences — closer to what an evaluator means when they ask "but is any of
this real" [Chao, Kulkarni, Goebel & Fink, "Aircraft Engine Run-to-Failure
Dataset under real flight conditions", NASA Prognostics Data Repository, NASA
Ames Research Center; see also Biggio, Wieland, Arias Chao, Kastanis & Fink
(2024) for continued work on the dataset].

Doing this **on day 1, before touching our own data**, is deliberate
sequencing, not a scheduling accident: it de-risks the entire week (if
everything else slips, this is still the single most credible slide in the
deck), and it produces the sentence that actually answers the panel's
question — *"our ML is not memorizing our own simulator, and here it is
running unmodified on NASA's flight-condition data, with the score."*

### Metrics — report the field's own, not just RMSE

RMSE alone reads like a course assignment. The field has its own standard
metrics from the original PHM08 challenge and its follow-on literature
[Saxena, Celaya, Saha, Saha & Goebel, "Metrics for Offline Evaluation of
Prognostic Performance", *International Journal of Prognostics and Health
Management*]:

- **The asymmetric scoring function** — `Σ e^(−d/13) − 1` for early predictions
  (`d < 0`), `Σ e^(d/10) − 1` for late ones. Late predictions are penalized more
  heavily than early ones, which reflects the actual safety asymmetry in
  maintenance decisions: an early warning costs a maintenance slot, a late one
  risks the failure itself. This asymmetry was the scoring function used to
  rank entries in the original 2008 PHM data challenge, so quoting it is
  quoting the field's own competition metric, not an invented one.
- **Prognostic horizon** and **α–λ accuracy** — from the same Saxena/Goebel
  metrics paper, standard in PHM literature for visualizing *when* an
  algorithm's predictions enter an acceptable error band around the true RUL
  and whether they stay there. Using them, unprompted, signals familiarity
  with the discipline rather than a one-off RMSE computation.
- **False alarms per flight hour** — the question a maintenance officer asks
  first, and one the academic metrics above don't directly answer. Quoting it
  unprompted is a strong, practically-minded signal.

---

## 9. What we do not claim

Stated here for the ML layer specifically, expanding `SOLUTION-ANALYSIS.md`
§7. Volunteering these is not modesty — an evaluator who finds a limitation we
concealed discounts everything else; one who finds every limitation already
identified and bounded reads the remaining claims as reliable.

- **No technique here is individually novel.** Structured/parity-space
  residual FDI, physics-informed correction networks, joint UKF state-parameter
  estimation, and subspace-projection novelty detection are all established,
  separately published methods. What is claimed is their coherent assembly
  around one frozen residual contract, plus an explicit, checked answer to the
  validation problem — not invention of any one piece.
- **Not a strict PINN.** A physics backbone with a learned correction and soft
  physically-motivated loss terms, stated as such rather than blurred into the
  stronger term.
- **The novelty detector's margin is narrow, and shrinks as the library
  grows.** After correcting the per-cylinder structure, the fault signatures
  span 10 of 11 residual dimensions on the coarse matrix — one dimension of
  genuine "I don't know" left, not a comfortable margin, and it must still be
  rechecked on the continuous sensitivity matrix where it may vanish entirely.
  This is a property of the *method*, not a bug in the implementation: a
  better, larger fault library structurally leaves less room for anything to
  look unfamiliar.
- **The data is synthetic** — and so is C-MAPSS, which the field has
  benchmarked against since 2008. Our limits come from type certificates, our
  fault signatures from published failure-mode literature, our degradation
  physics from established fatigue models (Archard wear, Miner's rule). The
  pipeline itself is dataset-agnostic and runs unmodified on N-CMAPSS — that's
  the point of §8.
- **Transfer to a new engine costs about ten healthy samples**, to recalibrate
  the UKF's parameter priors and the healthy-data thresholds — not zero, even
  though the architecture and code are unchanged.

---

## 10. Anticipated judge questions

**"Why not just use a Transformer / big foundation model for everything?"**
Because we don't have Transformer-scale data, our residual windows are short
and the signatures we need are local, and a monolithic model gives up the
explainability we get for free from splitting the job into three models that
check each other and a physics-derived incidence matrix that needs no training
data at all. See §2 and §4.

**"How do you know your anomaly threshold isn't cherry-picked to make the demo
look good?"** It's the 99.5th percentile of reconstruction/novelty error on
held-out healthy data, computed once and never hand-adjusted — the same
discipline for the M2 threshold and the novelty threshold. Re-deriving it is
the entire cost of moving to a new engine profile, which is itself evidence
it's derived, not tuned.

**"What happens when the engine does something you've never seen?"** The
novelty channel: we project the live residual onto the subspace spanned by
every fault signature we know, and report the fraction that's left over. High
residual with high novelty means "something real, and I can't name it" — the
system reports low confidence instead of guessing. We checked how much room
exists for that to work (§7's rank check) and we'll tell you the number, not
just assert the capability.

**"Isn't your training data synthetic, so how do you know any of this
generalizes?"** Two answers. First, C-MAPSS — the field's own benchmark since
2008 — is also synthetic, and nobody discounts prognostics research for using
it. Second, and more concretely: our entire residual → model → RUL pipeline
runs, unmodified, on N-CMAPSS, which carries real recorded flight conditions,
and we have a score to show for it. That's not a claim, it's a number.

**"Why do you need two RUL numbers instead of one?"** Because a lone point
estimate invites "how confident are you," and a physics-based estimate and a
learned estimate fail differently — one degrades however a network degrades
out of distribution, the other however an unmodelled physical effect degrades
a lumped model. When they agree, that's two independent methods confirming
each other. When they disagree beyond the predictive interval, that
disagreement is itself a warning worth surfacing, not noise to average away.

**"Why trust a UKF instead of a simpler estimator?"** The engine's induction
and turbocharger relations are meaningfully nonlinear, and hand-deriving
Jacobians for an EKF on every one of them is its own source of bugs, separate
from the estimation problem. The UKF propagates a small set of sigma points
through the real nonlinear model with no linearization step, which is a better
match for a system with structured, known nonlinearity and a state dimension
this modest.

---

**Sources cited above:**
- Chao, M., Kulkarni, C., Goebel, K., & Fink, O. — *Aircraft Engine
  Run-to-Failure Dataset under real flight conditions* (N-CMAPSS), NASA
  Prognostics Data Repository.
- Biggio, L., Wieland, A., Arias Chao, M., Kastanis, I., & Fink, O. (2024) —
  continued work on the N-CMAPSS dataset.
- Saxena, A., Celaya, J., Saha, B., Saha, S., & Goebel, K. — *Metrics for
  Offline Evaluation of Prognostic Performance*, International Journal of
  Prognostics and Health Management. Origin of the asymmetric scoring
  function, prognostic horizon, and α–λ accuracy.
- Gertler, J. — *Fault Detection and Isolation Using Parity Relations*,
  Control Engineering Practice; and the broader parity-space / structured
  residual FDI literature (Gertler, Isermann, Chen & Patton) underlying §1.
- General PINN literature on soft vs. hard constraint formulations, underlying
  the §3 "on PINNs, precisely" discussion.
- General UKF/EKF joint state-parameter estimation literature for nonlinear
  systems, underlying §6.

Where a claim above could not be pinned to a specific, verifiable citation, it
is stated as general/established practice rather than attributed to a paper —
consistent with this project's policy of not asserting more than can be
defended if checked.
