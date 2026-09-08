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

## The script

### 0:00 — Healthy cruise, 18 000 ft

Twin overlay sits exactly on the measurement. Residuals flat. The 3D engine
turning at scaled RPM, all four cylinders steel-grey.

> *"Every parameter is green. A threshold system sees exactly what you see.
> Watch."*

Leave the limits panel visible for the whole demo. **The contrast is the
argument.**

### 0:40 — Inject injector fouling on cylinder 2

Slow ramp, `C_d ↓ 0.4 %/min`. **Every limit stays green.** The residual on EGT₂
lifts within seconds; the anomaly score crosses at roughly t+22 s of simulated
time. Cylinder 2 goes amber in the 3D view.

> *"No limit has been exceeded. Nothing is red. But the twin already knows,
> because cylinder 2's exhaust temperature has no business being twelve degrees
> above its three siblings at this fuel flow and this altitude."*

This beat is the whole product in one sentence. Do not rush it.

### 1:20 — Isolation and explanation

> *"Injector fouling, cylinder 2. Ninety-one per cent."*

Open the **explain drawer**: EGT₂ up, fuel flow trending down, 0.5-order ripple
present, oil untouched — matched against the signature row. Show the network
and the physics **agreeing**.

> *"The network gives us the accuracy. The incidence matrix gives us the why.
> Two independent mechanisms, same answer — and their agreement is itself
> evidence."*

Judges remember the *why*.

### 2:00 — Remaining useful life

3.2 h, p10–p90 band 2.4–4.1 h. Show **both heads**.

> *"Physics says 4.1 hours, the network says 3.7. We advise on 3.7 — the
> conservative one."*

**Volunteer the uncertainty before anyone asks for it.** A bare number reads as
overconfident to anyone who has worked with real hardware.

### 2:25 — The decision

Mission panel recomputes live: **continue 58% · derate 94% · RTB 99%.** Route
recolours by predicted risk, point-of-no-return marker slides along the track.

> *"Derating costs forty minutes on station. That is the trade, and it is the
> commander's call — not ours."*

**Hand the mouse to a judge.** Let them drag the cruise altitude down and watch
the probability move. A judge changing the mission themselves is worth more
than any model architecture.

### 3:00 — The twist: sensor drift on CHT₃

Inject a slow bias ramp on the **sensor**, not the engine. The system reports a
**sensor fault**, and shows why: no corroborating residual anywhere. No ripple.
No fuel-flow change. No energy-balance shift.

> *"A threshold system just aborted an eighteen-hour mission for a forty-dollar
> thermocouple."*

**This is the line they will repeat to the next team.** The problem statement
lists sensor drift as a fault class explicitly, and nobody else in the room
will separate it from an engine fault. Build for this scene.

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
| Everything | The recorded video. Have it open in another tab already. |

**Never debug on stage.** Move to the fallback, finish the story, and mention
the issue in Q&A only if asked.
