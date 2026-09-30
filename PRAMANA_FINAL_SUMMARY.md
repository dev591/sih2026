# PRAMANA — Final Summary for Pitch Deck
### SIH26054 (DRDO) · VRDE 180 hp Aero-Diesel Digital Twin · branch `demo-final`
*Every number below was measured on the recording PC. Nothing is guessed.*

---

## 0. Changelog — new since the last version of this note

Use this section to find exactly what needs to change in the pptx. Everything below is also folded
into the numbered sections further down, so the rest of the document is internally consistent — this
is just the fast-scan version.

| What changed | Slide it affects | Detail |
|---|---|---|
| **Mission replay is built** (PS-E) | Scale / PS-coverage slide | Every completed run auto-saves to a flat file; a "History" button replays it — CHT/EGT charts, scrub bar, diagnosis at any instant. Fully isolated from the live session (verified: live engine kept running untouched while a replay was open). PS-E can move from "planned" to "done." |
| **Vibration RMS now modelled** (PS-B) | PS-coverage slide, honesty slide | Derived from signals the physics model already computes (crank speed-ripple + knock intensity). Labelled `derived`. Verified live: rises from 0.134g to 0.150g under an injected detonation fault. |
| **Injection timing now modelled** (PS-B) | PS-coverage slide | A representative common-rail FADEC advance schedule, scaled to the engine's own rated speed. Labelled `assumed`. Verified: 17.3° at 72% throttle near rated RPM. |
| **Battery/alternator now modelled** (PS-B) | PS-coverage slide | 28V DC regulated bus — the real MIL-STD-704-class aircraft/UAV electrical standard, not an arbitrary number — sagging toward battery-only below idle-equivalent speed. Labelled `assumed`. Verified: 28.0V / 11.91A at rated cruise RPM. **All three sensor gaps from the original review are now closed** — nothing in PS-B is served as `null` except fuel rail pressure. |
| **CAN-frame encoding built** (PS optional) | PS-coverage slide | Telemetry packed into CANaerospace-style frames, a live `GET /can/latest` endpoint, encode/decode round-trip self-tested. Honestly scoped: this is the frame *format*, not a running bus — `SocketCAN`/`vcan0` is Linux-only and the dev machine is Windows, so a real bus needs a Linux host or a USB-CAN adapter as a next step. Say this distinction if asked; don't claim a live bus. |
| **The assistant now watches a test as it develops** | Differentiator/"wow" slide | Previously: one command, one reply, then silence. Now: after a command, it polls the live frame every 7s for up to 2.5 minutes and speaks up *only* when something real changes (alarm fires, diagnosis appears, ambiguity/novelty flips) — silent on every poll where nothing moved. This is the single best live-demo moment: inject a fault, stop talking, and watch it narrate the detection as it happens on screen. |
| **A caught-and-fixed AI hallucination** (worth telling judges) | Honesty/rigor slide | While building the watch-and-narrate feature, the assistant was caught inventing numbers — it stated two different, contradictory "before" values for the same rising trend across two calls, because nothing forced it to quote a real one. Fixed by tracking the exact value every poll and constraining the model to only quote a number given verbatim. Re-verified: three consecutive narrations now chain exactly. **This is a genuinely strong story for a judge**: not "we assume our AI is honest," but "we caught it fabricating once, in testing, and closed the gap." |
| Root `README.md` rewritten | — | Now an accurate, current description of what's built (PS-coverage table, measured numbers, run instructions) instead of a stub. Doesn't affect the deck directly, but keep the two consistent. |

---

## 1. One-line pitch

**A digital twin of a real DRDO aero-engine that catches faults before any conventional threshold alarm would — explained and commanded by a fully local voice assistant, on an engine-agnostic platform.**

---

## 2. What it is (for the "solution" slide)

- A **physics-based mean-value engine model (MVEM)** of the VRDE 180 hp turbocharged aero-diesel, calibrated against DRDO's own published altitude-trial numbers — not a generic engine.
- An **ML layer** (autoencoder anomaly gate + fault classifier) trained on 4,000 simulated runs from that same physics model, so training data matches what the live system actually sees.
- A **local voice/chat assistant** (Ollama + Whisper + Kokoro, zero cloud) that explains the twin's state, answers questions about the code, runs live test scenarios on command, and then **keeps watching and narrates as the test develops** — all read-only except for the twin itself.
- An **engine-agnostic front door**: pick an engine from a card gallery, or generate a new one from a spec sheet and (optionally) train its own model — "a new engine is a config change, not a rewrite."
- **Mission replay**: every completed run is recorded and replayable — scrub back through a past fault, independent of the live session.

---

## 3. Physics — verified, not asserted

| Check | Result |
|---|---|
| Gate check (`gates_check.py`) | **6 / 6 PASS** |
| Full verification suite (`verify.py`) | **ALL PASSED** |
| Full-throttle power, sea level | 137.6 kW (measured on this PC) |
| Full-throttle power, 11,000 ft | 129.2 kW (−3.7% vs 180 hp/134.2 kW DRDO spec) |
| BSFC | 204 g/kWh |
| Critical altitude behaviour | MAP holds flat to ~6,000 ft, then declines — honest nuance vs the "flat to 11,000 ft" headline |

**Say plainly if asked:** gates 3 and 6 use loose pass criteria (documented in code comments); geometry (bore/stroke/displacement) is an engineering estimate calibrated to hit 180 hp, not a published DRDO figure — labelled `assumed` everywhere it appears, including in the generated engine YAMLs.

---

## 4. ML — recall, accuracy, latency (the numbers slide)

Trained on **4,000 simulated runs** (80 installations × 50 runs × 300 s each, generated from the live physics model), split **by installation** so test engines are never seen in training.

| Metric | Value | Basis |
|---|---|---|
| **Recall** (faulty test runs that raised an alarm) | **94.5%** (206 of 218 runs) | held-out test installations |
| **Accuracy** (top-1 diagnosis after alarm) | **98.6%** | 33,407 ticks |
| Top-2 accuracy | 99.9% | same |
| Cylinder localisation | 99.5% | same |
| Severity estimation error (mean abs.) | 0.033 | same |
| **False alarms — offline test** | 0.73 episodes/hour | 82 healthy runs, 34,630 ticks |
| **False alarms — live soak test** | **0.00 per hour** | 6 × 15 min live sessions = 1.5 engine-hours, run through the actual WebSocket |
| ML inference latency | median 8.0 ms, p95 9.0 ms | measured per tick on this PC |
| **Live end-to-end fault test** | **15 / 15 fault types detected** | every catalogued fault injected through the real WebSocket and confirmed |

**Weakest faults (say this honestly, it builds credibility):**
- Lambda-sensor drift: detected in 58% of test runs (slow-developing signal)
- Detonation: 87% detected, ~125 s median latency
- Bearing wear ↔ oil-pump wear: physically near-indistinguishable from these sensors — the system flags this ambiguity explicitly rather than guessing

**No database.** State lives in the live WebSocket session and model weight files; no persistence layer, by design (this is a real-time diagnostic feed, not a logging system).

---

## 5. The local AI assistant (the differentiator slide)

Fully offline: **Ollama (gemma4:26b)** for reasoning, **Whisper** for speech-to-text, **Kokoro** for speech-to-audio. No cloud API calls anywhere in this path — a hard requirement for DRDO telemetry.

**What it does, in-app, after you open an engine:**
1. **Explains** the live twin's state in plain language when asked — grounded only in real telemetry, cites actual sensor values and residuals, never invents numbers.
2. **Analyses, doesn't just alarm-read** — it reasons over residuals, health-parameter drift, and trends even *before* the ML anomaly gate fires, so it can flag "early sign, not yet confirmed" rather than parroting a quiet alarm as "all healthy."
3. **Answers code/architecture questions** — reads the codebase itself (`tools/codemap.py`) and cites the actual file, e.g. "false alarms are suppressed by a persistence rule in `ml/m2_autoencoder/model.py`."
4. **Runs live test scenarios on command** — "test my engine at 12,000 feet with injector fouling on cylinder 3" or natural phrasing like "what happens if the turbo fails at 8,000 feet" — it parses the request, validates it against the real fault catalog, and executes it through the *same code path* the manual Fault Console uses, live on the twin. Verified: altitude reached exactly 12,000 ft, injected fault correctly localised to cylinder 3, ML alarm fired and correctly diagnosed within the model's own measured latency.
5. **Never invents, never decides** — "should I land?" is always answered by the deterministic mission-reliability layer in code, not the language model. The assistant explains and (on explicit command only) tests; it never advises.
6. **Keeps watching after a command, and narrates only real changes** — once a test is running, it polls the live frame every 7 seconds for up to 2.5 minutes. Silent on almost every poll (a cheap comparison, no model call) — it speaks up only when the alarm actually fires, the diagnosis appears or changes, or an ambiguity/novelty flag flips. This is the moment to build the live demo around: inject a fault, go quiet, and let the assistant call out the detection as it happens.

**Interaction:** hold **F9** anywhere in the app to talk (even with the chat panel closed) — release to send. **F8** stops speech playback instantly. Typed chat works identically.

**Safety boundary:** the assistant can only affect the *simulated twin* of the engine open in your own browser tab. It cannot touch another tab's session, another engine, the trained model files, or anything outside the sandboxed simulation. Every action it takes is undoable with "reset to healthy."

---

## 5b. PS coverage, updated (for the requirements-mapping slide)

| PS asks for | Status | Detail |
|---|---|---|
| E. Mission replay | ✅ Done | Was "planned" — now built and verified (§0 above) |
| B. Vibration, battery/alternator, injection timing | ✅ Done | Was `null` — now all modelled with stated provenance (§0 above); only fuel rail pressure is still `null` |
| D. Maintenance advisory / mission-wise health report | ✅ Already built | The post-flight report (`FlightReport.tsx`) already gives RUL, diagnosis and a Continue/Derate/RTB verdict — say "the verdict comes from the deterministic layer, the AI explains it," not "we don't do advisory" |
| CAN bus / ECU interface (PS marks optional) | 🧪 Frame format built | Not a live bus — see §0 |

---

## 6. Engine-agnostic platform (the scalability slide)

- **Engine picker**: card-based gallery, one card per configured engine, showing live-twin readiness and ML-training status at a glance.
- **Add a new engine**, two levels:
  - **Low level** (~1 minute): generate a profile YAML from a short spec form, with every field marked `user-specified`, `derived`, or `assumed`. Auto-validated and physics-smoke-tested before anything is saved.
  - **High level** (~15–70 min depending on dataset size): full pipeline — noise-level calibration → synthetic dataset generation → training → prognostics → commissioning — run in an isolated workspace so the live demo engine is never touched.
- Currently **fully proven for 4-cylinder engines** (matches VRDE and the ML feature layout exactly). Backend/ML code has been substantially generalised toward arbitrary cylinder counts (residual layout, health-parameter estimator, and dataset/training pipeline now derive their shape from the engine profile rather than hardcoding 4) — **this generalisation is in progress, not fully complete or tested end-to-end for non-4-cylinder engines.** State this honestly if asked; do not claim it works for any cylinder count yet.

---

## 7. Known limitations — state these proactively, it reads as rigor not weakness

1. Physics geometry (bore/stroke/displacement) is a calibrated estimate, not a published DRDO figure.
2. Bearing-wear vs oil-pump-wear cannot be separated with the current sensor set — flagged as an explicit ambiguity, never guessed.
3. Lambda-sensor drift detection rate (58%) is the weakest fault class; slow-developing signal.
4. Non-4-cylinder engine support is architecturally underway but not complete or validated.
5. No persistent database — by design, not an oversight, given the real-time diagnostic use case.
6. Rotax 914 profile is present but incomplete (missing published spec fields) — runs in simulation mode only, not live twin, until real numbers are sourced.

---

## 8. Suggested pitch-deck flow

1. **Problem** — conventional threshold monitoring catches faults only after they're already dangerous.
2. **Solution** — physics twin + ML catches faults early, with quantified accuracy (§4).
3. **Proof** — gates 6/6, verify all-pass, live 15/15 fault detection, 0% live false alarms.
4. **Differentiator** — the local AI assistant: explain, analyse, command, **watch and narrate live** (§5) — inject a fault on stage, stop talking, let it call out the detection itself. This is the "wow" moment.
5. **Scale** — engine-agnostic architecture, add-an-engine flow (§6), honestly scoped (§7).
6. **Rigor** — the caught-and-fixed hallucination story (§0) if there's room; it's a stronger trust signal than claiming perfection.
7. **Close** — DRDO's own engine, DRDO's own published numbers, zero cloud dependency.
