# The backend, explained simply

**For:** whoever owns the physics twin / server (main.py, twin/, parity/).
**Goal:** explain WHY it's built this way, HOW the pieces fit, IF-conditions
that must hold, and BUT — where it's fragile — in plain language.

---

## The one-sentence job of the backend

**Simulate (or receive) an engine's raw sensor readings, run them through a
physics model to get what those readings SHOULD be, subtract the two to get
residuals, and stream all of it to the frontend once a second over a
WebSocket.** Everything else — fault injection, altitude commands, the
damage/RUL calculation — hangs off that one loop.

---

## The physics twin (MVEM) — WHY and HOW

**MVEM = Mean-Value Engine Model.** Instead of simulating every individual
combustion event millisecond-by-millisecond (way too slow to run live), it
models the *averaged* behavior over each cycle — intake, boost, combustion,
exhaust, thermal — as a set of coupled differential equations. It's the
standard technique for real-time engine simulation, not something we invented.

- **IF valid:** the equations are calibrated close enough to a real
  turbocharged piston aero-engine (VRDE-180 / Rotax-914 specs) that its
  outputs are physically plausible, even though it's not a exact copy of any
  one real engine.
- **BUT — the thing that bit us tonight:** every constant in these equations
  has to be independently correct, because they interact. We found `h_air`
  (a heat-transfer number for the cylinder head) hardcoded at 50.0 in code
  while its five sibling constants all lived properly in the engine's YAML
  config — and it was ~9x too small, so a HEALTHY engine's temperature
  climbed to ~540°C instead of settling at ~140°C. It "looked right" for the
  first 20 seconds because the model starts warm, then drifted. **Lesson:**
  any constant not in the YAML config is a red flag — check it's actually
  right, don't assume.

## Residuals (ρ) — the contract between backend and ML

The backend's most important output is an 11-number vector ρ, computed as
**measured − predicted** for several independent ways of checking the same
physical quantities (e.g., air mass flow can be computed 3-4 different ways
from different sensors — if they disagree, something's wrong with one of
those sensor paths specifically). Each number is normalized by its own
"healthy noise" standard deviation, calibrated once by running the healthy
simulator many times (`sigma_generator.py`) and stored in `sigma_vector.json`.

**IF you change ANY physics constant** (like we did with `h_air`), the
"normal noise level" of ρ may shift too — so `sigma_vector.json` needs
regenerating, or every downstream "how many sigma is this deviation" number
becomes wrong. We verified after tonight's fix: only the per-cylinder thermal
channels' sigma moved, everything else was bit-identical — that's the
expected, contained blast radius of a targeted fix.

## Fault injection — WHY it's a physics change, not a data hack

Faults are NOT "paste an anomalous number into the data stream." They are
**parameter changes inside the same physics equations** — e.g., "injector
discharge coefficient drops 0.045%/min" for injector fouling. This matters
because:
1. The fault's effect on all 11 residuals propagates CORRECTLY and
   consistently — you get physically coherent signatures, not made-up ones.
2. You get exact ground-truth RUL labels for free, because you know exactly
   when and how fast you broke something.
3. The SAME code path handles the scripted demo and a judge injecting a
   fault live — there's no separate "demo mode" that behaves differently
   from what's actually being demonstrated.

**BUT:** this only works if the fault parameter genuinely maps to something
physically real in the equations — a "fault" that's just noise injected on
top wouldn't have this property.

## The damage integrator — WHY it exists

Separately from the ML's RUL prediction, there's a physics-based
damage-accumulation formula (Archard wear + Arrhenius temperature-acceleration
laws — standard mechanical fatigue models) that integrates forward from
current conditions to estimate remaining life. This is the "physics" RUL head
that gets compared against the network's RUL head (see the ML doc) — two
independently-derived numbers that should roughly agree.

- **IF valid:** the wear-law constants are calibrated to give plausible
  failure timescales (we found and fixed a bug here too — `K_ARRHENIUS` was
  off by roughly 10,000x, causing damage to hit "fully failed" in about 1
  second of simulated time regardless of fault severity).
- **BUT:** these constants are estimates from published failure-mode
  literature, not measurements from a real destroyed engine — say this
  honestly if asked, it's disclosed in the docs already.

## The WebSocket server — HOW live data actually flows

One FastAPI server, one endpoint (`/ws/telemetry`). Every second, it:
1. Computes current altitude/throttle (either the scripted profile, or a
   commanded ramp if the GCS asked for a climb/descent).
2. Runs the physics twin forward one step.
3. Computes ρ (residuals).
4. Runs the ML inference pipeline (if available) to get diagnosis/RUL/novelty.
5. Sends one JSON frame to the connected browser.

Each browser tab gets its OWN independent fault/altitude state (per-connection,
not global) — so two people can't accidentally fault each other's view.

**BUT — a real bug we found and fixed tonight:** the server was waiting for
BOTH its receive-loop and send-loop to finish before cleaning up a
connection. If a browser tab just closed (no proper goodbye message — which
is normal for a reload or a killed tab), the send-loop kept running FOREVER,
still doing ML inference once a second for a client that no longer existed.
After a few page reloads during testing, there were 17 dead connections still
computing — and frames slowed from 1-per-second to 1-per-8-seconds. **This is
exactly the kind of thing that quietly kills a live demo** if you reload the
page a few times while setting up. Fixed by cancelling whichever loop is
still running the moment the other one ends.

## What to say if a judge asks "is this all real code, or mostly spec?"

Be honest and specific rather than vague: point at what runs — `gates_check.py`
(6 physics sanity checks, all passing) and `verify.py` (4 residual-property
tests, all passing) are real, automated, and re-run before every demo. If
some piece is still spec-only, say exactly which piece and why — that's more
credible than claiming everything is finished.
