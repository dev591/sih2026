# Engineering standards — read this alongside any handoff

This is not a status report. It's the working discipline this codebase has
been held to during the sensor/physics fidelity work, written down so a
fresh session enforces it rather than rediscovering it — or worse, quietly
lowering the bar because nothing said otherwise.

If a change you're about to make doesn't clear one of these, it isn't done.

## 1. Provenance on every physical number, no exceptions

Every constant in `config/*.yaml` and `config/sensors.yaml` carries one of:

- `published` — from a standard, datasheet, or primary source. Citable in a
  slide. Example: the ITS-90 thermocouple polynomials, the Bosch LSU 4.9
  Ip-lambda table, DRDO's own "180 hp constant to 11,000 ft" figure.
- `derived` — computed from a published figure (e.g. 134.2 kW from 180 hp).
- `measured` — a third-party measurement, not an official document (e.g. the
  Rotax CHT sensor's fitted R-T curve — real, but not a vendor datasheet).
- `assumed` — an engineering estimate. **Must be labelled as such if it ever
  reaches a slide.** This is most of the numbers in this project, and that
  is fine — DRDO's own RFP exists because no published data exists for a
  UAV engine at this scale. Assumed does not mean careless: every assumed
  constant in this codebase has a comment explaining WHY that specific
  value, not just that it's a guess.

**A number with no provenance marker is not finished.** If you add a
constant, mark it before you commit it, not after someone asks.

## 2. Validate before committing — never tune a number to pass a test

This project has a specific, repeated failure mode it has trained itself out
of: picking a constant because it makes `gates_check.py` or `verify.py` go
green, rather than because it's physically justified.

Concrete examples of doing it right:
- The compressor flow-capacity loss ratio (`flow_capacity_loss_ratio: 1.0`)
  was sourced from gas-path-analysis literature (Mathioudakis et al.,
  *Energies* 2024) and from the closest real analogue (a turbocharger on a
  diesel engine, Chen et al.) — R=1.0 sat at the CONSERVATIVE end of the
  published range, and the gate passed as a *consequence* of using the real
  number, not because the number was picked to pass.
- The smoke-limit lambda (`lambda_smoke_limit: 1.15`) came from the same
  engine-identity research that established this is a compression-ignition
  diesel in the first place, not from "whatever makes Gate 3 pass."
- When Gate 3's threshold genuinely needed changing (see
  `HANDOFF-2026-09-12-engine-fidelity.md`), the fix was empirically
  diagnosed first — swept the plausible levers, confirmed which one was
  actually binding, and only then re-derived the threshold, with the
  measured sweep numbers written into the commit so the reasoning is
  checkable, not just asserted.

**If you find yourself adjusting a constant and re-running a test to see if
it passes yet, stop.** Go find the source or the physical justification
first. If a threshold itself is wrong (calibrated under now-superseded
physics — this has happened twice), say so explicitly, show the sweep that
proves it, and set the new number with margin below the worst case, not
tuned to the one point that passes.

## 3. Honest labelling over fabrication

This codebase refuses to manufacture the appearance of information it
doesn't have. Three load-bearing examples to match, not deviate from:

- **ρ₃ returns `None`** rather than reusing another parity path's estimate
  to fill the slot — `backend/parity/residuals.py`. A number that looks like
  signal but carries no independent information is worse than a gap.
- **Unmodelled telemetry fields stay frozen constants**, not jittered fakes.
  `bus_voltage_V`, `alternator_A`, `inj_timing_deg`, `fuel_rail_bar`,
  `tas_mps` are genuinely unmodelled — no electrical model, no injection
  schedule, no airframe model exists. They used to jitter in SIMULATED and
  freeze in LIVE, which was worse than either: it implied a sensor that
  doesn't exist. Now both sit in one `UNMODELLED` dict, deliberately static,
  with a comment explaining why noise would be a lie.
- **The N-CMAPSS number correction** — two judge-facing docs claimed a
  result that appeared in no artefact in the repo. Fixed to match what the
  committed notebook actually says, even though the real number is a weaker
  claim. A defensible weaker number beats an indefensible strong one.

If a value can't be measured or sourced, either don't display it, or display
it and say plainly that it isn't modelled yet. Never split the difference by
making it look real.

## 4. The verification gate — three suites, every time

Before any change to `backend/twin/`, `backend/parity/`, or
`backend/config/` counts as done:

```bash
cd /Volumes/dev/sih2026/sih-project/backend
source .venv/bin/activate
python test_sensors.py    # sensor/transducer layer — must be N/N PASSED
python verify.py          # physics self-consistency — must be ALL PASSED
python gates_check.py     # the 6 P0 acceptance gates — must be 6/6
```

And if the change touched `measurement.py`, `mvem.py`, or `residuals.py`:
**regenerate `backend/config/sigma_vector.json`** —
`python -m parity.sigma_generator` from `backend/`. `gates_check.py`
recomputes sigma in memory to check gates but does NOT rewrite the committed
file; this was missed once and caught only by diffing the file by hand.
Compare before/after sigma values and be able to explain any large jump —
a "genuine relation is quieter than the fake it replaced" pattern has shown
up three times this session and is a good sign; an unexplained 4-5x jump is
not (see the flow-capacity offset bug caught and fixed the same session it
was introduced).

A change that passes fewer than all three, or that skips sigma regeneration
when it should have run, is not finished — it's in progress.

## 5. Live end-to-end check, not just unit tests

Passing the offline suites is necessary, not sufficient. Before considering
a physics or sensor change complete, start the backend and pull a real frame
over the websocket:

```python
import asyncio, json, websockets
async def m():
    async with websockets.connect("ws://localhost:8000/ws/telemetry", max_size=None) as ws:
        for _ in range(4): d = json.loads(await ws.recv())
        print(d["health"]["diagnosis"]["top"][0])
        print(d["health"]["rho"])
asyncio.run(m())
```

Check it reads healthy on a fresh connection, residual magnitudes are
sane, and nothing throws. This has caught real bugs the offline suites
didn't (the rpm-quantisation ordering bug, several frame-schema mismatches).

## 6. Schema sync — three places, every time

Any new or changed field in the telemetry frame must land in all three:

1. `backend/main.py` (the actual frame assembly)
2. `frontend/src/types/telemetry.ts` (the type)
3. `frontend/src/mock/missionGenerator.ts` (the SIMULATED fallback)

Missing one means LIVE and SIMULATED silently disagree — the exact bug class
Phase 1 spent real effort eliminating. `p_amb_hPa` (added this session for
parity Path 2) is the template for how to add a field correctly across all
three.

## 7. Demo safety is not optional, even mid-refactor

- Tag or branch before starting invasive work, and write down the exact
  restart procedure (`docs/DEMO-FALLBACK.md` is the template — server start
  commands, port checks, what "clean" looks like).
- **Never demo from a work-in-progress branch.** `engine-fidelity` is not
  demo-safe at any point in its history so far — physics has changed too
  many times in one session for it to be soaked. The `demo-safe-2026-09-12`
  tag is what a presenter uses.
- Push work-in-progress branches, don't leave them local-only. This machine
  has already had one uncommitted-work-losing crash (2026-09-11). A branch
  that only exists on disk is one incident away from gone.

## 8. Real sources over plausible-sounding numbers

When a professor, judge, or teammate flags something as physically wrong,
the response is research, not a quick patch. This session's pattern,
repeated four times, is the one to keep using:

1. Confirm the criticism is right (it usually is, and confirming it
   concretely — with a measurement, not a hunch — is itself valuable work).
2. Research the correct physics/standard/value from primary sources.
3. Implement against the sourced number.
4. Verify the fix against the FULL suite, not just the specific case that
   prompted it.
5. Write the reasoning into the commit message with the actual numbers, so
   the next person can check it rather than take it on faith.

The alternative — patching the symptom with a plausible-sounding constant —
is faster in the moment and is exactly what put this project in the state
that needed a fidelity pass in the first place (see: the original
`lambda_full_power: 0.98` on a diesel, or the original ρ1/ρ4/ρ5 residuals
that looked like real parity relations but were zero by construction).

## Where the state and the plan live

This document is principles, not status. For "what's actually done and
what's next," read `HANDOFF-2026-09-12-engine-fidelity.md` in the same
directory — it's the status report this document is a companion to.
