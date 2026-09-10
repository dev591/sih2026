# Known Issues

Open defects with their demo-day workaround. **An issue with a workaround
written down is a non-event on stage; the same issue discovered live is a
lost minute.** Anything here that has no workaround is a blocker.

Last reviewed: **2026-09-09**.

---

## SPEC-1 · `detonation` and `egt_sensor_drift` are structurally inseparable

**Severity:** high · **Blocker:** no · **RESOLVED 2026-09-10** · **Owner:** spec

Their rows in the fault incidence matrix are **identical** —
`[0, 0, 0, 1, 0, 2, 2, 2, 2, 0, 0]`. This is not an implementation slip: the
table in `docs/spec/residual-spec.md` §3 gives them the same excitation
pattern, and `ml/incidence.py` and `frontend/src/analysis/incidence.ts` both
mirror it faithfully. By the columns-differ criterion in §4, no method built
on that matrix can separate them.

**Why this one matters more than the other ambiguous pairs.** Detonation is a
COMPONENT fault actively damaging the engine. EGT sensor drift is
INSTRUMENTATION — the engine is fine. That is precisely the discrimination the
3:00 demo beat is built on. Undeclared, the system could report
"instrumentation fault, the engine is serviceable" for an engine that is
detonating, or send a good engine to teardown for a forty-rupee thermocouple.

Measured effect: `egt_sensor_drift` has the worst per-class recall in the
confusion matrix at **0.20**, confused with `detonation` 142 times out of
~240. The classifier is not failing — it is being asked to separate two
things the physics contract says are the same.

**Handled, as of the ML review.** `ml.incidence.inseparable_groups()` derives
these groups from the matrix instead of assuming them, and the inference
pipeline now:
- refuses to assert `is_sensor_fault` when the top hypothesis is in such a group,
- sets `ambiguous: true`,
- returns `inseparable_from` naming the other candidate.

That is the "the system knows which ambiguities its own structure cannot
resolve, and probes only those" behaviour of `residual-spec.md` §5 — a
strength when declared, a trap when not.

**RESOLVED.** `detonation` now carries **ρ₁₁ = +2**. A detonating cylinder is
an abnormal combustion event that rings the structure and shows in the
0.5-order crank ripple; a drifting thermocouple moves no ripple at all. The
entry restores a difference the physics always had and the table had omitted.
Changed in all three mirrors in one commit — `ml/incidence.py`,
`frontend/src/analysis/incidence.ts`, `docs/spec/residual-spec.md` §3 — with
the rationale recorded in §4. `inseparable_groups()` now returns `[]` and is
the regression test.

Measured effect on the oracle ceiling (best achievable by ANY model):
**0.643 → 0.687**. `egt_sensor_drift` was scoring 0.00 even for a perfect
classifier; it is now separable.

**BE-1 and BE-2 must both know this changed** — it moves the matcher, the
classifier's training signal and the novelty rank together.

---

## FE-2 · Fault Console has no effect while the feed is LIVE

**Severity:** high · **Blocker:** for a live-backend demo only · **Owner:** BE-1, then frontend

The Fault Injection Console drives the frontend's own mock generator
(`missionStore.applyConfig`). BE-1's WebSocket is send-only — there is no
receive loop — so there is no way to tell the backend which fault to inject.
`missionStore.ts:170` drops mock ticks entirely once `source === 'live'`, so
in LIVE mode the console goes inert.

**Consequence:** connecting to the live backend is currently a demo
*downgrade*. The judge-facing interactive fault selection — the strongest
beat we have — stops working.

**Workaround for demo day:** **present in SIMULATED mode.** The frontend
does the entire story end-to-end on its own. Mention the live backend and
the CAN layer as architecture, and treat a working LIVE feed as a bonus, not
a dependency.

**Fix:** tracked in `docs/team/BE-1-DAY2-PLAN.md` Task 1 (backend receive
loop, contract already specified), then ~2 h of frontend wiring to add
`feed.send(config)` gated on `source === 'live'`.

---

## BE-1 · Air path collapsed — engine makes 2.2 kW of a rated 134 kW

**Severity:** critical · **RESOLVED 2026-09-10** · **Owner:** was BE-1, fixed directly per PRAMANA-DIRECTIVE-2026-09-10.md §1

At the operating point `main.py` hardcodes, `p_im` sat at exactly
`p_atm × 0.4` and `turbo_rpm` at exactly 95 rpm — both pinned to their clamp
floors, not converged. Cruise settled at 1 564 rpm against the frontend's
`cruiseRpm: 3580`.

**RESOLVED.** Turbo shaft is now a kinetic-energy state (§1.1 — removes the
`1/ω` singularity that pinned it to the floor). Compressor is now the
Leufven & Eriksson Ellipse model, genuinely independent of `ṁ_a` (§1.2) —
verified directly: a 25% compressor fault now moves ρ₁ by **−44.9σ**
(before: exactly 0, structurally impossible). Turbine decoupled from the
intake side and no longer zeroed alongside `P_comp` (§1.3). Two additions
beyond the directive's literal equations, both necessary and disclosed as
such: a wastegate (without one, sea-level MAP ran away to 7.17 bar — over
3× rated) and a mechanical shaft-speed ceiling (without one, "critical
altitude" stopped being a real physical limit — colder inlet air at
altitude raises corrected speed at constant physical rpm, so compressor
capacity kept growing with altitude instead of running out). Also raised
`eta_i` 0.40→0.50 (defensible range for a modern common-rail turbodiesel)
and replaced the fuel schedule's fixed `lambda=1.42/throttle_frac` with a
linear-in-lambda schedule — the old one capped power at 33% of rated at
72% throttle even at sea level with zero altitude penalty; no compressor
retuning could have fixed that, it was a fuel-schedule ceiling.

All six of the directive's gates pass: MAP 1.399 bar (target 0.9–1.4),
turbo 103,265 rpm (target 80k–160k), power 61% rated / rpm 1.9% off target
(target ≥60% / ±5%), σ(ρ₁)=5.11 & σ(ρ₅)=0.42 (target off the 1e-3 floor),
ρ₁₀ responds to bearing wear (see below), altitude sweep flat to ~11–13kft
at 100% throttle then genuinely falls (critical altitude is a
max-continuous-power spec — the 72% throttle sweep the gate script
originally used stays flat past 20kft, which is physically correct, not a
bug).

**Rotax profile not gate-verified** — same MVEM code, but the backend
(`main.py`) hardcodes VRDE at module load and never serves Rotax, so this
does not reach the live demo. Left visibly unstable (oil temp runs to
490°C at some conditions) rather than silently calibrated — recalibrate
before relying on the cross-engine transfer demo.

---

## BE-1 · ρ₁₀ carries no signal — no oil system model

**Severity:** high · **RESOLVED 2026-09-10** · **Owner:** was BE-1, fixed directly per PRAMANA-DIRECTIVE-2026-09-10.md §1.6

`mvem.py` returned `oil_press_bar: 3.42` and `oil_temp_C: 96.3` as hardcoded
constants. Plant and twin therefore emitted the same value, so ρ₁₀ was pure
measurement noise. Confirmed numerically: σ(ρ₁₀) = 0.222, exactly the oil
sensor noise (0.02 ÷ 3.42 × 40).

**RESOLVED.** Real oil model: gear-pump flow, a viscosity-vs-temperature
term, laminar flow through the bearing clearance (which widens under
wear — modelled via the same `f_fric_scale` UKF parameter that already
drives friction torque, since Q/clearance⁴-type flow makes a modest
clearance increase produce a large, unambiguous pressure drop), a relief
valve, and a lumped thermal node. Verified: healthy 3.41 bar / 96.3°C
(steady-state lands almost exactly on the old hardcoded reference values —
no other threshold in the app had to move); bearing wear (`f_fric_scale`
1.6) drops it to 2.06 bar, a real Δ of 1.35 bar. Oil leak/pump wear is now
detectable — one of the ten faults, and one of the seven modes
`residual-spec.md` calls strongly isolable.

---

## Bundle size — 1.47 MB (452 kB gzipped)

**Severity:** cosmetic · **Blocker:** no

Vite warns above 500 kB. Almost all of it is three.js and postprocessing.
Irrelevant for a demo served from `localhost`; noted only so nobody
"discovers" it on the day and thinks it is a problem. Do not code-split
before judging — the risk is not worth the zero benefit.

---

## Resolved

| Issue | Fixed in |
|---|---|
| `Q_LHV_J_per_kg: 43.0e6` parsed as a string, breaking every MVEM step | `3488fa6` |
| `sigma_generator.py` empty — ρ never normalised to σ units | `3488fa6` |
| ρ₃ placeholder identical to ρ₁, would double-count Path 1 as Path 4 | `3488fa6` |
| Post-flight report in the script with no implementation | `e3a236b` |
| **FE-1** · engine blank for ~8 s on every Simple ⇄ Expert toggle | `a53c192` |
| Cylinder temperature tags drawing on top of the report | `a53c192` |
| Scrolling the report orbited the camera behind it | `a53c192` |
| Engine cropped at the bottom of the stage; verdict card overlapping it | `a53c192` |

**On FE-1, for the record.** It was logged here as "~2 seconds, cosmetic."
Measured in the browser it was **about eight seconds of blank white**, on the
view the demo opens in — the entry understated a demo-killer, because it was
written from reading the code rather than from running it. The console tell
was the shadow-map init warning appearing once per toggle. Fixed by hoisting
to a single persistent `<Canvas>`; three consecutive toggles now produce zero
such warnings. **Lesson worth keeping: time the symptom, do not estimate it.**
