# Deployment Path — How This Actually Reaches an Aircraft

**Owner: R1.** Source material for the deployment roadmap slide (a listed DRDO
deliverable that most teams ship zero slides on) and for the "how does this get
onto an aircraft?" question in the Q&A sheet.

**Read this before presenting.** A DRDO panel are procurement people as much as
engineers. They are evaluating whether this could become a programme, not only
whether it runs on a laptop. Sounding realistic about fielding is worth more
than sounding ambitious about capability.

---

## The short answer

**It does not go onto a flying aircraft, and we should never imply it does.**

Nothing from a hackathon flies. What we are building is the software core of
something that reaches an aircraft through a staircase, and each step needs
evidence the step before it produced. Being able to name those steps in order
*is* the answer to the question.

---

## The staircase

### Step 1 — A test cell, not an aircraft

The first real validation is a **dynamometer**, not a flight. VRDE already has
this engine on test stands with 1100+ hours logged. Bolt it to a dyno,
instrument it properly, run the operating envelope, and check whether the
parity residuals actually sit near zero on a real machine rather than on our
simulator.

Then **seed faults deliberately**: partially block an injector, foul a
compressor, restrict an oil gallery. Check whether the incidence matrix
predicts what really happens.

This is where the project either survives or dies, and **it needs no aircraft
at all.** It is also where our synthetic-data answer stops being an argument
and starts being a measurement.

**Evidence produced:** detection lead time and false-alarm rate on real
hardware; whether the residuals are centred on a physical engine.

### Step 2 — Shadow mode on recorded flight data

Take flight data that already exists from TAPAS trials. Replay it through the
twin on the ground. Let it produce diagnoses **that nobody acts on.** Then
compare against what maintenance actually found when they opened the engine.

Pure software, zero flight risk, and it is how the false-alarm-per-flight-hour
number gets built — the first number a maintenance officer asks for.

**Evidence produced:** agreement between our diagnoses and confirmed
maintenance findings.

### Step 3 — On the airframe, read-only

A Jetson-class board on the aircraft, reading CAN, computing residuals,
downlinking the 1 Hz health vector. **It observes and reports. It commands
nothing.**

**Evidence produced:** latency, resource envelope, behaviour in the real
electrical and thermal environment.

### Step 4 — And only then, anything that acts

See the next section. This is years away and that is the correct answer.

---

## THE MOST IMPORTANT DISTINCTION IN THIS DOCUMENT

There are **two completely different products** inside our design, and the
difficulty gap between them is enormous:

| | What it takes to field |
|---|---|
| **A monitor that advises a human** | Hard but ordinary. If it is wrong, a person disregards it. |
| **Anything that commands the engine** | An entirely different universe of assurance. |

Our **active diagnosis** — commanding a ±3–5% fuel trim and measuring the gain
of the response — is the single most sophisticated capability in the design.
**It is also the last thing that will ever fly.** The moment software perturbs a
running engine on an aircraft it becomes a flight-critical function. In India
that means clearance through **CEMILAC** (Centre for Military Airworthiness and
Certification, itself part of DRDO), with software assurance at a level broadly
comparable to DO-178C. That is years and a different engineering budget.

The same split applies to the mission layer:

- *"Derate to 78%: 94% completion probability, costs 40 minutes on station"*
  presented **to an operator** → fieldable as an advisory system.
- The same schedule handed **directly to the flight management system** →
  flight-critical, and a completely different certification problem.

### Say this out loud in the pitch

> *"The advisory path and the commanding path are deliberately separated.
> Everything we have shown can be fielded as an advisory system with no change
> to flight-critical software. The active diagnosis is specified with its full
> safety envelope so it is ready when the airworthiness case is made — but
> nothing else in the system depends on it."*

That sentence tells a procurement panel we understand what fielding actually
costs. Almost no student team will say anything like it.

---

## Why the layer boundary is the migration path

Layers 0 and 1 are the only components that know the data is simulated. The
twin consumes **CAN frames**; a real ECU emits CAN frames. So replacing the
simulator with hardware is a **source change, not a rewrite** — which is the
entire deployment argument, and why the boundary was designed deliberately
rather than falling out of convenience.

### And why the FADEC matters more than it looks

The VRDE engine is the right target specifically because **it has an
indigenously developed FADEC.**

A FADEC already computes and publishes *commanded* quantities — commanded fuel,
commanded timing. Our injector-fouling diagnosis rests entirely on comparing
**commanded fuel against delivered fuel**. Without the commanded number that
residual does not exist.

On a carburetted engine, roughly half the fault library gets weaker. This is not
a coincidence in our engine choice; it is the reason for it, and it is worth one
sentence if anyone asks why this engine.

---

## The near-term product — the credible one

Even if nothing ever flies, **Step 2 has standalone value**, and this is the
version that could be in use within a year.

Any organisation running these engines has flight data in one place and
maintenance records in another, and nothing joins them. A system that replays
recorded flights and reports *"cylinder 2 was drifting for eleven hours before
that injector was replaced"* pays for itself in maintenance planning — with
**no airworthiness case whatsoever**, because it never touches an aircraft.

Mention this as the near-term deployment. It is the one a procurement person
can actually imagine buying.

---

## The feedback loop nobody builds

Every deployed prognostic system in service today has the same gap: a diagnosis
is issued, the aircraft lands, maintenance opens the component, and **the
finding never returns to the algorithm.**

Designing that path in from the beginning is inexpensive now and effectively
impossible to retrofit later. It is the final row of the TRL table and it is
worth a sentence of its own — it says we have thought past the demo.

---

## TRL summary for the slide

| Phase | TRL | Activity | Evidence produced |
|---|---|---|---|
| **Current** | 3→4 | Software twin, synthetic generation, internal consistency + external benchmark validation | Parity residual spread, N-CMAPSS score, isolation confusion matrix |
| **Next** | 4→5 | Recalibration against instrumented dynamometer data; seeded-fault testing | Detection lead time and false-alarm rate on real hardware |
| **Then** | 5→6 | Edge deployment on an airborne computing module; recorded-data shadow mode | Latency, resource envelope, agreement with maintenance findings |
| **Mature** | 6→7 | Fleet analytics; maintenance-record feedback closing the diagnostic loop | Continuously improving priors from confirmed outcomes |

---

## Programme alignment — state this plainly

DRDO has issued a request for proposals under the **Technology Development
Fund** for a digital twin framework for aero engine health monitoring,
integrated with Health and Usage Monitoring Systems, structured in two phases:
first a digital model of the engine built from physics-based and data-driven
approaches covering mechanical behaviour, thermal behaviour, degradation
pathways and failure modes; second, integration into the wider aircraft digital
ecosystem.

PRAMANA is scoped as a **Phase 1 contribution**, on the reciprocating rather
than the gas-turbine side of the portfolio.

> *"This is not a student exercise adjacent to an organisational interest. It is
> a prototype of a capability the organisation has formally solicited."*

### And be honest internally about what winning does

Winning SIH gets us **a conversation, not a contract.** What it realistically
opens is internships, continued engagement with the lab, and a credible
position relative to a programme that is already funded. That is a good outcome
and we should not oversell it to ourselves.

---

## Q&A — the answer to "how does this get onto an aircraft?"

> *"Not directly, and we would not claim otherwise. The next step is a
> dynamometer, not a flight — recalibrating against a physical engine and
> seeded-fault testing, which is where our synthetic-data argument becomes a
> measurement. After that, shadow mode on recorded flight data, where the system
> produces diagnoses nobody acts on and we compare them against what maintenance
> actually found. Only then does anything go on an airframe, and it goes on
> read-only.*
>
> *We have deliberately separated the advisory path from the commanding path.
> Everything in this demo can be fielded as an advisory system with no change to
> flight-critical software. The active diagnosis perturbs a running engine, so
> it is flight-critical by definition and needs a CEMILAC case — it is specified
> with its safety envelope, but nothing else depends on it.*
>
> *And the architecture is built for that migration: only the bottom two layers
> know the data is simulated. We already consume CAN frames, which is what the
> FADEC emits. Replacing the simulator with the real engine is a source change,
> not a rewrite."*
