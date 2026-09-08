# Addendum — Novelty Detection & Twin Confidence

**Status: SCHEMA ADDITION. Adding fields is free; renaming them later is not.
Read this before building the health-frame publisher.**

Owner: **BE-2** (the maths), **BE-1** (publishing the two fields).
Consumed by: the ground control station, which already reserves UI space for it.

---

## The question this exists to answer

> *"You have ten faults in your library. What happens when the engine does
> something that isn't one of them?"*

A DRDO panel will ask this. Every classifier is forced to pick from its list,
so the honest answer for most systems is *"it confidently names the closest
wrong thing."* That is worse than useless to a maintenance officer.

We can do better, and it costs almost nothing, because the answer falls
straight out of the structure we already have.

---

## The mechanism

Each fault mode in the incidence matrix (`residual-spec.md` §3) defines a
**direction** in the 11-dimensional residual space. Stack the normalised
signature columns into a matrix **F** ∈ ℝ^(11×K).

Split any live residual vector into the part your fault library can account
for, and the part it cannot:

```
ρ̂  = F F⁺ ρ            explained   — lies in the span of known faults
ρ⊥ = ρ − ρ̂             UNEXPLAINED — orthogonal to every fault we modelled

ν  = ‖ρ⊥‖ / ‖ρ‖         novelty index, in [0, 1]
```

`F⁺` is the Moore–Penrose pseudoinverse. Use the SVD, not a normal-equations
inverse — the columns are deliberately collinear (see the rank check below) and
`FᵀF` will be ill-conditioned.

### Interpretation

| ‖ρ‖ | ν | Meaning | What the GCS shows |
|---|---|---|---|
| low | — | healthy | nominal |
| high | **low** | a fault we know | isolate normally, full confidence |
| high | **high** | **real, and not in the library** | *"excitation pattern not in fault library"* — twin confidence LOW |

That third row is two things at once, and both are valuable:

- an **unknown-unknown detector** — an unmodelled failure mode
- a **model-validity monitor** — the twin itself has drifted from the engine

Either way the correct output is *"my confidence in this diagnosis is low"*
rather than a confident wrong answer.

---

## THE RANK CHECK — do this before claiming anything

**This is not optional, and it is the part an expert evaluator will probe.**

With K ≈ 13 fault modes in an 11-dimensional residual space, **F could in
principle span ℝ¹¹, leaving no null space at all** — in which case every
possible residual is "explainable" and the whole claim collapses.

It does not collapse, because several signatures are near-collinear: EGT sensor
drift, CHT sensor drift and detonation all excite ρ₆₋₉ in a similar pattern.
The *effective* rank is expected to be around 7, leaving 3–4 genuine dimensions
of unexplainable residual.

**But expected is not measured.** BE-2:

1. Compute the SVD of **F** and report the singular values.
2. State the effective rank at a stated tolerance (e.g. σᵢ/σ₁ > 0.05).
3. Put `effective_rank` and `null_space_dim` on the validation slide.

> *"We checked: the effective rank is 8, so there are three dimensions of
> unexplainable residual"* is a far stronger statement than asserting the
> concept. And if the rank came out at 11, we would say so and drop the claim —
> which is exactly the honesty policy that makes the rest of the deck credible.

### Measured result (frontend reference implementation, 2026-09-08)

```
tol = 0.01 / 0.05 / 0.10   ->   rank = 8,  null-space dim = 3   (identical at all three)

exactly collinear with the first 8 (residual norm 0.000 after orthogonalisation):
  detonation, map_sensor_drift, egt_sensor_drift, cht_sensor_drift, lambda_sensor_drift
```

The rank is stable across an order of magnitude of tolerance, so it is a real
structural property and not a threshold artefact. Note also *which* signatures
turned out to be exactly dependent — they are the same ones the isolability
analysis in `residual-spec.md` §4 already flagged as weakly or non-isolable
from steady-state parity. **The two analyses corroborate each other**, which is
worth one sentence on the slide.

> **CAVEAT BE-2 MUST HANDLE.** The number above comes from the *coarse* incidence
> matrix, whose entries are glyph-level (0, ±1, ±2). That quantisation is what
> makes five signatures come out exactly collinear. The physically correct object
> is the **sensitivity matrix** — the Jacobian of each residual with respect to
> each fault parameter, evaluated at the operating point — whose entries are
> continuous. Recompute the rank on that, with the SVD, and report *those*
> singular values. The rank will likely be higher and the null space smaller.
> If it collapses to zero, we drop the claim and say why. Do not put the coarse
> number on a slide as if it came from the physics.

### SIGNIFICANCE WEIGHTING — do not skip this

**ν on its own is not interpretable, and this bit a working implementation.**

At rest the residual is pure noise, and isotropic noise distributes itself
evenly over all 11 dimensions — so it puts √(3/11) ≈ 0.52 of itself in the null
space *by construction*. An unweighted confidence channel therefore sits at
about 50% on a perfectly healthy engine, which is worse than useless: it would
be ignored exactly when it mattered.

ν only carries information once there is a residual worth explaining. Weight it:

```
significance = clamp((‖ρ‖ − ρ_floor) / (ρ_full − ρ_floor), 0, 1)
confidence   = 1 − ν · significance
exceeded     = ν > threshold  AND  significance > 0.5
```

with `ρ_floor` calibrated as the 99th percentile of ‖ρ‖ on held-out healthy
data. Below the floor there is nothing to explain, so confidence is full.

### Threshold

Under the healthy null hypothesis, ‖ρ⊥‖² is chi-square distributed with
`null_space_dim` degrees of freedom. Set the alarm at the 99.5th percentile on
held-out healthy data, same as the autoencoder — **never a hand-picked
constant**, and be ready to say so.

---

## Schema addition

Two new fields in `pramana.health.v1`, alongside `anomaly`:

```jsonc
{
  "novelty": {
    "index": 0.14,             // nu = ||rho_perp|| / ||rho||, in [0,1]
    "residual_norm": 4.82,     // ||rho||, sigma units
    "unexplained_norm": 0.67,  // ||rho_perp||, sigma units
    "threshold": 0.42,         // 99.5th pct of nu on held-out healthy data
    "exceeded": false,         // index > threshold AND residual_norm significant
    "effective_rank": 7,       // rank(F) at the stated tolerance
    "null_space_dim": 4        // 11 - effective_rank
  },
  "twin_confidence": {
    "value": 0.93,             // 1 - nu, clipped; how far the diagnosis is trusted
    "basis": "residual_projection",
    "note": "low when the excitation pattern is not in the fault library"
  }
}
```

Both blocks are **optional**. The GCS degrades gracefully if they are absent —
it simply hides the confidence channel rather than showing a wrong number. So
BE-1 can publish the rest of the health frame today and add these later without
breaking anything.

---

## What we claim, and what we do not

**Claimed:** the system can distinguish *"a fault I recognise"* from
*"something is wrong and it is not in my library"*, and reports the difference
as a confidence channel rather than forcing a pick from a fixed list.

**Not claimed:** that we can identify what the unknown fault *is*. We cannot.
The value is in refusing to guess, and in telling the operator that a human
should look.

**Not claimed:** novelty of method. Residual projection onto a fault-signature
subspace is standard structured-residual practice. What is unusual is applying
it as an explicit *self-assessment* channel in a health monitor, and being
willing to display the result when it is unflattering.

> A tool that never says *"I don't know"* cannot be trusted when it does answer.

That sentence is the pitch. It is also the reason the project is called
**Pramāṇa** — a valid means of knowledge is one that can identify the boundary
of its own validity.
