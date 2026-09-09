# Known Issues

Open defects with their demo-day workaround. **An issue with a workaround
written down is a non-event on stage; the same issue discovered live is a
lost minute.** Anything here that has no workaround is a blocker.

Last reviewed: **2026-09-09**.

---

## FE-1 · WebGL context lost when toggling Simple ⇄ Expert

**Severity:** cosmetic · **Blocker:** no · **Owner:** frontend

`App.tsx` switches views with a ternary (`{simple ? <SimpleView/> :
<ExpertGrid/>}`), so React unmounts one subtree and mounts the other. Both
contain `<Engine3D/>`, so the `<Canvas>` and its WebGL context are destroyed
and rebuilt on every toggle. Console shows
`THREE.WebGLRenderer: Context Lost`, and the 3D view is blank for roughly
two seconds before it recovers on its own.

**Workaround for demo day:** choose your view *before* you start speaking
and stay in it. The rehearsed script never toggles mid-beat. If a judge asks
to see the other view, toggle it during a natural pause and keep talking —
it recovers by itself and needs no intervention.

**Why it is not fixed yet.** The correct fix is a single hoisted `<Canvas>`
positioned by CSS into whichever view is active, rather than one instance
per view. The cheap alternative — keeping both views mounted and hiding one
— is worse here, because the Canvas carries an `EffectComposer` with Bloom
and SMAA, and two postprocessing pipelines cost more than the bug does.
The real fix is a layout change to the demo centerpiece and must be
verified visually before it is trusted. **Do not attempt it in the last
48 hours before judging.**

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
