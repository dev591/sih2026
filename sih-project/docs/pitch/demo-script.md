# PRAMANA — The Four-Minute Demo

**Written on Day 0, before any code. The build serves this script, not the
other way round.** If a feature does not appear in these four minutes, it is
not on the critical path — and R3 has the authority to say so out loud.

Rehearse until it is boring. A demo that still feels exciting to the presenter
is a demo that has not been rehearsed enough.

---

## Before you start

- [ ] Every dataset **pre-generated**. Never generate during a demo.
- [ ] Model weights **pre-trained and committed**. Never train during a demo.
- [ ] Whole scenario reproducible from a single `make demo`
- [ ] **Full video backup recorded the night before**, verified playable offline
- [ ] Laptops charged, everything mirrored locally, one teammate carrying a duplicate
- [ ] `candump vcan0` scrolling in a corner of the screen from second zero

> Assume the venue wifi will fail. At some point, it will.

---

## ⚠️ Restructured 2026-09-12 — rehearsed live, fault choice changed twice today

Two things changed after live rehearsal against today's engine-fidelity code:

1. **`injector` fouling (this script's original primary fault) is confirmed
   broken** — the real classifier misdiagnoses it as `ignition_misfire` even
   at max console severity. `bearing` and `cooling` are also confirmed
   unreliable (see the fallback table's warning at the bottom). Weights are
   trained on pre-fidelity-rewrite residual statistics and were not
   retrained — retraining under competition time pressure was explicitly
   ruled out.
2. **The primary visual beat is now CHT sensor drift, not an engine fault.**
   `turbo` degradation is correctly diagnosed (94.3%) but is a *whole-engine*
   fault — all four cylinder lines move together, so it does not give the
   "watch this one cylinder go wrong while the others stay flat" visual the
   strip charts are built for, and it takes ~130s to become confident.
   `chtSensor` drift is per-cylinder, gives exactly that visual, is
   correctly classified, and resolves in under 30 seconds — it is now Act 1.
   `turbo` is kept as an optional Act 2 for the "it's not always just a
   sensor" contrast, budgeted honestly at ~2 minutes.

---

## The script

### 0:00 — Healthy cruise, 18 000 ft

Twin overlay sits exactly on the measurement. Residuals flat. The 3D engine
turning at scaled RPM, all four cylinders steel-grey. Point at the strip
chart legends — each cylinder now has a live numeric readout next to its
color swatch, and hovering the chart shows every cylinder's exact value at
the cursor. **Do this before injecting anything** — a judge needs to know
what "normal" looks like on this chart before "wrong" means anything.

> *"Every parameter is green. A threshold system sees exactly what you see.
> Watch."*

Leave the limits panel visible for the whole demo. **The contrast is the
argument.**

### 0:40 — Act 1: CHT sensor drift on one cylinder

**Measured live 2026-09-12.** Use the Fault Console, `chtSensor`, pick a
cylinder (e.g. 3), console-default-to-max rate. Rehearsal measured: that
cylinder's CHT trace climbs cleanly away from the other three (140°C → 175°C
over ~37s while the rest hold flat at ~140°C) — this is the exact "one line
pops out" visual. The classifier locks onto `cht_sensor_drift`, correct
cylinder, at **t+14-28s** (varies by cylinder/run, always under 30s in
rehearsal).

> *"Watch cylinder 3's line. Nothing else has moved — same fuel flow, same
> boost, same everything else. Just that one temperature reading, climbing."*

### 1:00 — Isolation: it's the sensor, not the engine

Open the **explain drawer**: `is_sensor_fault: true`, no corroborating
residual anywhere else — no ripple, no fuel-flow change, no energy-balance
shift. Then show the **mission panel did not move**: continue 99% · derate
99.5% · RTB 99%, recommendation **continue** — measured live, unchanged from
healthy baseline.

> *"A threshold system on that one channel would have flagged an
> over-temperature and derated the mission. This system checked everywhere
> else first, found nothing, and correctly told you: it's a forty-dollar
> thermocouple, not the engine. Keep flying."*

**This is the line they will repeat to the next team.** The problem statement
lists sensor drift as a fault class explicitly, and nobody else in the room
will separate it from an engine fault. Build for this beat — it's fast,
reliable, and it's the whole product in one sentence.

### 1:30 — Act 2 (optional, budget ~2 min): a real engine fault, for contrast

Reset, then inject `turbo` (turbocharger degradation) at console-max
severity. **Every limit stays green**, and — unlike Act 1 — all four
cylinder lines drift together now, because this fault is in the shared air
path, not one cylinder. Measured live: the classifier locks onto
`turbo_degradation` at 94.3% confidence at **t+130s** — narrate the healthy
strip charts and the 3D engine through the wait, it's not dead air.
**Pre-arm this fault silently ~2 minutes before this segment if stage time is
tight** — the fault and its detection are both still completely real, you're
just choosing when the audience's attention starts relative to when the
clock started.

> *"No limit has been exceeded. Nothing is red. But the twin already knows —
> the compressor map and the speed-density estimate, two independent readings
> of the same air mass flow, have started to disagree."*

Open the explain drawer again: compressor-map flow estimate diverging from
speed-density while intake temperature and crank speed stay nominal.

> *"Turbocharger degradation. Ninety-four per cent. The network gives us the
> accuracy, the incidence matrix gives us the why."*

**Do not say "independent" here.** The classifier is trained on residuals
drawn around the incidence signatures, so the two are related by
construction. What IS defensible: the training samples are jittered off the
nominal columns, so the agreement rate is a *measured* number (see
`ml/weights/m3_classifier_report.json`) rather than a guarantee — quote that
number if asked.

**RUL, measured live for this scenario:** the network head reports 8.78 h
(p10-p90 band 8.2-9.58 h); the physics/damage-integrator head reports 0.49 h
— `heads_disagree: true`. Advise on the **physics head (0.49 h)**: it's the
exact ground-truth damage integration, whereas the network head was fitted
before today's rebuild and has not been retrained. Say so plainly if asked.

> *"The physics model says thirty minutes. The learned network still says
> nearly nine hours. We advise on the physics number, and we're telling you
> exactly why they disagree: the network hasn't been retrained since today's
> engine rebuild. We're not hiding that."*

**Volunteer the uncertainty before anyone asks for it.**

Mission panel, measured live at the same point: **continue 79.5% · derate
95.0% · RTB 99%**, recommending **derate to 78% power** (40 min cost on
station).

> *"Derating costs forty minutes on station. That is the trade, and it is the
> commander's call — not ours."*

**Hand the mouse to a judge.** Let them drag the cruise altitude down and watch
the probability move.

### 3:30 — Replay and report

Scrub the timeline back through the entire event — the 3D model, the charts and
the residual heatmap all follow the scrubber. Generate the post-flight PDF on
screen.

Ten seconds, and it closes out PS components **E** and **F**.

### 3:50 — Close on deployment, not on tech

One architecture slide. What runs on a Jetson-class board on the airframe, what
runs on the ground, and what changes when a real ECU replaces Layer 0.

> *"Nothing above Layer 1 changes. The ground software is already consuming
> byte-identical CAN frames."*

**Close on the roadmap.** A DRDO panel are procurement people as much as
engineers, and a deployment roadmap is a listed deliverable that most teams
ship zero slides on.

---

## If something breaks mid-demo

Each layer is independently demoable — that property is the point of the
architecture. Drop the broken layer and keep the story:

| Broken | Fall back to |
|---|---|
| 3D view | Residual heatmap + strip charts; narrate the cylinder |
| Live sim | Replay from a committed Parquet mission file |
| ML stack | Signature-matrix isolation alone — it is a complete diagnosis on its own |
| Mission layer | RUL band and the derate recommendation as a static number |
| Short on stage time | Drop Act 2 (turbo) entirely and close after Act 1 (CHT sensor drift) — it alone is a complete, fast, correctly-classified story |
| Everything | The recorded video. Have it open in another tab already. |

**Never debug on stage.** Move to the fallback, finish the story, and mention
the issue in Q&A only if asked.

**Never select `injector`, `bearing`, `cooling`, or `egtSensor` from the Fault
Console for this demo** — confirmed live 2026-09-12 to misdiagnose or never
trigger at all (see the 0:40 and 3:00 sections above). The only two
console options confirmed correct are `turbo` and `chtSensor`.
