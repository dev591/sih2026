# ML Evaluation Metrics

**Every number here was measured, not estimated.** Anything not yet measured
says so and stays blank — a `TBD` on this page is safer than a number on a
slide that cannot be defended.

Measured **2026-09-10** on `ml/weights/*` as committed.

> **All results below are on SYNTHETIC residuals** from `ml/data/synthetic.py`.
> They characterise the models, not the engine. The only external check is
> N-CMAPSS (§1), and until that has a number the honest answer to "why should
> we believe this" is §1's, not §3's.

---

## 1. N-CMAPSS benchmark — external validation

| Metric | Value | Notes |
|--------|-------|-------|
| RMSE | _pending_ | cycles |
| NASA asymmetric score | _pending_ | lower is better |
| Within ±10% band | _pending_ | % of test windows |
| α–λ accuracy | _pending_ | |

Dataset downloading at time of writing (NASA PCoE, "Turbofan Engine
Degradation Simulation Data Set 2", 14.6 GB). Notebook:
`ml/benchmarks/ncmapss_baseline.ipynb`, which prints `REAL_DATA=True/False`
beside every score — **do not quote a number printed with `REAL_DATA=False`.**

The notebook trains on `X_s` (measured sensors) only. `X_v` (virtual
channels) and `T` (ground-truth health parameters) are deliberately excluded:
`T` is the answer, and training on it would produce an excellent score that
collapses under one question.

---

## 2. M2 autoencoder — anomaly detection

| Metric | Value | Notes |
|--------|-------|-------|
| Anomaly threshold | **1.2031** | 99.5th pct of reconstruction error on held-out healthy — never hand-picked |
| Persistence rule | 4-of-5 | consecutive frames above threshold |
| Detection floor | **≈4σ aggregate** | measured: 2σ→no fire, 4σ→fires |
| False alarm rate | _TBD_ | needs `data/healthy_flights/*.parquet` from BE-1 |
| Detection lead time | _TBD_ | needs BE-1's fault runs with redline timestamps |

Escalation actually measured, one fault direction, 40 frames each:

| Severity | Reconstruction error | Fires |
|---|---|---|
| 2σ | 0.301 | no |
| 4σ | 1.459 | **yes** |
| 6σ | 3.618 | yes |
| 20σ | 47.06 | yes |

---

## 3. M3 classifier — fault isolation

| Metric | Value | Notes |
|--------|-------|-------|
| **Held-out top-1** | **0.624** | n=3100, `faulty_val`, separate RNG, never trained on |
| **Held-out top-2** | **0.816** | |
| Incidence matcher alone, top-1 | **0.327** | same held-out set |
| Classifier↔matcher agreement | **0.379** | window-mean ρ (0.270 on a single noisy frame) |
| Confusion matrix | `ml/weights/m3_classifier_report.json` | 13×13 |

**An earlier handoff reported 0.903. That was training-set accuracy** —
measured on the same loader the model fit, with no held-out fault split in
existence. The generalisation figure is 0.624. Quote 0.624.

### Read the confusion matrix before quoting the accuracy

The errors are not spread randomly. They land almost entirely on the pairs
`residual-spec.md` §4 already predicts cannot be separated:

| True | Confused with | n | Predicted by the spec? |
|---|---|---|---|
| egt_sensor_drift | detonation | 142 | **identical incidence rows** |
| detonation | egt_sensor_drift | 76 | **identical incidence rows** |
| egt_sensor_drift | cht_sensor_drift | 67 | differ only in ρ₄ |
| injector_fouling | ignition_misfire | 48 | §4 "isolable only with dynamic channels" |

Per-class recall ranges from 0.85 (lambda_sensor_drift, a unique row) down to
0.20 (egt_sensor_drift, which shares its row exactly with detonation).

**This is the number to lead with, not 0.624.** A classifier whose confusion
structure reproduces the isolability analysis derived independently from the
physics is behaving correctly. One that scored higher by separating
structurally identical rows would be fitting noise.

### On "two independent mechanisms agreeing"

They agree **38%** of the time, and the matcher alone scores 0.327 against the
classifier's 0.624. **Do not claim they agree.** The defensible statement is
that the network carries the accuracy, the matrix carries the explanation, and
where they diverge the system reports both rather than picking one.

---

## 4. Structural isolability — measured on our own matrix

`ml.incidence.inseparable_groups()` tests the columns-differ criterion against
the matrix rather than asserting it:

- **`detonation` ≡ `egt_sensor_drift`** — identical rows `[0,0,0,1,0,2,2,2,2,0,0]`.

One is a component fault, the other instrumentation, so this pair straddles
the exact discrimination the 3:00 demo beat rests on. The pipeline now refuses
to assert `is_sensor_fault` for either, sets `ambiguous: true`, and returns
`inseparable_from` — the "know what you cannot know" behaviour of
`residual-spec.md` §5. It is a demonstrable strength, but only because it is
declared; undeclared it was a silent way to clear a detonating engine.

---

## 5. Novelty projection

| Metric | Value | Notes |
|--------|-------|-------|
| Effective rank | 10 | of 11 |
| Null-space dimension | **1** | one direction of genuine "unrecognised" |
| Threshold (99.5th pct) | 0.7341 | |

**Computed on the COARSE incidence matrix** (entries 0, ±1, ±2). It must be
recomputed on the sensitivity matrix (Jacobian) before rank-10 goes on a
slide — on the continuous matrix the null space may vanish entirely, which
`ML-DEEP-DIVE.md` §9 already states as a known property of the method.

---

## 6. UKF — health parameter identification

Fed a single fault signature for 80 steps; every other parameter should stay
at nominal 1.000:

| Injected signature | Identified | Others |
|---|---|---|
| turbo degradation (ρ₁ −2) | η_c → **0.667** | at nominal |
| injector fouling cyl 2 | cd_inj₂ → **0.329**, cd₁/₃/₄ ≈ 0.91 | — |
| cooling fouling | hA → **0.561** | at nominal |
| bearing wear (ρ₅ +2) | f_fric → **1.449** | at nominal |
| healthy | — | all 1.000 |

Before the sigma-point fix every parameter returned an **identical** value
whatever the residuals did (all 1.063 on a compressor-only fault), because
`_sigma_points()` perturbed all eight dimensions together instead of one per
point. θ carried no information at all. It is now the interpretable health
indicator the architecture claims it is.

---

## Not measured yet — and why

| Gap | Blocked on |
|---|---|
| N-CMAPSS score | dataset download |
| False alarm rate / hr | `data/healthy_flights/*.parquet` from a **fixed** MVEM |
| Detection lead time | BE-1 fault runs with redline timestamps |
| RUL against ground truth | BE-1's damage integrator (Day 3) |
| Ablation study | after the above |

Every one of these needs BE-1's Task 0 done first. Numbers generated from the
collapsed air path would be measuring the bug, not the models.
