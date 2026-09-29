"""
In-app assistant API: chat, speech-to-text, text-to-speech. All local; nothing leaves this machine.

  POST /assistant/warm   load Whisper + Kokoro and keep the LLM resident (call when the chat opens)
  POST /assistant/chat   {text, engine, session}  -> {reply, kind, ms}
  POST /assistant/stt    raw audio body (webm/wav) -> {text, ms}
  POST /assistant/tts    {text}                    -> audio/wav

Grounding: the chat answers from the newest frame of THE ENGINE THE USER OPENED IN THIS TAB, never from another
engine or tab. Explaining is read-only. The only thing it can DO to the twin is what the user explicitly commands
("test at 12,000 ft with injector fouling on cylinder 3", "reset to healthy"): those come back as `actions` that the
app runs through the same code as the Fault console (tools/twin_commands.py validates them against the fault catalog).
Training or writing files still needs the confirmation flow in tools/assistant_actions.py, and decision questions
("should I land") are answered by the mission layer in code, not by the model.
"""
from __future__ import annotations

import io
import os
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(_TOOLS))
import explain_assistant as ea            # noqa: E402  facts renderer, prompt, Ollama call, decision rule
import assistant_actions as aa            # noqa: E402  onboarding / jobs / code questions
import twin_commands as tc                # noqa: E402  'test at 12,000 ft with injector fouling on cylinder 3'

router = APIRouter(prefix="/assistant")

MODEL = os.environ.get("PRAMANA_LLM", "gemma4:26b")
WHISPER = os.environ.get("PRAMANA_WHISPER", "base.en")
VOICE = os.environ.get("PRAMANA_VOICE", "af_heart")
MODELS = Path(__file__).resolve().parents[2] / "models"
_ML_ROOT = Path(__file__).resolve().parent.parent / "ml" / "weights"
DEFAULT_ENGINE = os.environ.get("PRAMANA_ENGINE", "vrde_180")
STALE_S = 10.0

# filled by main.py's telemetry loop
LATEST: dict[str, tuple[dict, float]] = {}                  # engine id -> (frame, wall time)
HISTORY: dict[str, deque] = {}                               # engine id -> recent frames (sampled)

_sessions: dict[str, dict] = {}                              # chat session id -> {"actions", "history"}
_lock = threading.Lock()                                     # ea.HIST is module-global
_stt = _tts = None
_load_lock = threading.Lock()


def publish(engine_id: str, frame: dict, client: str | None = None) -> None:
    """Called by the telemetry loop for every frame it sends. Stored per browser tab (client) as well as per
    engine, so a question is answered from the tab it was asked in, not from whichever tab sent a frame last."""
    now = time.time()
    keys = [engine_id] + ([f"{engine_id}|{client}"] if client else [])
    t = (frame.get("health") or {}).get("t")
    for k in keys:
        LATEST[k] = (frame, now)
        h = HISTORY.setdefault(k, deque(maxlen=120))
        if h and t is not None:
            last = (h[-1].get("health") or {}).get("t")
            if last is not None and t < last:                # a new session on this key: old history is another run
                h.clear()
            elif last is not None and t - last < 2:
                continue
        h.append(frame)


def _speech_models():
    global _stt, _tts
    with _load_lock:
        if _stt is None:
            from faster_whisper import WhisperModel
            try:
                _stt = WhisperModel(WHISPER, device="cuda", compute_type="float16", download_root=str(MODELS / "whisper"))
            except Exception:
                _stt = WhisperModel(WHISPER, device="cpu", compute_type="int8", download_root=str(MODELS / "whisper"))
        if _tts is None:
            from kokoro_onnx import Kokoro
            _tts = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    return _stt, _tts


def _session(sid: str) -> dict:
    if sid not in _sessions:
        _sessions[sid] = {"actions": aa.Actions(MODEL), "history": []}
    return _sessions[sid]


@router.post("/warm")
def warm():
    t0 = time.time()
    stt, tts = _speech_models()
    import numpy as np
    stt.transcribe(np.zeros(16000, dtype=np.float32), language="en")
    tts.create("ready", voice=VOICE, speed=1.05, lang="en-us")
    ea.ask(MODEL, "ok", 0.0, "reply with one word", [])       # loads the LLM and keeps it resident (keep_alive 2h)
    return {"ok": True, "ms": round((time.time() - t0) * 1000), "model": MODEL, "stt": WHISPER}


@router.post("/chat")
def chat(payload: dict = Body(...)):
    q = str(payload.get("text", "")).strip()
    if not q:
        raise HTTPException(400, "empty question")
    engine = str(payload.get("engine") or "")
    s = _session(str(payload.get("session") or "default"))
    t0 = time.time()

    client = str(payload.get("client") or "")
    key = f"{engine}|{client}" if client and f"{engine}|{client}" in LATEST else engine

    # Is this a command to the twin ("test at 12,000 ft with injector fouling on cylinder 3") or a new-engine
    # conversation? Commands win unless the user is already in an onboarding conversation.
    onboarding = aa.RX_ONBOARD.search(q) or s["actions"].active or s["actions"].pending
    pend = s.get("cmd_pending", "")
    if pend and (len(q.split()) > 12 or not re.search(r"\d|one|two|three|four|five|six", q, re.I)):
        pend = ""                                             # an unrelated message: drop the half-finished command
    is_command = (not onboarding) and bool(pend or tc.looks_like_command(q))

    if not is_command:
        acted = s["actions"].route(q)                         # new-engine onboarding, job status: no live frame needed
        if acted is not None:
            s["history"] += [{"role": "user", "content": q}, {"role": "assistant", "content": acted}]
            return {"reply": acted, "kind": "action", "ms": round((time.time() - t0) * 1000)}

    entry = LATEST.get(key)
    if entry is None:
        return {"reply": "I have no live data from this engine yet, so I can't say anything about its state or run a test on it. "
                         "Is the live feed running?", "kind": "nodata", "ms": 0}
    frame, at = entry
    age = time.time() - at
    if age > STALE_S:
        return {"reply": f"The live feed from this engine stopped {age:.0f} seconds ago, so I won't describe its state or run a test.",
                "kind": "stale", "ms": 0}

    if is_command:
        n_cyl = len((frame.get("slow") or {}).get("cht_C") or []) or 4
        rp = (_ML_ROOT / "v2" / "report.json") if engine == DEFAULT_ENGINE else (_ML_ROOT / "engines" / engine / "report.json")
        res = tc.handle(MODEL, q, n_cyl, rp, pending=pend)
        s["cmd_pending"] = res["pending"]
        s["history"] += [{"role": "user", "content": q}, {"role": "assistant", "content": res["reply"]}]
        return {"reply": res["reply"], "kind": "command" if res["actions"] else "command_incomplete",
                "actions": res["actions"], "ms": round((time.time() - t0) * 1000)}

    if ea.DECISION_Q.search(q):
        reply = ea.decision_answer(frame)
        kind = "decision"
    else:
        with _lock:                                           # rebuild the trend history for THIS engine
            ea.HIST.clear()
            for f in list(HISTORY.get(key, [])):
                ea.sample(f)
            facts, t = ea.render(frame)
        extra = s["actions"].code_context(q)
        if extra:
            facts += "\n\n" + extra
        reply = ea.ask(MODEL, facts, t, q, s["history"])
        kind = "answer"
    s["history"] += [{"role": "user", "content": q}, {"role": "assistant", "content": reply}]
    s["history"] = s["history"][-12:]
    return {"reply": reply, "kind": kind, "ms": round((time.time() - t0) * 1000), "frame_age_s": round(age, 1)}


@router.post("/stt")
async def stt(request: Request):
    data = await request.body()
    if len(data) < 800:
        raise HTTPException(400, "no audio received")

    def work():
        model, _ = _speech_models()
        t0 = time.time()
        segs, _info = model.transcribe(io.BytesIO(data), language="en", beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segs).strip()
        return {"text": text, "ms": round((time.time() - t0) * 1000)}
    return await run_in_threadpool(work)


@router.post("/tts")
def tts_endpoint(payload: dict = Body(...)):
    text = " ".join(str(payload.get("text", "")).split())[:600]
    if not text:
        raise HTTPException(400, "empty text")
    import soundfile as sf
    _, tts = _speech_models()
    samples, sr = tts.create(text, voice=VOICE, speed=1.05, lang="en-us")
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav")
