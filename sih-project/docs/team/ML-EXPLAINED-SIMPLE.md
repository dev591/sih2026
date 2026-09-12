# The ML layer, explained simply

**For:** the two people working on M1/M2/M3/UKF/novelty detection.
**Goal:** you should be able to explain WHY every piece exists, HOW it works,
under what IF conditions it's valid, and BUT — where it can break — without
reading code. This is the plain-language version of `docs/backend/ML-DEEP-DIVE.md`.

---

## The one sentence that explains everything

**We never feed the ML raw sensor values. We feed it the GAP between what a
sensor reads and what physics says it should read.** That gap is called a
**residual**, written ρ (rho). Everything downstream — anomaly detection,
fault classification, RUL — only ever sees ρ, never raw EGT/CHT/pressure.

### WHY this matters (the thing to say if you remember nothing else)

If you train a model on raw sensor values, it learns the **flight profile**,
not the **engine**. Every time the pilot pushes the throttle or climbs to a
new altitude, every raw number changes — so a model watching raw numbers
either (a) screams a false alarm on every throttle change, or (b) has to be so
desensitized it misses real faults. This is *the* most common way hackathon
teams fail this exact problem.

Residuals fix this by construction:
- **Nominal value is zero.** Healthy engine → ρ ≈ 0, always, regardless of
  altitude or throttle.
- **Operating-point invariant.** Climb, dive, speed up — physics already
  accounted for it, so ρ doesn't move because of that.
- **Its noise is measured once** (on healthy data) and reused everywhere, so
  "is this deviation significant?" has one consistent answer (in units of σ —
  standard deviations of known good noise).

So the ML's job becomes very easy in spirit: **watch 11 numbers that should
always be ~0, and say something when they aren't.** That's it. That's the
entire product.

---

## Why THREE models instead of one big network

A natural instinct is "just train one big model that takes sensors in and
spits out 'fault: X, RUL: Y hours'." We deliberately don't do that. Three
reasons, easy to explain to a judge:

1. **Explainability.** One black-box model gives you an answer with no way to
   check it. Three separate pieces mean the fault classifier's guess can be
   checked against an independent, hand-built "fault fingerprint" table
   (the incidence matrix) — **two different mechanisms agreeing is itself
   evidence.** A single network has nothing to agree with but itself.
2. **Data economy.** Each piece needs a different kind and amount of data.
   The physics model needs very little data (physics does the heavy lifting).
   The anomaly detector only needs *healthy* data (easy to get — just run the
   simulator). The classifier needs *labelled fault* data — which our
   synthetic generator gives us for free with perfect labels, since we're the
   ones injecting the fault. One giant model would be bottlenecked by
   whichever data source is weakest.
3. **Independent failure.** If the anomaly detector's threshold is a bit off,
   the classifier and RUL model still work fine — they don't depend on it.
   One big model fails as a single unit; three models fail (and get debugged)
   one at a time.

---

## The three pieces (M1 / M2 / M3) and the UKF

### M1 — "what SHOULD this engine read right now?"
A physics model of the engine (a Mean-Value Engine Model, MVEM — basically a
simplified thermodynamics simulation) predicts every sensor value given the
current operating point (altitude, throttle, RPM). A small neural network
sits on top to correct for the physics model's imperfections. **This is what
produces the "should read" half of the residual.** ρ = measured − predicted.

- **IF valid:** the physics model's assumptions hold (it's a reasonable
  approximation of a real 4-stroke turbo-diesel).
- **BUT:** it's a hybrid, not a "pure" physics-informed neural network (PINN)
  — say this precisely if asked, don't oversell it as more novel than it is.

### M2 — "is anything wrong at all?" (the anomaly detector)
An LSTM autoencoder trained ONLY on healthy-engine residual sequences. It
tries to reconstruct the residual window it just saw; if it can't reconstruct
it well, that's a sign the pattern is unfamiliar — i.e., something's off.
Threshold is the 99.5th percentile of reconstruction error on held-out
healthy data — **never hand-picked**, which matters a lot if a judge asks "how
do you know your threshold isn't cherry-picked."

- **WHY a sequence model (LSTM) and not just "is this one number weird":**
  faults develop as *trends over time*, not single-frame spikes. A slow
  injector-fouling ramp looks totally normal in any single frame; it only
  looks wrong across a window.
- **BUT:** it only knows what "normal" looks like — it has no idea WHAT is
  wrong, only THAT something is. That's M3's job.

### M3 — "which fault, and how long until it fails?"
Two heads sharing the same input (the residual window):
- **Classifier head:** which of our known fault types is this? Its answer is
  cross-checked against the incidence matrix (see below).
- **RUL head:** Remaining Useful Life, predicted at THREE quantiles (p10,
  p50, p90) using pinball/quantile loss — so you get a range with honest
  uncertainty, not a single fake-precise number. **Two RUL estimates always
  exist** — one from a physics damage-integration formula, one from this
  network — and we report the more conservative one. If they disagree a lot,
  that disagreement is itself flagged as a warning.

### The incidence matrix — the "free" explainability trick
Separately from any ML, we hand-derived a table: for each known fault type,
which of the 11 ρ channels should move, and in which direction. A live
residual vector gets compared (cosine similarity) against every row of this
table. **No training required for this half of the check** — it's pure
domain knowledge encoded once. When the classifier's guess and this table's
best match agree, that's the "two independent mechanisms, same answer" story.

- **IF this works:** the fault's "fingerprint" (which channels move) is
  genuinely distinct from other faults' fingerprints.
- **BUT — the honest caveat:** some fault pairs have IDENTICAL fingerprints
  in this table (e.g. detonation vs. a drifting EGT sensor look the same on
  paper). When that happens we do NOT guess — we flag it "ambiguous" and hand
  it to the active-diagnosis system (see below), rather than confidently
  naming the wrong one.

### The UKF — tracking slowly-drifting engine health
A Kalman filter is a way to estimate a hidden number you can't measure
directly, by combining a prediction with noisy measurements. We use the
"Unscented" variant (UKF) because our physics model is nonlinear (a plain
Kalman filter assumes linearity, which would be a bad approximation here).
It jointly tracks things like "compressor efficiency" and "injector discharge
coefficient per cylinder" — numbers that drift slowly as an engine wears, and
that you can't read off any single sensor. As a side effect, tracking these
per-cylinder makes distinguishing "the engine is wearing" from "one sensor
started lying" much easier — almost for free.

### Novelty detection — "I genuinely don't know"
This is the most important honesty feature in the whole system. We keep a
library of every fault's known "signature direction" in the 11-dimensional
residual space. A live residual vector gets projected onto the space spanned
by all known signatures; whatever's LEFT OVER (the part no known fault
explains) is the "novelty" score. High novelty = "something real is
happening, and it doesn't match anything I know" — the system says **"I do
not know"** instead of confidently guessing wrong.

- **WHY this matters for the pitch:** an unmanned aircraft has no crew to
  double-check the computer. A system that can say "I don't know" is more
  trustworthy than one that's always confident.
- **BUT — the honest limitation, and you should know this cold:** the margin
  for "genuinely unexplained" is narrow and gets narrower as we add more known
  faults to the library (more known directions = less room left over to be
  "novel"). This is a property of the *method itself*, not a bug — a bigger,
  better fault library structurally leaves less room for anything to look
  unfamiliar. Own this if asked; it's honest, not a weakness.

---

## N-CMAPSS — why we validate on someone else's data

Everything above was trained/tested on data OUR simulator generated. A sharp
judge will say: **"your model could just be memorizing your own simulator."**
The only way to answer that is to run the exact same pipeline, unmodified, on
data we did NOT generate.

N-CMAPSS is a real NASA dataset — actual turbofan (jet) engine run-to-failure
data under real recorded flight conditions. It's a **different engine type**
than our actual target (piston, not turbofan) — that's fine, it's not meant
to be "our model," it's meant to prove the *architecture and training
approach* isn't circular.

**Result (real, not synthetic — verified 2026-09-11):** RMSE 8.62 cycles,
NASA competition score 1352850, within-±10% band 35%. This is the evidence
behind the sentence: *"our ML pipeline ran unmodified on NASA's real
flight-condition data, and here's the number."*

---

## What we openly do NOT claim (say this before a judge finds it)

- No individual technique here is novel — residual-based fault detection,
  physics-informed correction, Kalman filtering, subspace novelty detection
  are all established published methods. The claim is **correct assembly**
  around one frozen contract (the 11-number ρ vector), not invention.
- Not a strict PINN — a physics backbone with a learned correction.
- The novelty margin is narrow (see above) and will need re-checking as the
  fault library grows.
- Training data is synthetic — same as the field's own C-MAPSS benchmark used
  since 2008, so this isn't unusual, but say it plainly rather than let a
  judge "discover" it.
