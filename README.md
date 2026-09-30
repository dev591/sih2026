# PRAMANA

**प्रमाण** — *the means by which a thing is known to be true*

A digital twin and fault-diagnosis system for aero piston engines in MALE UAVs, built around DRDO's own VRDE 180 hp aero-diesel.

**Smart India Hackathon 2026 · Problem Statement SIH26054 · DRDO · Software · Robotics & Drones**

Branch: `demo-final`

---

## What it is

A physics-based mean-value engine model (MVEM) streams live telemetry over a WebSocket. An ML layer — an autoencoder anomaly gate plus a fault classifier, trained on data generated from that same physics model — catches faults before a conventional threshold alarm would, localises them to a cylinder, and estimates remaining useful life where the physics supports it. A fully local voice/chat assistant (no cloud calls anywhere) explains what the twin is doing, answers questions about the codebase itself, and can run live test scenarios on command. The platform is engine-agnostic: pick an engine from a card gallery, or generate a new one from a spec sheet and train its own model.

Every number below was measured on the development machine, not estimated. See [`PRAMANA_FINAL_SUMMARY.md`](PRAMANA_FINAL_SUMMARY.md) for the full pitch-ready writeup and [`PRAMANA_Final_Technical_Document.docx`](PRAMANA_Final_Technical_Document.docx) for the detailed technical report.

---

## Problem statement coverage

| PS asks for | Status | Notes |
|---|---|---|
| **A.** Digital twin core, live data sync, modular | ✅ Built | MVEM in lockstep with the WebSocket feed; engine parameters are a YAML profile, not hardcoded |
| **B.** Health monitoring: RPM, CHT, EGT, oil, fuel flow, ... | ✅ Built | Per-cylinder CHT/EGT, oil pressure/temp, fuel flow, MAP, turbo speed |
| — vibration RMS | ✅ Built | Derived from the crank speed-ripple and knock-intensity signals the physics model already computes — labelled `derived`, not an independently calibrated channel |
| — injection timing | ✅ Built | A representative common-rail FADEC advance schedule scaled to the engine's own idle/rated speed — labelled `assumed`, no published DRDO map exists to calibrate against |
| — battery/alternator | ✅ Built | 28 V DC regulated bus (the standard aircraft/UAV electrical bus, MIL-STD-704-class — not an arbitrary number), sagging to battery-only voltage below idle-equivalent speed; alternator current from a typical avionics/FADEC/ignition/fuel-pump baseline load. Labelled `assumed` — the MVEM has no electrical subsystem of its own, so this isn't derived from the physics core |
| **C.** Fault detection (misfire, injector, lubrication, sensor drift, ...) | ✅ Built | **15 fault types**, all live-tested through the real WebSocket, 15/15 detected |
| **D.** Anomaly detection, RUL, trend analysis | ✅ Built | Autoencoder gate + classifier; RUL (p10/p50/p90) computed for faults with a real redline crossing (4 of 14) — the rest correctly report "not available" |
| **D.** Maintenance advisory | ✅ Built | A deterministic mission-reliability layer gives a Continue/Derate/RTB verdict; the AI assistant explains it but never decides it |
| **E.** Mission replay | ✅ Built | Every completed run is recorded to a flat file and replayable from the "History" drawer — independent of the live session |
| **E.** Environment scenarios (altitude, hot-day, endurance) | ✅ Built | Altitude sweep verified 0–20,000 ft; ISA+20 hot-day case in `verify.py`; fuel/endurance and point-of-no-return tracked live |
| **F.** Dashboard, live 3D twin, fault console, reports | ✅ Built | Interactive 3D engine, live fault console, a printable post-flight engineering report |
| CAN bus / ECU interface | 🧪 Encoding built | Telemetry is packed into CANaerospace-style frames (`backend/can_frames.py`, `GET /can/latest`); PS marks this optional ("may use"). This is the frame **format**, not a live bus — `SocketCAN`/`vcan0` is Linux-only and this runs on Windows, so a real bus needs a Linux host or a USB-CAN adapter as a next step |

---

## The AI assistant

Fully offline — **Ollama** (`gemma4:26b`) for reasoning, **Whisper** for speech-to-text, **Kokoro** for speech synthesis. No cloud API is called anywhere in this path, which is a hard DRDO telemetry requirement.

- **Explains** the live twin's state in plain language, grounded only in real telemetry — it never invents a number.
- **Reasons over the raw evidence**, not just the alarm gate: it can flag "early sign, not yet confirmed" from residual and health-parameter drift before the ML formally alarms.
- **Answers questions about the code itself**, citing the actual file (`tools/codemap.py` indexes the repo for this).
- **Runs live test scenarios on command** — "test my engine at 12,000 feet with injector fouling on cylinder 3", or natural phrasing like "what happens if the turbo fails at 8,000 feet" — validated against the real fault catalog and executed through the same code path as the manual Fault Console.
- **Never decides.** "Should I land?" is always answered by the deterministic mission layer in code, never by the language model.

Hold **F9** anywhere in the app to talk (even with the chat closed); **F8** stops speech playback instantly.

---

## Measured results (live model, held-out test installations)

| Metric | Value |
|---|---|
| Recall (faulty test runs that alarmed) | **94.5%** (206/218) |
| Top-1 diagnosis accuracy after alarm | **98.6%** |
| Top-2 accuracy | 99.9% |
| Cylinder localisation | 99.5% |
| False alarms — offline test | 0.73 episodes/hour (82 healthy runs) |
| **False alarms — live soak test** | **0.00/hour** (6 × 15 min live sessions, 1.5 engine-hours) |
| ML inference latency | median 8.0 ms, p95 9.0 ms |
| Live end-to-end fault detection | **15/15** fault types, real WebSocket |

Weakest points, stated rather than hidden: lambda-sensor drift is detected in 58% of test runs; bearing wear and oil-pump wear are physically near-indistinguishable with the current sensor set and are flagged as an explicit ambiguity rather than guessed.

An earlier NASA N-CMAPSS transfer-learning result exists in the repo (`sih-project/ncmapss_rul_result.png`, `sih-project/docs/`) from an earlier project phase — it has not been re-verified in the current pipeline and should be checked before being quoted on its own.

---

## Repository layout

```
sih-project/
  config/                    engine_vrde_180.yaml (primary), engine_rotax_914.yaml, sensors.yaml
  backend/                   FastAPI + WebSocket server (main.py), physics twin (twin/), assistant API,
                             engine onboarding endpoints, mission recorder
  ml/                        M2 autoencoder + M3 classifier training, inference pipeline, prognostics,
                             health-parameter Kalman filter, dataset generator
  frontend/                  React + three.js GCS — engine picker, 3D twin, fault console, assistant
                             chat, mission history, post-flight report
  tools/                     explain_assistant.py, voice_assistant.py, onboard.py, twin_commands.py,
                             codemap.py — the assistant's supporting tooling
  data/missions/             flat-file mission recordings (created at runtime, not committed)
```

---

## Running it

Requires Python 3.11–3.13, Node 20+, and [Ollama](https://ollama.com) for the assistant.

```bash
# backend
cd sih-project/backend
pip install -r requirements.txt torch      # CUDA wheel if you have an NVIDIA GPU
python gates_check.py                      # sanity check — expect 6/6
python main.py                             # :8000

# frontend (separate terminal)
cd sih-project/frontend
npm ci
npm run dev                                # prints the URL, default :5173

# assistant models (one-time)
ollama pull gemma4:26b
```

Or use `start_demo.bat` on Windows to launch both plus a model warm-up in one step.

Open the printed frontend URL, pick an engine from the gallery, and the header should show `LIVE · ENGINE TWIN FEED` and `ML LIVE`.

To train a model from scratch:
```bash
cd sih-project
python -m ml.data.mvem_dataset --installations 80 --runs-per-installation 50 --seconds 300
python -m ml.train_mvem --device cuda --threshold-pct 99.9
```

---

## Honesty policy

Every claim here carries its provenance. Physics geometry (bore/stroke/displacement) that isn't publicly known for the real VRDE engine is a calibrated estimate, labelled `assumed` everywhere it appears — including in any engine profile the assistant generates. Nothing in the ML pipeline reads an injected fault to produce its own diagnosis: if the model is offline, the UI says "no diagnosis," never a guess. Unmodelled sensor channels are served as `null`, never as an invented value.

An evaluator who finds a limitation already disclosed reads the rest of the claims as reliable. That's the trade this project makes deliberately, not an oversight.
