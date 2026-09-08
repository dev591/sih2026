# Session Log — 2026-09-08

**PRAMANA / SIH26054.** Record of what was built, why, and what was learned.
Written so the reasoning survives the session, not just the code.

---

## Starting state

The repo held a generic SIH team scaffold — `person1-research.md`,
`person3-qa.md`, stub files of 30–50 bytes — plus two source PDFs:

- **`PRAMANA_SIH26054_Technical_Design.pdf`** — the rigorous design: over-determination thesis, 11-element parity residual vector, fault incidence matrix, UKF joint state–parameter estimation, active diagnosis, validation without ground truth.
- **`Engine Twin War Room.pdf`** — the opinionated build plan: six layers, ten-fault signature matrix, three models, day-by-day schedule, four-minute demo script, cut list.

Zero lines of application code. Branches `backend` and `ml` were stale doc-only
forks predating everything.

---

## What was built

### Day 0 — the contracts (PR #2)

| Artifact | Why it exists |
|---|---|
| `docs/spec/telemetry-schema.md` | The three-lane contract, **frozen**. Carries `engine_id` from message one so the twin-engine differential is available later without a schema change. |
| `docs/spec/residual-spec.md` | ρ₁–ρ₁₁, incidence matrix, isolability analysis *including what does not separate*, active diagnosis, UKF state vector. |
| `config/engine_vrde_180.yaml` | Primary. Every block carries a provenance marker. Declares `parity_paths` honestly — Path 4 is `false` on an unthrottled FADEC diesel. |
| `config/engine_rotax_914.yaml` | Fallback and transfer target. Limits verbatim from EASA TCDS E.122, including `egt_C: null`. |
| `docs/pitch/demo-script.md` | The four minutes, written before the code, with a fallback per layer. |

**The point of Day 0:** three developers can build simultaneously because the
contracts exist. The frontend was never blocked on the backend at any point in
this session, by design.

### Team documents (PR #2, #5, #8)

- `work-breakdown.md` (29pp) — one section per person: mission, ownership, deliverables with paths, day-by-day definition of done, explicit "what you must not do". Ends with the **Contracts** section.
- `team-briefing.md` (20pp) — the same system with no engineering background assumed, for the three non-developers. Four-witnesses analogy for over-determination. Eight rehearsable judge answers.
- `SOLUTION-ANALYSIS.md` (15pp) — mechanism end to end, one fault traced through eleven stages, portability, how DRDO would use it, built/specified/claimed split.
- `research/deployment-path.md` — the dyno → shadow mode → read-only → commanding staircase, and the advisory-vs-commanding distinction.
- `BE-1-START-HERE.md`, `BE-2-START-HERE.md` — day-one companions. The 29pp reference does not tell someone which file to create first.

### Frontend — the ground control station (PR #2, #3, #4, #6, #7)

Vite + React + TS + three.js + uPlot + zustand.

- **Engine3D** — interactive twin from named primitives, no downloaded GLB. Finned barrels, rocker covers, pushrod tubes, intake plenum and runners, exhaust on CatmullRom curves merging into the turbine volute, reduction gearbox and prop flange. Three-point studio rig, contact shadows, ACES tone mapping, selective bloom.
- **StripChart** — uPlot with the twin's prediction dashed underneath.
- **Panels** — residual heatmap, diagnosis with hypothesis ranking, UKF health parameters with covariance, two-headed RUL, threshold monitor kept permanently on screen.
- **ExplainDrawer** — residual contributions against the derived incidence row.
- **Scrubber** — global timeline driving every panel and the 3D model.
- **MissionMap** — route coloured by risk, point-of-no-return from *degraded* BSFC, draggable cruise altitude.
- **CrossEngine** — Δ = A − B, with a common-mode warm-air-mass event so the cancellation is demonstrable.
- **TwinConfidence** — the novelty channel.
- **FaultConsole** — interactive injection with blind mode.
- **net/feed.ts** — WebSocket client that upgrades from simulated to live with no reload, with a stale-frame timer and a prominent `LIVE` / `SIMULATED` badge.

---

## Decisions worth remembering

**Residuals, never raw signals.** Everything downstream consumes ρ. This is the
decision that separates the build from most submissions, and it is why the
detector does not fire every time the throttle moves.

**Faults are parameter perturbations.** Degrade an injector discharge
coefficient and let EGT, fuel flow, λ and the crank ripple move on their own.
Nobody hand-authors what a fault looks like. The generator obeys this; BE-1's
MVEM must too.

**A sensor fault perturbs the measurement only, never the engine state.** If
this asymmetry is not true in the data, the headline demo is a lie.

**uPlot, not Recharts.** Recharts re-renders the React tree per frame and drops
frames at 50 Hz with eight series.

**Primitives, not a downloaded model.** A GLB arrives as hundreds of unnamed
meshes; you cannot tell which is cylinder two. Every mesh here is a named
variable, so binding health state to material is three lines.

**No CDN-dependent assets.** Rejected drei's `<Environment preset>` because it
fetches an HDR at runtime. Assume the venue wifi fails.

**One code path for script and sandbox.** The rehearsed mission is just one
`FaultConfig`. There is deliberately no separate demo mode whose behaviour could
diverge from what is being demonstrated.

---

## Bugs found, and what each one teaches

These are recorded because several were only findable by *looking at the running
thing*, and because the same classes will recur.

### 1. StrictMode mirrored the engine

Double-invoked effects mounted the r3f Canvas twice; OrbitControls initialised
against a stale instance and cylinders came up reading 4-3-2-1 on alternate
reloads. Harmless in production, but an unpredictable default camera in a
rehearsed demo is a real hazard. **StrictMode removed.**

### 2. Colour ramps anchored to absolute temperatures

A healthy engine glowed amber, because the CHT ramp started at 60 °C against a
200 °C limit. Later, switching to the Rotax profile lit the whole exhaust
manifold red, because the glow anchor was tuned to the VRDE's EGT range.

**Lesson: any colour that means "something is wrong" must be anchored relative
to the active engine.** A permanently glowing engine carries no information when
a real fault arrives.

### 3. Twin confidence read 54% on a healthy engine

Isotropic noise puts √(3/11) ≈ 0.52 of itself in the null space *by
construction*, so ν is high whenever there is nothing to explain. A confidence
channel that panics at idle gets ignored exactly when it matters. **Fixed by
weighting ν by residual significance.**

### 4. The incidence matrix treated ρ₆–ρ₉ as one direction — the important one

Found by injecting an in-library fault through the console and watching the
system report *"Unrecognised excitation 98%"*.

A single-cylinder fault does not lift all four thermal deviations equally. It
lifts **its own** and pushes the other three slightly negative, because each is
measured against the **conditional mean across cylinders**. Sum-to-zero is a
property of the residual definition.

```
                                       nu BEFORE   nu AFTER
EGT sensor drift, cyl 4 (in library)     0.932      0.000
Injector fouling, cyl 2 (in library)     0.610      0.000
Unmodelled fault (null space)            0.000      1.000
```

**It was crying wolf on two thirds of its own library while scoring the
genuinely unknown fault at zero — exactly backwards.** The detector was right;
the matrix was wrong.

Fixed by expanding per-cylinder faults into one column each. **Rank 8 → 10, null
space 3 → 1.** The claim survives but is now much narrower, and the spec says so.

> This bug was only findable because the console let a fault be injected that
> the script never used. That is an argument for the console independent of the
> demo.

### 5. "A new engine is a config file" was false in the code

The YAML profiles existed and read beautifully. **Nothing loaded them.**
Cylinder count, displacement, every redline, critical altitude — all hardcoded
in the frontend. A judge could have asked for a demonstration and there would
have been none.

Now enforced: `frontend/src/config/engines.ts` is the single source, and
switching profiles re-derives geometry, limits, critical altitude, fuel
properties, colour ramps, and **which parity paths exist**.

### 6. Engine A and B disagreed about the outside air temperature

The common-mode offset was applied to engine B's ambient but not engine A's —
two engines on the same wing reporting different OAT. Also, the common-mode
claim was originally measured on CHT, which is combustion-driven and barely
moves with ambient: it read **+0.4 °C** while asserting *"every absolute channel
moved"*. Now measured on intake air temperature, which actually tracks ambient,
with IAT in the table so the claim can be checked against the numbers.

**Lesson: weak evidence for a strong claim is exactly what an evaluator
notices.**

### 7. Script beats out of chronological order

The warm-air-mass beat was inserted mid-array, so the bar read 2:45 then 2:00 —
*and* the "which beat is active" logic compares against the next entry, so it
was silently highlighting the wrong chip. Now sorted, with a comment saying it
must stay that way.

---

## Open items

- **`git push origin --delete backend ml`** — stale doc-only forks predating the schema. Verified to hold zero commits not already in `main`. Blocked as destructive for the assistant; must be run by a human.
- **Post-flight PDF** — closes PS component F.
- **Demo script update** — write in where the judge takes over.
- **Keyboard shortcuts unverified** — beat-bar number keys and Space could not be tested through browser automation (synthetic key dispatch does not reach the window listener; the pre-existing Space shortcut fails the same way). Needs one manual check.
- **Team names** — tables in the team documents still have blanks.

---

## State at end of session

Everything merged to `main` (PRs #2–#8). Verified by cloning `main` fresh from
GitHub: `npm install`, typecheck and production build all clean first try.

The frontend is roughly at **Day 4** of the six-day plan. **BE-1 and BE-2 are
the critical path**, and both have start-here guides with a concrete first task
that produces a visible win on day one.
