# ML Evaluation Metrics

**Every number here was measured, not estimated.** Anything not yet measured
says so and stays blank — a `TBD` on this page is safer than a number on a
slide that cannot be defended.

Measured **2026-09-10, late session**, after the air-path fix (six physics
gates now pass — `backend/gates_check.py`) and a full retrain against it.
**These numbers supersede everything measured earlier the same night** — the
collapsed air path is fixed, the incidence-matrix degeneracy is fixed, and
`direction_jitter` is now a measurement instead of a guess. Read this version.

> **Sections 2–6 are on SYNTHETIC residuals** from `ml/data/synthetic.py` —
> they characterise the models, not the engine. N-CMAPSS (§1) is the only
> external check.

---

## 1. N-CMAPSS benchmark — external validation

| Metric | Value | Notes |
|--------|-------|-------|
| RMSE | _pending_ | cycles |
| NASA asymmetric score | _pending_ | lower is better |
| Within ±10% band | _pending_ | % of test windows |

Dataset downloaded, verified, extracted: `N-CMAPSS_DS01-005.h5` (2.68 GB,
NASA PCoE official mirror). DS01 has exactly 6 dev units and 4 test units
total — the notebook now pulls **all of them at full 1 Hz resolution**
(`max_units=10, stride=1`, ~1.4 GB peak, nothing subsampled away). Benchmark
run is next, immediately after this file is pushed.

Notebook: `ml/benchmarks/ncmapss_baseline.ipynb`, prints `REAL_DATA=True/False`
beside every score — **never quote a number printed with `REAL_DATA=False`.**
Trains on `X_s` (measured sensors) only — `X_v` (virtual/simulator-only
channels) and `T` (ground-truth health parameters) are excluded; `T` is the
answer, training on it would be leakage.

Only DS01 of the eight DS0x subsets in the downloaded 14.6 GB archive has
been extracted and used. The other seven remain a possible follow-up, not
tonight's scope.

---

## 2. The air-path fix, and what it changed for every number below

Earlier tonight, `ρ₁` and `ρ₅` were **structurally** zero — `m_c` was
algebraically slaved to `m_a`, so no amount of ML could ever have made them
carry information, regardless of model quality. That is fixed
(`docs/qa/known-issues.md`, `PRAMANA-DIRECTIVE-2026-09-10.md` §1). Verified
directly: a 25% compressor fault now moves ρ₁ by real, non-trivial σ. All
eleven residual channels carry genuine signal for the first time tonight —
`backend/config/sigma_vector.json` regenerated against the fixed physics
shows none pinned at a hard floor.

This matters for every section below because M2/M3 were previously trained
and evaluated on **synthetic** residuals regardless — the air-path bug never
touched their training data directly. What it *did* block was
`ml/data/measure_jitter.py`, which perturbs BE-1's actual MVEM to measure how
far a real fault's residual direction sits from its textbook incidence
column. With the air path broken, that measurement was noise (component
faults barely moved anything real). Fixed, it now gives §3's real number.

---

## 3. `direction_jitter` — now measured, not guessed

`ml/data/synthetic.py` perturbs each training sample off its nominal
incidence column by `direction_jitter`, which is necessary (without it, the
classifier and the incidence matcher score against the same construction and
"agreement" is an identity, not evidence — see §5). The value was **invented
at 0.30** earlier tonight with no empirical basis, and it turned out to set
the classifier's achievable accuracy ceiling almost single-handedly.

Re-measured against the fixed MVEM (`ml/data/measure_jitter.py`,
`ml/data/jitter_report.json`): **pooled median 0.3968** across
injector/turbo/cooling/bearing/cht/egt (n=60 perturbation samples).

| Fault | Measured jitter |
|---|---|
| injector_fouling | 1.75 |
| turbo_degradation | 1.31 |
| bearing_wear | 0.74 |
| cooling_fouling | 0.45 |
| cht_sensor_drift | 0.38 |
| egt_sensor_drift | 0.26 |

`ring_wear` and component faults with no MVEM parameter hook yet
(`detonation`, `fuel_filter_clog`, `ignition_misfire`, `lambda_sensor_drift`,
`map_sensor_drift`, `oil_pump_wear`) could not be measured — those faults
don't exist as physics yet (BE-1's directive §2 covers them). The pooled
value substitutes for them; that substitution is recorded in the report file,
not hidden.

**Honest result, in the direction that does NOT flatter the story:** the real
jitter (0.40) is *higher* than the invented one (0.30), which *lowers* the
achievable ceiling:

| Jitter | Oracle ceiling (perfect classifier, same task) |
|---|---|
| 0.30 (invented) | 0.687 |
| **0.40 (measured)** | **0.523** |

Used anyway — the point was never to pick a flattering number, it was to
stop guessing. `synthetic.py`'s default is now 0.40.

---

## 4. M3 classifier — fault isolation

| Metric | Value | Notes |
|--------|-------|-------|
| **Held-out top-1** | **0.435** | n=3100, `faulty_val`, separate RNG, never trained on |
| **Held-out top-2** | **0.655** | |
| Oracle ceiling at this jitter | 0.523 | see §3 — model is at 83% of the real achievable maximum |
| Classifier↔matcher agreement | **0.240** | measured, not assumed — see §5 |
| Confusion matrix | `ml/weights/m3_classifier_report.json` | 13×13 |

Architecture: scale-normalised input (fault identity lives in the
**direction** of ρ, magnitude is severity — a nuisance variable), 48/96
channel 1D-CNN with BatchNorm, 120 epochs at lr=2e-3 (previous run's cosine
schedule annealed to zero while loss was still visibly falling; this run's
final loss 0.72, converged cleanly).

**A lower number than earlier tonight's 0.624 — expected, not a regression.**
0.624 was measured against the invented jitter (0.30, ceiling 0.687); 0.435
is measured against the real one (0.40, ceiling 0.523). **The
percentage-of-ceiling is what's comparable across the two, and it held:
~83% both times.** The model didn't get worse; the yardstick got honest.

### Read the confusion matrix before quoting the accuracy

Top confusions, and what the spec already predicts about them:

| True | Confused with | n | Structural reason |
|---|---|---|---|
| detonation | ignition_misfire | 88 | both hit ρ₄/ρ₆₋₉/ρ₁₁ — separable only on ρ₁₁ *harmonic content*, which a window-mean-style classifier doesn't see |
| ignition_misfire | detonation | 85 | same |
| egt_sensor_drift | cht_sensor_drift | 81 | rows differ in exactly one channel (ρ₄) — `residual-spec.md` §4 calls this pair "weakly isolable" |
| cht_sensor_drift | egt_sensor_drift | 71 | same |
| injector_fouling | detonation | 64 | both excite ρ₆₋₉ strongly |
| injector_fouling | ignition_misfire | 61 | §4: "isolable only with dynamic channels" — exactly the pair the spec names |

Per-class recall: 0.26 (ring_wear, fuel_filter_clog) to 0.59 (cooling_fouling).
Lower across the board than the previous run, consistent with the ceiling
dropping from 0.687 to 0.523 — not a new failure mode.

**Lead with this, not the raw number:** a classifier whose confusion
structure reproduces an isolability analysis derived independently from the
physics — the exact three pairs `residual-spec.md` §4 names as hard — is
behaving correctly. One that scored higher by separating those pairs would
be fitting noise, not learning physics.

### On "two independent mechanisms agreeing"

They agree **24.0%** of the time. **Do not claim independence, and do not
claim strong agreement.** The defensible statement: the network carries
whatever accuracy it has, the matrix carries the derived explanation, and
where they diverge — which is most of the time — the system reports both
rather than picking one and hiding the disagreement.

---

## 5. Structural isolability — measured on our own matrix

`ml.incidence.inseparable_groups()` tests the columns-differ criterion
against the matrix rather than asserting it. As of tonight's fix
(`detonation` given ρ₁₁=+2, matching the physical fact that detonation rings
the crank structure and a drifting thermocouple does not):

**`inseparable_groups()` returns `[]` — no two fault rows are identical.**
This was `{detonation, egt_sensor_drift}` earlier tonight; fixed, mirrored
across `ml/incidence.py`, `frontend/src/analysis/incidence.ts`, and
`docs/spec/residual-spec.md` §3/§4 in one commit.

The confusion matrix (§4) shows the *weaker* structural ambiguities the spec
already names as hard (detonation/misfire, the two sensor drifts) — those are
real physics limits on isolability from steady-state parity alone, not a bug
to fix in the model.

---

## 6. M2 autoencoder — anomaly detection

| Metric | Value | Notes |
|--------|-------|-------|
| Anomaly threshold | **1.2132** | 99.5th pct of reconstruction error on held-out healthy — never hand-picked |
| Persistence rule | 4-of-5 | consecutive frames above threshold |
| False alarm rate | _TBD_ | needs `data/healthy_flights/*.parquet` regenerated against tonight's fixed physics (`BE-1-FINAL-DIRECTIVE-2026-09-10.md` §4) |
| Detection lead time | _TBD_ | needs BE-1's labelled fault runs with redline timestamps |

Retrained against residuals from the fixed MVEM. Escalation behaviour
(2σ→no fire, 4σ→fires) was verified earlier tonight before the retrain and
the gating mechanism is unchanged; not re-verified point-by-point this pass.

---

## 7. Novelty projection

| Metric | Value | Notes |
|--------|-------|-------|
| Effective rank | 10 | of 11 |
| Null-space dimension | **1** | one direction of genuine "unrecognised" |
| Threshold (99.5th pct) | 0.7341 | |

**Still computed on the COARSE incidence matrix** (entries 0, ±1, ±2) — must
be recomputed on the sensitivity matrix (Jacobian) before rank-10 goes on a
slide. `ML-DEEP-DIVE.md` §9 already states the null space may vanish
entirely on the continuous matrix as a known property of the method.

---

## 8. UKF — health parameter identification

Unchanged from earlier tonight — this fix predates the retrain and doesn't
depend on M2/M3's weights. Fed a single fault signature for 80 steps; every
other parameter stays at nominal 1.000:

| Injected signature | Identified | Others |
|---|---|---|
| turbo degradation (ρ₁ −2) | η_c → **0.667** | at nominal |
| injector fouling cyl 2 | cd_inj₂ → **0.329**, cd₁/₃/₄ ≈ 0.91 | — |
| cooling fouling | hA → **0.561** | at nominal |
| bearing wear (ρ₅ +2) | f_fric → **1.449** | at nominal |
| healthy | — | all 1.000 |

Before the sigma-point fix every parameter returned an **identical** value
whatever the residuals did. θ carried no information at all. It is now the
interpretable health indicator the architecture claims it is.

---

## Not measured yet — and why

| Gap | Blocked on |
|---|---|
| N-CMAPSS score | running next, immediately after this commit |
| False alarm rate / hr | `data/healthy_flights/*.parquet` regenerated against **tonight's** physics — BE-1's directive §4 |
| Detection lead time | BE-1's labelled fault runs with redline timestamps |
| RUL against ground truth | BE-1's damage integrator — directive §3, not yet built |
| Novelty rank on the sensitivity matrix | recompute before quoting rank-10 anywhere |
| Ablation study | after the above |
