# BE-1 — Day 2 Plan

**Written from the frontend side, after actually running your Day-1 backend
against the live dashboard.** Read `BE-1-START-HERE.md` and
`docs/spec/residual-spec.md` first if you haven't — this document assumes
both and does not repeat them.

---

## What Day 1 actually shipped (verified, not assumed)

Pulled your `404c87b` commit, ran it against the frontend. Two bugs were
found and already fixed for you in a follow-up commit — you don't need to
redo this part, just `git pull`:

1. **`Q_LHV_J_per_kg: 43.0e6` in both engine YAMLs parsed as the string
   `"43.0e6"`, not a float.** PyYAML's safe-load float regex requires a
   signed exponent (`e+`/`e-`). This broke `MVEM.step()` on the very first
   call — even your own `verify.py` was failing. Fixed to `43.0e+6` /
   `43.5e+6`.
2. **`parity/sigma_generator.py` was a 0-byte stub.** Implemented it —
   samples the healthy plant/twin pair across 9 operating points, computes
   per-channel σ, writes `backend/config/sigma_vector.json`. `main.py` now
   loads real σ instead of silently falling back to `[1.0]*11`. Re-run it
   any time your MVEM tuning changes: `python3 -m parity.sigma_generator`.

With those fixed, `verify.py` passes end-to-end and the WebSocket serves
schema-valid frames. **The dashboard badge does go LIVE** — task 1 from your
start-here guide is genuinely done.

---

## The one thing that doesn't work yet, and it's the headline demo

I ran the frontend against your live backend and clicked "Inject fault" in
the Fault Console. **Nothing happened to the live feed.**

Root cause: your WebSocket is send-only. There is no receive loop in
`main.py`, so there is no way for the frontend to ever tell your backend
"inject an injector fault on cylinder 2 now." The Fault Console currently
only drives the frontend's own local mock generator
(`frontend/src/mock/missionGenerator.ts`) — the moment the badge goes LIVE,
your backend's fixed healthy loop takes over and the console goes inert.

This is expected — fault wiring is explicitly Day 2/3 scope in your
start-here guide — but it's the **first thing to build today**, because
without it, going LIVE for a demo is a downgrade from SIMULATED, not an
upgrade.

---

## Task 1 — Accept fault commands over the socket (do this first)

The frontend already has a fully-formed fault schema it uses against the
mock generator. Reuse it verbatim — this is a deliberate ask, not
laziness: it means the Fault Console needs **zero frontend changes**, and
mock vs. live stay behaviourally identical (the "one code path for script
and sandbox" decision already in force elsewhere in this repo).

From `frontend/src/mock/missionGenerator.ts`:

```ts
export interface FaultSpec {
  startT: number;   // seconds into the run at which this fault begins
  cyl?: number;      // 0-indexed cylinder, where the fault is per-cylinder
  rate: number;      // severity rate, units depend on the fault
}

export interface FaultConfig {
  injector?: FaultSpec;    // rate = fraction of C_d lost per minute
  turbo?: FaultSpec;       // rate = fraction of eta_c lost per minute
  cooling?: FaultSpec;     // rate = fraction of hA lost per minute
  bearing?: FaultSpec;     // rate = friction fraction gained per minute
  chtSensor?: FaultSpec;   // rate = degC per minute of bias
  egtSensor?: FaultSpec;   // rate = degC per minute of bias
  unmodelled?: FaultSpec;  // rate = sigma per second (BE-2's territory, ignore for now)
  warmAirMass?: boolean;   // common-mode OAT offset, must hit BOTH engines identically
}
```

**Add a receive loop to `main.py`.** FastAPI WebSockets support concurrent
send/receive on the same connection — run them as two tasks:

```python
current_config: dict = {}   # FaultConfig, JSON as sent by the frontend

async def receive_loop(websocket, state):
    async for msg in websocket.iter_json():
        if msg.get("type") == "fault_config":
            state["config"] = msg["config"]
        elif msg.get("type") == "reset":
            state["config"] = {}
```

Run `receive_loop` and your existing send loop concurrently with
`asyncio.gather`, sharing a small per-connection state dict (you already
create fresh `plantA`/`twinA`/`plantB` per connection — put the fault state
next to them, not at module scope, or two judges opening two tabs will
fault each other's engines).

**Frontend-side change needed too (I'll make this one):** `feed.ts` needs a
`send(config: FaultConfig)` method wired to the Fault Console's
`applyConfig`, gated on `source === 'live'`. Flag me once your receive loop
is up and I'll wire it same day — this is a two-hour job once your half
exists, not a blocker on you.

---

## Task 2 — The six faults, as parameter ramps

`backend/twin/faults.py` (new file). A `FaultSpec` becomes a scalar
multiplier ramp starting at `startT`, applied to the relevant `MVEM.step()`
param:

| Frontend field | MVEM param it drives | Ramp direction |
|---|---|---|
| `injector` (`cyl`, `rate` = %C_d/min) | `params['cd_inj'][cyl]` | decreases from 1.0 |
| `turbo` (`rate` = %η_c/min) | `params['eta_c_scale']` | decreases from 1.0 |
| `cooling` (`rate` = %hA/min) | `params['hA_scale']` | decreases from 1.0 |
| `bearing` (`rate` = %friction/min) | `params['f_fric_scale']` | increases from 1.0 |
| `chtSensor` (`cyl`, `rate` = °C/min) | `sensor_biases['cht_C'][cyl]` | ramps up (never touches plant state — measurement layer only) |
| `egtSensor` (`cyl`, `rate` = °C/min) | `sensor_biases['egt_C'][cyl]` | ramps up (measurement layer only) |

This is exactly the sensor-vs-engine asymmetry you already built correctly
into `measurement.py` — the two sensor faults must go through
`sensor_biases`, never through `plantA_params`. Your `verify.py` already
checks this asymmetry; extend it to check these six ramps specifically
once wired (assert a `cht_C[i]` change with `plant.get_outputs()['cht_C']`
held bit-identical).

**A ramp, not a step.** `rate` is per-minute, so at time `t`:
```python
severity = max(0.0, (t - spec['startT']) / 60.0) * spec['rate']
cd_inj[cyl] = max(1.0 - severity, floor)   # clamp so it can't go negative
```
This matches the "faults are parameter perturbations, never spikes" rule
and matches what the frontend's mock generator already does — don't
diverge from it, an evaluator comparing SIMULATED vs LIVE side by side
should see the same *shape* of degradation, just real physics under it.

**`warmAirMass`** is common-mode: when true, add a fixed OAT offset (the
frontend uses a few Kelvin) to the **shared** `isa()` call that feeds both
`plantA` and `plantB` — you already got this right structurally (`atm =
isa(...)  # Same atm for A and B`), so this is a one-line addition to that
call, not a new mechanism.

---

## Task 3 — Healthy dataset for BE-2

Your `parity/sigma_generator.py` (now implemented) already does 90% of
this — it's the same sampling loop, just logging full frames instead of
only ρ. Extend it (or add a sibling script) to write
`data/healthy_flights/*.parquet`: one row per `(operating_point, timestep)`
with the full `slow`/`fast`/`health.rho` fields, zero faults active. BE-2
needs this to fit the M2 LSTM autoencoder's healthy-only threshold — see
`docs/backend/ML-DEEP-DIVE.md` §4 if you want the reason, but you don't
need to read the whole thing, just produce the parquet.

---

## Definition of done, Day 2

- [ ] `main.py` has a receive loop; a `fault_config` message visibly changes
      the live feed within a few seconds (test with `curl`/`websockets`
      client before waiting on the frontend wiring)
- [ ] `backend/twin/faults.py` implements all six ramps from the table above
- [ ] `verify.py` extended with one assertion per fault: plant state moves,
      twin state does not, sensor faults never touch `plant.get_outputs()`
- [ ] `data/healthy_flights/*.parquet` generated and handed to BE-2
- [ ] Re-run `python3 -m parity.sigma_generator` once faults exist, so σ is
      characterised on genuinely fault-free data (it already is, but
      re-confirm nothing in Task 1/2 changed the healthy baseline)

---

## How I'll test this from the frontend side

1. `python3 main.py`, `npm run dev`, badge goes LIVE (already confirmed
   working).
2. Send a `fault_config` message for `injector` on cyl 1 — confirm `egt_C[1]`
   and `ripple` move in the live feed within the ramp's timescale, and
   `health.rho.rho6_9_cyl_dev` deviates for that cylinder specifically.
3. Send `chtSensor` on cyl 2 — confirm `cht_C[2]` reading moves but
   `health.diagnosis` (once BE-2's side exists) or at minimum `rho10_oil`/
   other unrelated channels stay flat, proving the sensor-vs-engine
   asymmetry survives the live wire, not just `verify.py`.
4. Two browser tabs open against the same backend, fault injected from one
   — confirm the other tab's engine is unaffected (per-connection state,
   not module-global).

Ping me the moment the receive loop exists — I don't need faults.py
finished to start wiring the frontend send-side, just the message contract
above.
