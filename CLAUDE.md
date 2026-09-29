# PRAMANA — handoff for the fast recording PC (branch `demo-final`)

> **FIRST THING:** the user decided ALL training happens on THIS machine. Nothing
> was trained on the old laptop (a run was started and deliberately stopped).
> Start at step 0 below and go straight through to step 7.

You are picking this up on a fast (20+ core) machine on the LAST day before the
SIH26054 (DRDO) demo recording. The previous machine (8 GB M3) was too slow to
train. Your job, in order: **set up → train → test end to end → set up Ollama →
hand the user a running system to record.** Work fast but never fake anything.

## Non-negotiable user rules
1. **Verify by running, never by docs.** Docs/PDFs/old notes (incl.
   `sih-project/docs/VALIDATION-STATUS.md`) are leads, not facts. Every number
   you tell the user must come from a command you ran on this machine.
2. **No fake / answer-key values.** Nothing may read the injected fault config
   to produce a diagnosis, RUL or probability. If ML is down, the UI must say
   "no diagnosis", never a guess. Unmodelled channels stay `null`.
3. **Do not change the `diagnosis` block shape** served by `backend/main.py` —
   the frontend reads it in ~9 files.
4. **Ask the user before committing or pushing.**
5. Keep the user posted in short plain sentences; they are under time pressure.

## Where everything is
- `sih-project/backend/` — FastAPI + WebSocket twin (`main.py`, :8000,
  `ws://127.0.0.1:8000/ws/telemetry`). Physics: `twin/mvem.py`, profile
  `sih-project/config/engine_vrde_180.yaml`, noise σ in
  `backend/config/engines/vrde_180/`.
- `sih-project/ml/` — `data/mvem_dataset.py` (dataset from the SAME engine
  model the server steps), `train_mvem.py` (M2 anomaly gate + M3 fault/severity,
  split BY INSTALLATION so test engines are never seen), `inference.py` (loads
  `ml/weights/v2/`), `ukf/`, `prognostics.py`, `commission.py`.
- `sih-project/frontend/` — the **ui-overhaul** UI (VRDE 3D engine, cutaway,
  light chrome + dark 3D stage). This is the one to record with.
- `sih-project/tools/explain_assistant.py` — Ollama "explain, never decide"
  assistant grounded on the live WebSocket frame (tested against the live
  backend on the old machine; the LLM step itself not yet tested).
- `sih-project/data/mvem_v1/` — a finished **900-run dataset** (30 installations
  × 30 runs × 240 s) generated on the old laptop from the calibrated physics.
  Use it to get a working model FAST, then generate the bigger one.

## State when handed over (verified by running on 2026-09-28/29)
- Physics calibrated (Kiro): full throttle 137.6 kW at SL, 129.2 kW at
  11,000 ft (−3.7 % vs DRDO 180 hp spec), BSFC 204 g/kWh; `gates_check.py`
  6/6 — but gates 3 and 6 use LOOSE criteria (≥50 % rated; "flat" = within
  35 %). MAP actually starts falling at ~6,000 ft. Say this honestly if asked.
- **ML has never been trained on this physics.** `ml/weights/v2/` has only
  `jacobian.json` + `commissioning.json`; `m2.pt`, `m3.pt`, `config.json` do not
  exist → backend serves "ML OFFLINE · NO DIAGNOSIS". That is correct behaviour
  until you train.
- Healthy boot frame showed ρ4 (energy balance) = 5.4 σ on the FIRST frame —
  probably a warm-up transient. Check steady-state healthy residuals are < ~2 σ
  or the demo will false-alarm.
- The old "graphs go blank" bug was the OLD frontend crashing on `null`
  unmodelled values; it is replaced by ui-overhaul here. If any panel blanks,
  open the browser console — it will be a `null.toFixed`; guard it.
- Old Kiro claims that are FALSE — never repeat them: "λ 1.15 is a published
  smoke limit" (it is our engineering choice at the lean end of the typical
  aero-diesel 1.15–1.3 range); "ML weights were trained on the old engine" (they
  never existed); Kaggle "94.8 %" (leaky dataset).

## Steps

### 0. Environment (detect the OS first — this PC may be Windows)
- Python 3.11–3.13. `pip install -r sih-project/backend/requirements.txt torch`
  (use the CUDA torch wheel if there is an NVIDIA GPU; CPU wheel otherwise).
- Node 20+. `cd sih-project/frontend && npm ci`.
- Sanity: `cd sih-project/backend && python gates_check.py` (expect 6/6) and
  `python verify.py`.

### 1. Train a first model on the 900-run dataset (minutes)
```
cd sih-project
python -m ml.train_mvem --device cuda   # or cpu; default picks mps/cpu only
```
Read `ml/weights/v2/report.json`: false-alarm rate on healthy test
installations, detection rate + median latency per fault, top-1/top-2 after
alarm, cylinder localisation, ambiguity groups. Copy `ml/weights/v2/` to
`ml/weights/v2_small/` as a fallback.

### 2. Bigger dataset for accuracy (all cores), then retrain
```
mv data/mvem_v1 data/mvem_small
python -u -m ml.data.mvem_dataset --installations 80 --runs-per-installation 50 --seconds 300 --workers <cores-2>
python -m ml.train_mvem --device cuda
```
Old laptop speed: ~40 s per 240-s run per worker. It prints an ETA every 50
runs — if the ETA is too long for the day, stop and use fewer installations.
Keep whichever model is better on the held-out TEST installations (not val).
Known physical ambiguity (more data will NOT fix these; report them as
"one of these two"): bearing wear ≈ oil-pump wear, mild radiator fouling
invisible at cruise (thermostat), detonation ≈ EGT-sensor drift.

### 3. Remaining ML artifacts (untested on the new physics — fix if they fail)
```
python -m ml.ukf.sensitivity
python -m ml.prognostics
python -m ml.commission --installation 42
```

### 4. Run the system
- Backend: `cd sih-project/backend && python main.py` → :8000
- Frontend: `cd sih-project/frontend && npm run dev` → open the printed URL.
  (Its default WS is `ws://<host>:8000/ws/telemetry`; override with
  `VITE_PRAMANA_WS` if the backend is elsewhere.)
- Header must show `LIVE · ENGINE TWIN FEED` and `ML LIVE` (green).

### 5. End-to-end test EVERY fault through the real WebSocket
Write a script that connects to the WS, sends
`{"type":"reset"}`, then `{"type":"fault_config","config":{KEY:{"startT":T,"rate":R,"cyl":C}}}`
for each key: injector, misfire, detonation (per-cylinder, `cyl` 0-indexed),
turbo, cooling, coolantPump, bearing, oilLeak, ringWear, fuelFilter,
chtSensor, egtSensor, mapSensor, lambdaSensor, unmodelled (novelty), plus
healthy. Rates like the UI (injector 0.045/min). Record: time to first alarm,
the served top-1 / top-2, cylinder, `is_sensor_fault`, and false alarms in the
healthy run. Also click through the UI: every panel, Cutaway, Report, Simple
view, Explain drawer, Demo script, Rotax switch — no blank screens, no console
errors.

### 6. Ollama explain assistant
Install Ollama, `ollama pull qwen2.5:7b` (or `llama3.1:8b`; use a bigger
model if the machine has the RAM/GPU). With the backend running:
`python sih-project/tools/explain_assistant.py --model qwen2.5:7b`.
Test with a fault injected: "what is wrong", "which cylinder", "why do you
think that", "what is the oil temperature" (it must quote the real number or
say it has no data), "should I land" (must NOT advise — the code answers from
the mission layer). Small models invent facts — check every number it says
against the frame.

### 7. Hand over
Tell the user, in plain words: the real measured accuracy numbers (with n),
what each fault does in the live demo, the known ambiguities, and the exact
commands to start backend + frontend + assistant for recording.
