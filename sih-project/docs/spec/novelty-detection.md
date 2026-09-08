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

**Corrected after a bug found by injecting an in-library fault and watching the
detector reject it.** Both numbers are recorded because the difference is the
lesson.

```
                                   columns   rank   null-space dim
block thermal (WRONG)                 13       8          3
per-cylinder expansion (CORRECT)      31      10          1
```

Stable at tolerances 0.01 / 0.05 / 0.10 in both cases.

#### The bug: rho6..rho9 is not one direction

The incidence matrix lists rho6..rho9 as a group, which is right for a printed
table and **wrong as a direction in residual space.** A single-cylinder fault
does not lift all four thermal deviations equally — it lifts **its own** and
pushes the other three slightly negative, because each is measured against the
**conditional mean across cylinders**. Sum-to-zero is a property of how the
residual is defined, not an accident.

Treated as a block, a genuine in-library single-cylinder fault does not lie in
span(F) at all. Measured on synthetic residuals:

```
                                        nu BEFORE    nu AFTER
EGT sensor drift, cyl 4  (in library)     0.932       0.000
Injector fouling, cyl 2  (in library)     0.610       0.000
Unmodelled fault (null space)             0.000       1.000
```

**The detector was crying wolf on two thirds of its own library while scoring
the genuinely unknown fault at zero — exactly backwards.** The detector was
right and the matrix was wrong: it was faithfully reporting that the observed
pattern did not match what it had been told to expect.

**Fix:** expand any fault whose per-cylinder block is excited into one column
per cylinder, with the thermal part shaped as `(e_i − mean)`.

#### What this does to the claim

The null space shrinks from 3 dimensions to **1**. The claim survives — there
is still a direction in which an unmodelled fault is visible — but it is now a
**much narrower** claim, and it must be stated that way. One dimension out of
eleven is not a comfortable margin.

> **Say it honestly:** *"After correcting the per-cylinder structure, the fault
> signatures span ten of eleven residual dimensions. One dimension remains in
> which a fault we have never modelled is still detectable. That is a narrow
> margin, and it shrinks further as the library grows — which is itself a
> useful thing to know about the method."*

That last clause is worth saying out loud: **the better your fault library, the
less room there is for this detector to work.** It is a genuine limitation of
the approach, not of our implementation, and volunteering it is exactly the
trade that makes the rest of the deck credible.

> **BE-2 — THE CAVEAT STILL STANDS, AND IT MATTERS MORE NOW.** Everything above
> comes from the *coarse* incidence matrix (entries 0, ±1, ±2). The physically
> correct object is the **sensitivity matrix** — the Jacobian of each residual
> with respect to each fault parameter at the operating point — with continuous
> entries. Recompute the rank there, with the SVD, and report the singular
> values. Given that the correction above already took the null space from 3 to
> 1, **there is a real chance it goes to 0 on the continuous matrix, and then we
> drop the claim and say why.** Check before anyone builds a slide on it.

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
    "effective_rank": 10,      // rank(F) at the stated tolerance
    "null_space_dim": 1        // 11 - effective_rank; if this is 0, drop the claim
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
