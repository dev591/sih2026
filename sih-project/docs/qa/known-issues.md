# Known Issues

Open defects with their demo-day workaround. **An issue with a workaround
written down is a non-event on stage; the same issue discovered live is a
lost minute.** Anything here that has no workaround is a blocker.

Last reviewed: **2026-09-09**.

---

## SPEC-1 · `detonation` and `egt_sensor_drift` are structurally inseparable

**Severity:** high · **Blocker:** no (handled) · **Owner:** BE-1 + BE-2 + spec

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

**Still open for the team to decide:** whether ρ₁₁ (0.5-order ripple) or
`fast.knock_intensity` should carry a non-zero entry for detonation. Physically
a detonating cylinder is a combustion abnormality and a drifting thermocouple
is not, so a channel almost certainly separates them — but changing an
incidence row changes the contract for the frontend, the matcher and the
novelty rank simultaneously, so **do not change it unilaterally.**

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

**Severity:** critical · **Blocker:** yes, for any LIVE demo · **Owner:** BE-1

At the operating point `main.py` hardcodes, `p_im` sits at exactly
`p_atm × 0.4` and `turbo_rpm` at exactly 95 rpm — both pinned to their clamp
floors, not converged. Cruise settles at 1 564 rpm against the frontend's
`cruiseRpm: 3580`.

Three causes: stiff explicit-Euler integration of the turbo shaft
(`J_tc = 2e-5`); an absorbing state where `p_im < p_atm` clamps both
pressure ratios to 1.0, zeroing `P_turb` *and* `P_comp` so `dw_tc/dt = 0`
exactly; and `m_c` slaved to `m_a` instead of read from a compressor map.

**Workaround for demo day:** present in SIMULATED mode (see FE-2 — same
workaround covers both).

**Fix:** `docs/team/BE-1-DAY2-PLAN.md` Task 0. Objective pass condition:
σ(ρ₁) and σ(ρ₅) come off the 1e-3 floor when the bootstrap is re-run.

---

## BE-1 · ρ₁₀ carries no signal — no oil system model

**Severity:** high · **Blocker:** no · **Owner:** BE-1

`mvem.py` returns `oil_press_bar: 3.42` and `oil_temp_C: 96.3` as hardcoded
constants. Plant and twin therefore emit the same value, so ρ₁₀ is pure
measurement noise. Confirmed numerically: σ(ρ₁₀) = 0.222, which is exactly
the oil sensor noise (0.02 ÷ 3.42 × 40).

**Consequence:** oil leak and pump wear cannot be detected — one of the ten
faults, and one of the seven modes `residual-spec.md` calls strongly
isolable.

**Workaround:** do not offer an oil fault in a judge-driven sandbox until
the model exists. Every other fault in the console is genuine.

**Fix:** an oil pressure model as a function of speed and oil temperature.
Small — a few lines — but nobody has scoped it.

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
