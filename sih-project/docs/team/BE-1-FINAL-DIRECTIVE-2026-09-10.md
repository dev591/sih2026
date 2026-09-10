# BE-1 — Complete the backend, tonight, in one pass

**To:** BE-1, working on `github.com/dev591/sih2026`, branch `day0-foundation`
**Read first:** `docs/qa/known-issues.md` (top two entries), then this document.
**Judging: 12 September.** This is not a day-by-day plan. Everything below is
one push, tonight, to a finished backend. No partial credit for "started."

---

## UPDATE — read this before §1, it changes what's actually left

You pushed your own MVEM rewrite and fault-injection wiring (`0a1c5d5`,
`47edc41`) in parallel with this document being written, from the same base.
**Both were reviewed by running them, not by reading them**, against the same
six gates §0 describes. Full honesty, both directions:

- **Your fault-injection wiring — kept, merged in, verified live.** The
  WebSocket receive loop, `twin/faults.py`'s six faults, per-connection fault
  state, and the frontend `feed.send()` wiring are **all in `day0-foundation`
  now** (merge commit after `47edc41`). Tested end-to-end over the actual
  socket tonight: injected `injector` fault on cylinder 2, watched `egt_C[1]`
  move and `ρ₆₋₉[1]` read −5.26σ within 20 seconds. This is good work —
  **§1 below is done, do not redo it.**
- **Your MVEM/compressor rewrite — not kept.** Scored 1/6 on `gates_check.py`
  (26% rated power at the actual 18kft/72% cruise point, oil pressure still
  a hardcoded constant, ρ₁/ρ₅ still on the noise floor). Your commit's own
  quoted number — 137.5 kW — was measured at sea-level-100%-throttle, the
  easy case; the gates test the actual demo condition, which is harder. Your
  `verify.py` also didn't run on a fresh checkout (`UnicodeDecodeError` from
  a non-UTF-8 character in `compressor_map.csv` — worth knowing for next
  time: a script that "passes" on your machine but was never run on a clean
  clone hasn't actually been verified). §0's physics — Ellipse compressor,
  kinetic-energy turbo, wastegate, oil model — is what's live, calibrated
  against the same six gates your own commit was checked against.

**What this means for tonight: skip §1 entirely, it's done.** Start at §2
(the remaining four faults + the four new sensor faults — you have six of
ten). §3 (damage integrator), §4 (dataset regeneration — **do this against
the current physics, not your reverted version**), §5 (CAN, if time remains)
are all still fully open, unchanged from below.

---

## 0. What changed under you tonight — read this before touching `mvem.py`

Your Day-1 air path was structurally collapsed (turbo pinned at its floor,
compressor slaved to `m_a`, `ρ₁`/`ρ₅` structurally zero). It has been rewritten
tonight against a technical directive (`PRAMANA-DIRECTIVE-2026-09-10.md`, still
in the repo if you want the full derivation) and **all six of that directive's
acceptance gates now pass** — verified, not asserted:

```
python3 backend/gates_check.py
```

should print `6/6 gates passed`. If it doesn't, something changed under you —
stop and diff against `git log` before proceeding.

**What this means for you:**

- The turbo shaft is now a kinetic-energy state (`self.E_tc`), not `self.w_tc`
  directly — `w_tc` is a read-only property computed from it.
- The compressor is the Leufven & Eriksson Ellipse model, genuinely
  independent of `m_a` — do not reintroduce `m_c = m_a * (w_tc/w_tc_nom)`.
- There is a wastegate and a mechanical shaft-speed ceiling in the turbo loop.
  Both are load-bearing — without them MAP either runs away to 7 bar or
  never loses critical-altitude behaviour. Do not remove them "to simplify."
- The fuel schedule is `lambda = lambda_idle + (lambda_full-lambda_idle)*throttle_frac`,
  not the old `1.42/throttle_frac`. `eta_i = 0.50`.
- There is now a real oil model (`self.T_oil`, `self.oil_press_bar_val`),
  driven by `f_fric_scale` — ρ₁₀ carries real signal.
- There is now a real propeller load (`P_prop` via a Cp(J) curve), replacing
  the old throttle-scaled quadratic.

**Your job tonight is additive: wire fault injection and the damage model on
top of this, using the parameter hooks that already exist (`cd_inj`,
`eta_v_scale`, `eta_c_scale`, `hA_scale`, `f_fric_scale`) plus a small number
of new ones specified below. Do not rewrite the state equations.** If you
think one of them is wrong, say so in the PR rather than silently changing it
— every constant in there was calibrated against the six gates tonight, and
an uncoordinated change can break them without your realizing it.

Run `gates_check.py` again after you're done. It must still be 6/6.

---

## 1. The WebSocket receive loop — ✅ DONE, merged from your own push, skip to §2

**File:** `backend/main.py`.

Your socket is currently send-only. The frontend's Fault Injection Console
has nothing to talk to — confirmed live: connecting to your backend today is
a demo *downgrade*, because the console (the strongest beat in the script)
goes inert the moment the badge turns green. This is the single highest-value
thing you can ship tonight.

**Contract — reuse the frontend's own types verbatim, so it needs zero
changes on its side.** From `frontend/src/mock/missionGenerator.ts`:

```python
# One fault spec. cyl is 0-indexed, absent for engine-wide faults.
# rate's units depend on which field it's attached to (see the table below).
FaultSpec = {
    "startT": float,        # seconds into the run the fault begins
    "cyl": int | None,
    "rate": float,
}

# The full injected state. Every key is optional; absent = that fault is off.
FaultConfig = {
    "injector":    FaultSpec | None,  # rate = fraction of C_d lost per minute
    "turbo":       FaultSpec | None,  # rate = fraction of eta_c lost per minute
    "cooling":     FaultSpec | None,  # rate = fraction of hA lost per minute
    "bearing":     FaultSpec | None,  # rate = friction fraction gained per minute
    "ringWear":    FaultSpec | None,  # rate = fraction of eta_v lost per minute (NEW — see §2)
    "oilLeak":     FaultSpec | None,  # rate = fraction of pump flow lost per minute (NEW — see §2)
    "misfire":     FaultSpec | None,  # rate = probability of a skipped firing event, 0-1 (NEW — see §2)
    "detonation":  FaultSpec | None,  # rate = knock severity 0-1 (NEW — see §2)
    "fuelFilter":  FaultSpec | None,  # rate = fraction of fuel rail capacity lost per minute (NEW — see §2)
    "chtSensor":   FaultSpec | None,  # rate = degC/min bias — MEASUREMENT layer only
    "egtSensor":   FaultSpec | None,  # rate = degC/min bias — MEASUREMENT layer only
    "mapSensor":   FaultSpec | None,  # rate = hPa/min bias — MEASUREMENT layer only (NEW)
    "lambdaSensor":FaultSpec | None,  # rate = lambda units/min bias — MEASUREMENT layer only (NEW)
    "unmodelled":  FaultSpec | None,  # BE-2's territory — accept and ignore, do not implement
    "warmAirMass": bool,              # common-mode OAT offset, hits BOTH engines identically
}
```

Six of these fields (`injector`, `turbo`, `cooling`, `bearing`, `chtSensor`,
`egtSensor`) match the frontend's existing `FaultConfig` type exactly — do
not rename them. `ringWear`, `oilLeak`, `misfire`, `detonation`, `fuelFilter`,
`mapSensor`, `lambdaSensor` are new — the frontend needs a small, additive
type change to send them (same shape as the existing six), which the
frontend side will handle once your receive loop exists. Message envelope:

```python
{"type": "fault_config", "config": <FaultConfig>}
{"type": "reset"}
```

**Implementation.** FastAPI supports concurrent send/receive on one socket.
Keep fault state **per-connection**, next to your existing per-connection
`plantA`/`twinA`/`plantB` — module-level state means two judges on two tabs
fault each other's engine:

```python
import asyncio

async def receive_loop(websocket, state):
    async for msg in websocket.iter_json():
        if msg.get("type") == "fault_config":
            state["config"] = msg["config"]
        elif msg.get("type") == "reset":
            state["config"] = {}

# in telemetry_endpoint, after plantA/twinA/plantB are created:
state = {"config": {}}
recv_task = asyncio.create_task(receive_loop(websocket, state))
try:
    while True:
        # ... existing send loop, but plantA_params is now built from
        # state["config"] via the ramp function in §2, not always nominal_params
        ...
finally:
    recv_task.cancel()
```

---

## 2. The ten faults, as parameter ramps

**New file:** `backend/twin/faults.py`.

Ramp, never step — this matches the frontend's own `elapsedMin` exactly,
verified against its source:

```python
def elapsed_min(t: float, spec: dict | None) -> float:
    if spec is None or t < spec["startT"]:
        return 0.0
    return (t - spec["startT"]) / 60.0
```

### Faults that map onto existing MVEM parameters — no new physics

| FaultConfig field | MVEM param | Update | Clamp |
|---|---|---|---|
| `injector` (`cyl`) | `params['cd_inj'][cyl]` | `1 − rate·elapsed` | `≥ 0.4` |
| `turbo` | `params['eta_c_scale']` | `1 − rate·elapsed` | `≥ 0.55` |
| `cooling` | `params['hA_scale']` | `1 − rate·elapsed` | `≥ 0.5` |
| `bearing` | `params['f_fric_scale']` | `1 + rate·elapsed` | `≤ 2.2` |
| `ringWear` | `params['eta_v_scale']` | `1 − rate·elapsed` | `≥ 0.6` |

`ringWear` (piston ring wear / blow-by) is new but trivial — it's the same
`eta_v_scale` hook `mvem.py` already reads. Per `residual-spec.md`'s
incidence table this should also weakly excite ρ₁ (speed-density over-reads
when η_v drops while the compressor path doesn't) and ρ₁₀ (blow-by pressurises
the crankcase, gently degrading oil pressure) — both fall out for free from
the physics once η_v is actually degraded, you do not need to hand-code them.

### `oilLeak` — new, small

Oil pump wear/leak is a *different* physical fault from bearing wear even
though both touch ρ₁₀ — bearing wear narrows the clearance (pressure rises
against a fixed pump, this repo's existing model already has this backwards:
check `mvem.py`'s comment, wear should *lower* pressure via widened clearance,
which is what's implemented — leave it), while a leak or worn pump directly
cuts delivered flow regardless of clearance. Add a second scale factor:

```python
# in MVEM.__init__, alongside the other params read each step():
oil_pump_scale = params.get('oil_pump_scale', 1.0)
# in the oil block:
Q_pump = self._oil_eta_vol * self._oil_D_pump * (N_rpm / 60.0) * oil_pump_scale
```

`oilLeak` fault → `params['oil_pump_scale'] = 1 − rate·elapsed`, clamp `≥ 0.2`.

### `fuelFilter` — new, small

Fuel filter clog / vapour lock reduces deliverable fuel rail flow, and per
the incidence table gets *worse with altitude* (vapour lock is a
altitude/temperature phenomenon). Add one scale factor multiplying the fuel
command **after** it's computed from the mixture schedule:

```python
fuel_rail_scale = params.get('fuel_rail_scale', 1.0)
# altitude makes it worse — scale the fault's OWN severity, not a new state:
alt_factor = 1.0 + max(0.0, (atm_alt_ft - 8000.0) / 20000.0)  # grows above 8kft
self.fuel_delivered = cd_inj * self.fuel_cmd * fuel_rail_scale
```

You'll need to pass `atm_alt_ft` into `step()` or derive it from `atm` — check
what's available; if only pressure/temperature are passed, use a rough
pressure-based proxy instead of literal altitude (`p_atm/101325` as a stand-in
works fine, document it as a proxy). `fuelFilter` fault → `params['fuel_rail_scale']
= 1 − rate·elapsed·alt_factor`, clamp `≥ 0.3`.

### `misfire` — needs one real mechanism, not a scale factor

Ignition misfire (for this compression-ignition engine: a missed injection
event) is fundamentally different from fouling — it's a **discrete skipped
combustion event**, not a continuous derate. Per the incidence table this is
what distinguishes it from injector fouling on the same cylinder (they share
a steady-state signature and only separate on ρ₄ magnitude and the harmonic
content of ρ₁₁ — misfire removes a *complete* combustion event, fouling
attenuates it).

Implementation: `rate` is treated as a **per-cycle skip probability** (0–1),
ramped the same way. Each substep, for the affected cylinder, draw against
this probability; on a skip, zero that cylinder's `fuel_delivered[cyl]` and
`T_ind_i[cyl]` for that substep only (the injection didn't happen — no fuel,
no indicated torque, but the cylinder still pumps air, so don't zero its
contribution to `m_ex_i`, just its energy release):

```python
if rng.random() < skip_prob:
    fuel_delivered[cyl] = 0.0
    T_ind_i[cyl] = 0.0
```

This needs a per-plant `random.Random` instance (seeded, like `measurement.py`
already does) so it's deterministic per connection. This is what gives you
the correct ρ₁₁ harmonic signature (a real missing event, not a synthesized
ripple) — do not fabricate the ripple number directly, let it come out of
the actual torque accumulation the way `ripple` is already computed.

### `detonation` — needs one real mechanism, not a scale factor

Detonation/pre-ignition is a combustion abnormality, not a parameter derate.
Tonight's SPEC-1 fix added `ρ₁₁ = +2` to detonation's incidence row
specifically because a real knock event should show on crank ripple — model
it as an intermittent, severity-scaled torque disturbance plus a CHT spike on
the affected cylinder, not a steady multiplier:

```python
# severity ramps 0->rate (capped at 1.0) same as elapsed_min pattern
knock_severity = min(rate * elapsed_min(t, spec), 1.0)
if rng.random() < knock_severity * 0.3:   # intermittent, not continuous
    T_ind_i[cyl] *= (1.0 - knock_severity * 0.4)   # lost work this event
    Q_gas_i[cyl] *= (1.0 + knock_severity * 0.8)   # extra heat into the head
```

Also populate `fast.knock_intensity[cyl]` (already in the schema, currently
hardcoded to `0.02` for all cylinders in `main.py`) with `knock_severity` when
this fault is active — that field exists specifically for this.

### Sensor faults — measurement layer only, never touch plant state

`chtSensor`, `egtSensor` (existing), `mapSensor`, `lambdaSensor` (new) all go
through `sensor_biases`, exactly like the existing two — **never** through
`plantA_params`. This is the asymmetry the whole sensor-vs-instrumentation
demo beat depends on; `measurement.py`'s `sensor_biases` dict already has
`map_hPa` and `lambda` keys wired, just unused by any fault today.

```python
sensor_biases['map_hPa'] = rate * elapsed_min(t, spec)
sensor_biases['lambda']  = rate * elapsed_min(t, spec)
```

### `warmAirMass` — common-mode, one line

Add a fixed OAT offset to the **shared** `isa()` call feeding both engines —
you already got the sharing right (`# Same atm for A and B`), this is one
argument, not a new mechanism: `isa(altitude_ft, isa_offset_K=3.0 if
config.get('warmAirMass') else 0.0)`.

---

## 3. Damage integrator — ground-truth RUL, for real this time

**New file:** `backend/twin/damage.py`. Per `work-breakdown.md`, already
specified — implement it as written:

```python
# D in [0,1], one per degrading component (injector per-cyl, turbo, cooling,
# bearing/oil, ring). Accumulate through physically motivated laws, never
# injected noise — you own D, so every sample gets an exact RUL label with
# no censoring, the same construction NASA used for C-MAPSS.

# Adhesive/abrasive wear (Archard) — for friction/clearance-driven faults
# (bearing, ring wear): dD/dt proportional to normal force x sliding
# velocity / hardness. In this model's terms, proportional to T_fric * w.
dD_dt_archard = k_archard * T_fric * w / H_material

# Thermally activated ageing (Arrhenius) — for heat-driven faults
# (cooling fouling, detonation, injector coking): dD/dt proportional to
# exp(-E_a / (R * T_cht)). Higher CHT accelerates wear exponentially.
dD_dt_arrhenius = k_arrhenius * np.exp(-E_a / (R_GAS * T_cht_per_cyl))

# Cumulative damage (Miner's rule) for the reported state:
# D = sum(n_i / N_i) across duty cycles; failure at D=1.
D += (dD_dt_archard + dD_dt_arrhenius) * dt

# Ground-truth RUL, exact, no model needed — this is the label you hand BE-2:
RUL_h = (1.0 - D) / max(dD_dt, 1e-9) / 3600.0
```

Pick `k_archard`, `k_arrhenius`, `E_a`, `H_material` so a fault ramped at a
"typical" `rate` from the demo script reaches `D=1` (failure) in a
dramatically-compressed but *plausible-sounding* timeframe for a demo — tens
of minutes to a few hours of simulated time, not seconds and not weeks. State
the calibration basis in a comment (defensible, not exact — there is no
public wear-rate data for this engine, say so).

**Wire `damage_rate_per_hr` and `damage_state` into the ML pipeline call** —
`ml/inference.py`'s `run()` already accepts both and currently gets `0.0` for
both from `main.py`, which is why `physics_h` reports `null` right now (see
`docs/qa/known-issues.md` — that's the correct behaviour for "no data", not a
bug to route around). Once you have real `D` and `dD/dt`, pass them through
and the physics RUL head starts reporting real numbers instead of null.

---

## 4. Dataset generation — hand BE-2 something real

**Extend, don't replace:** `parity/sigma_generator.py`'s sampling loop already
does 90% of this (healthy plant/twin across 9 operating points). Two outputs:

- `data/healthy_flights/*.parquet` — full frames (`slow`/`fast`/`health.rho`),
  zero faults, from the *now-fixed* physics. Regenerate this even if it
  existed before tonight — anything generated before the air-path fix trained
  on the same collapsed physics BE-2's models were flagged as needing to
  avoid.
- `data/fault_runs/*.parquet` — one labelled run per fault in §2, ramped
  through a realistic severity range, **with the exact ground-truth
  `D`/`RUL_h` from §3 attached to every row.** This is what makes M3's RUL
  head trainable on real labels instead of the placeholder proxy it's using
  now.

---

## 5. CAN transport — do this last, only if time remains

**New file:** `backend/transport/can_bridge.py`. Originally Day 5 scope;
include it tonight only after §1–§4 are solid, since it's the lowest-value
item relative to time cost tonight (the socket already demonstrates live
telemetry; CAN is corroborating evidence, not the core claim).

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
pip install python-can
```

Publish the same telemetry as CAN frames on `vcan0`, decode them on the
receive side, confirm `candump vcan0` shows live traffic. Name-drop
**CANaerospace** and **DroneCAN/Cyphal** in whatever doc references this — see
`docs/team/work-breakdown.md`'s CAN section for the one-sentence version of
each, already written.

---

## Acceptance gates for tonight's work

Run these before calling it done. All should hold:

1. `python3 backend/gates_check.py` → still 6/6. If your fault-injection
   changes touched `mvem.py`'s core equations rather than just adding
   parameter hooks, this is how you find out you broke something.
2. Start `main.py`, connect a WS client, send `{"type":"fault_config",
   "config":{"injector":{"startT":5,"cyl":1,"rate":0.045}}}` — confirm
   `egt_C[1]` and `health.rho.rho6_9_cyl_dev[1]` move within the next ~30s,
   and nothing else does.
3. Send a `chtSensor` fault — confirm the *reading* moves but
   `plant.get_outputs()['cht_C']` (if you can introspect it) stays
   bit-identical. This is `verify.py`'s existing asymmetry test, extended to
   go through the live socket instead of just the offline pipeline.
4. Two WS connections open at once, fault one — confirm the other is
   unaffected (per-connection state, not module-global).
5. Generate one healthy and one fault-run parquet file, load them back,
   confirm `D` is monotonically non-decreasing and `RUL_h` is monotonically
   non-increasing within a single run.
6. `python3 -c "import main"` — no import errors, no missing config keys.

---

## What you must not do

- **Do not** touch the state equations in `mvem.py` — turbo energy state,
  Ellipse compressor, wastegate, shaft-speed ceiling, fuel schedule, oil
  model, propeller law. All six gates were calibrated against those tonight.
  Add parameter hooks; do not change the physics they feed into.
- **Do not** invent fault magnitudes to "look good" on a demo — ramp from the
  `rate` the frontend sends, always. The console's whole point is that a judge
  picks the severity and the system responds honestly.
- **Do not** put fault state at module scope. Two browser tabs must not
  fault each other's engine.
- **Do not** fabricate `D`/RUL — if a fault has no damage-rate law wired yet,
  report `physics_h: null` (already correct, already implemented) rather than
  a placeholder number.
- **Do not** skip `gates_check.py` at the end because §1–§4 "obviously
  wouldn't touch the physics." Run it. Thirty seconds now versus a broken
  demo Thursday.

---

## When you're done

Push to `day0-foundation`. State plainly in the PR/commit message which of
§1–§5 you finished, which you didn't, and — for anything you *did* finish —
whether `gates_check.py` was still 6/6 afterward. We will pull, run it, and
review before it's treated as final. That's not a lack of trust in the work;
it's the same thing that happened to the physics you're building on top of
tonight, and it's why the physics works now instead of quietly not.
