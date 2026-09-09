# BE-1 — Day 2 Plan

**Written from the frontend side, after pulling your Day-1 commit, running
it, and instrumenting the model.** Read `BE-1-START-HERE.md` and
`docs/spec/residual-spec.md` first — this assumes both and doesn't repeat
them.

**How to read this.** Everything below is labelled:
- **[verified]** — I ran it and saw the number quoted.
- **[deduced]** — I traced it in code but did not execute that path.
Nothing here is a guess. Where I'm unsure, it says so.

---

## Day 1: what shipped, and two bugs already fixed for you

Your `404c87b` is real work — five MVEM states, a clean measurement layer,
the sensor/engine asymmetry done correctly, and a `verify.py` that actually
tests something. The socket serves schema-valid frames and **the dashboard
badge does go LIVE** [verified]. Task 1 of your start-here guide is done.

Two bugs are already fixed in a follow-up commit — just `git pull`, don't
redo these:

1. **`Q_LHV_J_per_kg: 43.0e6` parsed as the string `"43.0e6"`, not a
   float** [verified], in both engine YAMLs. PyYAML's safe-load float regex
   requires a *signed* exponent. This threw on the first `MVEM.step()` call
   — your own `verify.py` was failing on it. Fixed to `43.0e+6` / `43.5e+6`.
   Worth internalising: a YAML scalar that looks numeric isn't necessarily
   parsed as one, and it fails far from where you wrote it.
2. **`parity/sigma_generator.py` was a 0-byte stub.** Implemented — samples
   the healthy plant/twin pair across 9 operating points, writes
   `backend/config/sigma_vector.json`. `main.py` was silently falling back
   to `[1.0]*11`, i.e. every ρ was in raw units, not σ units. Re-run it
   after any MVEM retune: `python3 -m parity.sigma_generator`.

Also removed a landmine: ρ₃'s placeholder was byte-identical to ρ₁, so
enabling `parity_paths.intake_restriction` on a future engine would have
silently double-counted Path 1 as Path 4. It now raises instead.

---

## TASK 0 — The air path is collapsed. Everything else waits on this.

This is the one that matters. Do it before faults.

### What I measured

I ran your MVEM at the operating point `main.py` hardcodes (18 000 ft,
72 % throttle) for 120 s and printed the states [verified]:

```
   t     rpm   p_im/Pa  turbo_rpm      m_a      m_c      kW
   1     151     20240         95  0.00060  0.00000     0.3
  10    1557     20240         95  0.00638  0.00001     2.2
 120    1564     20240         95  0.00640  0.00001     2.2
```

Read those numbers against the engine you're modelling:

| Quantity | Your model | Should be | Ratio |
|---|---|---|---|
| Crank speed | 1 564 rpm | 3 580 rpm (frontend `cruiseRpm`) | 0.44× |
| Manifold pressure | 20 240 Pa | boosted, above the 50 599 Pa ambient | — |
| Turbo shaft | 95 rpm | ~118 000 rpm | 0.0008× |
| Brake power | 2.2 kW | 134.2 kW rated | 0.016× |

`p_im = 20 240 Pa` is **exactly** `p_atm × 0.4` — it is sitting on your
clamp floor (`mvem.py:129`). `turbo_rpm = 95` is **exactly** the
`max(w_tc, 10.0)` rad/s floor (`mvem.py:128`). Both states are pinned to
their guard rails, not converged. The engine is running at a fraction of
ambient pressure with a dead turbocharger, and 1 564 rpm is simply all the
air it can find. A 180 hp engine producing 2.2 kW at cruise is not a
tuning problem — the air path has failed structurally.

### Three distinct defects cause this

**A. The turbo shaft integration is stiff and unstable** [deduced].
`J_tc = 2.0e-5 kg·m²` with explicit Euler at `h = dt/100 = 0.01 s`:

```python
dw_tc_dt = (self.eta_m_tc * P_turb - P_comp) / (self.J_tc * max(self.w_tc, 1.0))
```

Dividing a power imbalance by `2e-5 × w_tc` produces enormous derivatives.
Any early transient sends `w_tc` through the floor within a few substeps.
Fix by one of: a much smaller substep for this state only; a semi-implicit
update; or — my recommendation for a 6-day build — **treat the turbo shaft
as quasi-steady**, solving `η_m·P_turb = P_comp` algebraically for `w_tc`
each step. You lose turbo lag, which nothing in the demo depends on, and
you gain a state that cannot explode.

**B. The collapsed state is unrecoverable by construction** [deduced].

```python
p_ratio   = max(self.p_im / p_atm, 1.0)
p_ratio_t = max(self.p_im / p_atm, 1.0)
P_comp = (...) * (p_ratio   ** ((gamma-1)/gamma) - 1.0)
P_turb = (...) * (1.0 - (1.0/p_ratio_t) ** ((gamma-1)/gamma))
```

Once `p_im < p_atm`, both ratios clamp to `1.0`, so `P_comp = 0` **and**
`P_turb = 0`, so `dw_tc_dt = 0` **exactly**. The turbo can never spin back
up. It's an absorbing state — once entered, never left. That's why the
numbers are frozen from t=10 s to t=120 s.

Separately: `p_ratio_t` for the *turbine* should be exhaust-manifold
pressure over ambient, not *intake* manifold pressure over ambient. Using
`p_im` for both machines is physically wrong even when it isn't clamped.

**C. `m_c` is not a compressor map, and this breaks ρ₁** [verified —
this one has a second, independent symptom].

```python
self.m_c = self.m_a * (self.w_tc / (118000 * 2 * np.pi / 60))
```

Compressor flow is slaved to engine flow. Two consequences:

1. When `w_tc` sits at nominal, `m_c ≡ m_a` exactly, so
   `dp_im/dt = (R·T_im/V_im)·(m_c − m_a) ≡ 0` — manifold pressure can
   never leave its initial value. And that initial value is `101325.0`
   (`mvem.py:34`), i.e. **sea level, regardless of altitude**.
2. `residual-spec.md` defines **ρ₁ = ṁ_SD − ṁ_C**, speed-density versus
   the compressor path. If `m_c` is derived *from* `m_a`, ρ₁ has no
   independent information in it — structurally, not approximately.

Here's the corroboration: when I ran the σ bootstrap, **σ(ρ₁) came out at
the 1e-3 floor** — zero variance across all 9 operating points. Same for
σ(ρ₅). I noticed that before I found this defect; it's the same bug
showing up from the other end. **Compressor fouling — one of your ten
faults, and one of the seven "strongly isolable" modes in the spec — can
never be detected while `m_c` is computed this way.**

### Why you couldn't have got this right yet

Your config references three lookup tables that **don't exist in the
repo** [verified — `ls config/` shows only the two YAMLs]:

```
compressor:  map: compressor_map.csv
volumetric_efficiency:  map: lookup_eta_v.csv
propeller:  cp_map: prop_cp_map.csv
```

So you had no compressor map to implement Path 2 against, and you
substituted a placeholder. That's a reasonable call under time pressure —
but the placeholder is now load-bearing for a headline claim, so it has to
be replaced. **Generate the three CSVs as committed artifacts** (an
analytic surrogate is fine — an ellipse-family compressor map, a smooth
η_v(p_im, N) surface, a standard C_P(J) curve), commit them, and cite them
as "assumed" provenance exactly like the YAML blocks do. A documented
surrogate you can defend out loud beats a magic number you can't.

### While you're in there: the hardcoded constants

`BE-1-START-HERE.md` says *"do not hardcode an engine constant… 'a new
engine is a config change' is one of our five differentiating claims."*
Currently hardcoded in `mvem.py` [verified]: `eta_i = 0.40` (line 79),
friction coefficient `6.2` (83), load-model `100.0` and `380.0` (85),
`eta_t = 0.7` (108), turbo nominal `118000` (115), `oil_press_bar = 3.42`
and `oil_temp_C = 96.3` (144), initial `p_im = 101325.0` (34).

The load model is the important one. You have a `propeller:` block in the
YAML (D = 1.65 m, 3 blades, gear_ratio 1.0) and `residual-spec.md` §ρ₅
says the propeller **is** the dynamometer: `P = C_P(J)·ρ·n³·D⁵`. Replacing
`T_load = (throttle/100)·100·(w/380)²` with the propeller law fixes the
load model *and* gives ρ₅ its actual physical basis in one change.

### Sanity target

With a healthy air path, the torque balance is roughly flat-torque against
a square-law load, which lands near **3 590 rpm** — within 0.3 % of the
frontend's `cruiseRpm: 3580`. That's a back-of-envelope check assuming
η_v ≈ 0.9 and p_im ≈ 1 bar, not a specification — but if your converged
cruise speed is in the 3 500–3 600 rpm band and brake power is near
134 kW, the air path is healthy. If it's at 1 564 rpm, it isn't.

---

## TASK 1 — Accept fault commands over the socket

I ran the frontend against your live backend and clicked "Inject fault" in
the Fault Console. **Nothing happened** [deduced from code — I could not
click it live because the browser extension wasn't connected, but the path
is unambiguous in `missionStore.ts`].

Your WebSocket is send-only — no receive loop in `main.py`, so the
frontend has no way to say "inject injector fouling on cylinder 2 now."
`FaultConsole.tsx` → `missionStore.applyConfig` only mutates the
frontend's *own* mock generator, and `missionStore.ts:170` drops mock
ticks entirely once `source === 'live'`. So going LIVE today is a demo
**downgrade**: the judge-facing interactive fault console stops working.

### The message contract — reuse the frontend's, verbatim

This is deliberate: reuse means the Fault Console needs **zero** frontend
changes, and SIMULATED vs LIVE stay behaviourally identical (the "one code
path for script and sandbox" decision already in force here). From
`frontend/src/mock/missionGenerator.ts`:

```ts
interface FaultSpec {
  startT: number;   // seconds into the run at which this fault begins
  cyl?: number;     // 0-indexed cylinder, where per-cylinder
  rate: number;     // severity rate; units depend on the fault
}

interface FaultConfig {
  injector?:   FaultSpec;  // rate = fraction of C_d lost per minute
  turbo?:      FaultSpec;  // rate = fraction of eta_c lost per minute
  cooling?:    FaultSpec;  // rate = fraction of hA lost per minute
  bearing?:    FaultSpec;  // rate = friction fraction gained per minute
  chtSensor?:  FaultSpec;  // rate = degC per minute of bias
  egtSensor?:  FaultSpec;  // rate = degC per minute of bias
  unmodelled?: FaultSpec;  // BE-2's novelty channel — ignore for now
  warmAirMass?: boolean;   // common-mode OAT offset, must hit BOTH engines
}
```

Wire it as `{"type": "fault_config", "config": {...}}` and
`{"type": "reset"}`. Run the receive loop concurrently with your send loop
via `asyncio.gather`; FastAPI supports concurrent send/receive on one
socket. **Keep the fault state per-connection**, next to your existing
per-connection `plantA`/`twinA`/`plantB` — module-level state means two
judges on two tabs fault each other's engine.

I have not written or run this code, so treat any snippet as a sketch, not
a tested implementation — the *contract* above is the part that's fixed.

**My side:** `feed.ts` needs a `send(config)` gated on `source === 'live'`,
wired to the Fault Console. ~2 hours once your receive loop exists. Ping me
the moment the contract is live — I don't need `faults.py` finished to
start.

---

## TASK 2 — The six faults, as parameter ramps

`backend/twin/faults.py`. Ramp, never step — `rate` is **per minute**:

```python
elapsed_min = max(0.0, (t - spec['startT']) / 60.0)
severity = spec['rate'] * elapsed_min
```

This matches the frontend's `elapsedMin` exactly [verified — I read
`trueStateAt`/`sensorBiasAt` to confirm]. **Use these clamps**, they're
the frontend's actual values, and matching them means LIVE and SIMULATED
degrade in the same shape:

| Frontend field | MVEM param | Update | Clamp |
|---|---|---|---|
| `injector` (`cyl`) | `params['cd_inj'][cyl]` | `1 − sev` | `≥ 0.4` |
| `turbo` | `params['eta_c_scale']` | `1 − sev` | `≥ 0.55` |
| `cooling` | `params['hA_scale']` | `1 − sev` | `≥ 0.5` |
| `bearing` | `params['f_fric_scale']` | `1 + sev` | `≤ 2.2` |
| `chtSensor` (`cyl`) | `sensor_biases['cht_C'][cyl]` | `+ sev` | — |
| `egtSensor` (`cyl`) | `sensor_biases['egt_C'][cyl]` | `+ sev` | — |

The last two rows go through `sensor_biases` and **never** through
`plantA_params`. That asymmetry is already correct in your
`measurement.py` — this just has to not break it. Note the frontend leaves
`eta_v_scale` at 1.0 always; ring wear is a Day-3 fault.

`warmAirMass` is common-mode: add a fixed OAT offset to the **shared**
`isa()` call feeding both engines. You already got the sharing right
(`# Same atm for A and B`), so this is one argument, not a new mechanism.

**Also un-hardcode the operating point.** `main.py` pins
`altitude_ft = 18000, throttle_pct = 72` forever. Even a slow climb/cruise
profile makes the operating-point-invariance property *visible* rather
than merely asserted — and it's what makes ρ's flatness under throttle
movement demonstrable to a judge.

---

## TASK 3 — Healthy dataset for BE-2

`parity/sigma_generator.py` already does ~90 % of this — same sampling
loop, just log full frames instead of only ρ. Write
`data/healthy_flights/*.parquet`: one row per (operating point, timestep),
full `slow`/`fast`/`health.rho`, zero faults. BE-2 needs it for M2's
healthy-only threshold (`docs/backend/ML-DEEP-DIVE.md` §4 has the reason;
you don't need to read it to produce the file).

**Do this after Task 0.** A healthy dataset generated from a collapsed air
path would train BE-2's autoencoder on a broken engine — and that error
would surface on Day 4 looking like an ML problem, which it wouldn't be.

---

## Definition of done, Day 2

- [ ] **Task 0:** cruise converges near 3 500–3 600 rpm with brake power
      near rated; `p_im` and `w_tc` off their clamp floors; `m_c` from a
      real map, not from `m_a`
- [ ] The three map CSVs exist, committed, provenance labelled
- [ ] `σ(ρ₁)` and `σ(ρ₅)` come off the 1e-3 floor after re-running the
      bootstrap — that's the objective test that ρ₁ carries information
- [ ] Engine constants read from YAML; `T_load` uses the propeller law
- [ ] `main.py` has a receive loop; a `fault_config` message visibly moves
      the live feed
- [ ] `faults.py` implements all six ramps with the clamps above
- [ ] `verify.py` extended: one assertion per fault — plant state moves,
      twin state doesn't; sensor faults leave `plant.get_outputs()`
      bit-identical
- [ ] `data/healthy_flights/*.parquet` handed to BE-2
- [ ] Re-run `python3 -m parity.sigma_generator` last, after Task 0

---

## How I'll test this from the frontend side

1. Badge goes LIVE (already working), engine holds cruise rpm — not 1 564.
2. `fault_config` for `injector` cyl 1 → `egt_C[1]` and `ripple` move,
   `rho6_9_cyl_dev` deviates **for that cylinder specifically**.
3. `chtSensor` cyl 2 → the `cht_C[2]` *reading* moves while unrelated
   channels stay flat. That's the sensor-vs-engine asymmetry surviving the
   live wire, not just `verify.py`.
4. `turbo` fault → **ρ₁ must move.** If it doesn't, defect C is still
   there.
5. Two browser tabs on one backend, fault injected from one — the other
   tab's engine unaffected.

---

## Order of work, if you only read one section

1. Task 0 — the air path. Nothing downstream is meaningful until the
   engine makes rated power.
2. Task 1 — the receive loop contract (unblocks me in parallel).
3. Task 2 — the six ramps.
4. Task 3 — the dataset, last, so it's generated from a healthy engine.

Ask early rather than late. Every defect in this document was found by
running the thing and printing numbers — that's a habit worth more than
any of the individual fixes.
