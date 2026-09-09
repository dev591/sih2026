# ML Evaluation Metrics — Day 4 Results

**Fill this in on Day 4 (measurement day). All numbers go on the slide.**

---

## N-CMAPSS Benchmark (Task 1 — run before touching our data)

| Metric | Value | Notes |
|--------|-------|-------|
| RMSE | _TBD_ | cycles |
| NASA asymmetric score | _TBD_ | lower is better |
| Within ±10% band | _TBD_ | % of test windows |
| α–λ accuracy | _TBD_ | |
| False alarm proxy | _TBD_ | |

Source: `ml/benchmarks/ncmapss_baseline.ipynb`

---

## M2 Autoencoder — Anomaly Detection

| Metric | Value | Notes |
|--------|-------|-------|
| Anomaly threshold | _TBD_ | 99.5th pct held-out healthy |
| False alarm rate (100 healthy flights) | _TBD_ | per flight hour |
| Detection lead time (injector fouling) | _TBD_ | seconds before redline |
| Detection lead time (compressor degradation) | _TBD_ | seconds |

---

## M3 Classifier — Fault Isolation

| Metric | Value | Notes |
|--------|-------|-------|
| Top-1 accuracy | _TBD_ | on held-out fault runs |
| Top-2 accuracy | _TBD_ | |
| Confusion matrix | see attached | |
| Classifier vs matrix agreement rate | _TBD_ | % where both agree |

---

## M3 RUL — Two-Headed Estimation

| Metric | Value | Notes |
|--------|-------|-------|
| RMSE (network p50 head) | _TBD_ | hours |
| RMSE (physics head) | _TBD_ | hours |
| Interval coverage (p10–p90) | _TBD_ | % of true values inside |
| Heads-disagree rate | _TBD_ | % of steps |

---

## Novelty Detection

| Metric | Value | Notes |
|--------|-------|-------|
| effective_rank | _TBD_ | from SVD on sensitivity matrix |
| null_space_dim | _TBD_ | 11 − effective_rank |
| ν (in-library fault × 5σ) | _TBD_ | expect ≈ 0 |
| ν (null-space direction × 5σ) | _TBD_ | expect ≈ 1 |
| ν threshold (99.5th pct healthy) | _TBD_ | |

**⚠ IMPORTANT**: The effective_rank here must come from the SENSITIVITY MATRIX
(continuous Jacobian entries), NOT the coarse glyph matrix (0, ±1, ±2).
Do not put rank-10 on the slide until this is recomputed. See novelty-detection.md.

---

## Ablation — Raw Signals vs Residuals (Day 4)

Same M2 autoencoder architecture, two training conditions:

| Condition | False alarm rate | Detection lead time |
|-----------|-----------------|---------------------|
| Trained on RAW sensor values | _TBD_ | _TBD_ |
| Trained on RESIDUALS (ours) | _TBD_ | _TBD_ |

This is the bar chart that turns our central design claim from an assertion
into a measured result. One afternoon to run.

---

## Edge Latency (Day 5)

| Component | Latency (ms) | Hardware |
|-----------|-------------|----------|
| M2 inference (1 window) | _TBD_ | _TBD_ |
| M3 inference (1 window) | _TBD_ | _TBD_ |
| Novelty projection | _TBD_ | _TBD_ |
| UKF step | _TBD_ | _TBD_ |
| Full pipeline (1 Hz frame) | _TBD_ | _TBD_ |

Target: full pipeline < 100 ms on a Raspberry Pi 4 class device.
