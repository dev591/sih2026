"""
PRAMANA voice assistant — speech in, speech out, FULLY LOCAL.

    hold F9, speak, release  ->  Whisper (local) -> the same grounded explain
    assistant (Ollama, local) -> Kokoro (local) speaks the answer.

Read-only: it reads the live frame (GET /latest); it can never change the engine or
inject a fault. Decision questions ("should I land") never reach the model — the code
answers from the mission layer, exactly as in explain_assistant.py.

No cloud STT/LLM/TTS: Whisper and Kokoro weights are local files; Ollama is local.
Every turn prints measured latencies (STT, LLM first token, first audio).

Run (backend up, UI open, Ollama running):
    python sih-project/tools/voice_assistant.py
Options: --mic <index> (see --list-devices), --key f9, --whisper base.en, --model gemma4:26b
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).parent))
import explain_assistant as ea  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
SR_IN = 16000


def pick_mic(arg):
    if arg is not None:
        return arg
    d = sd.default.device[0]
    if d is not None and d >= 0 and sd.query_devices(d)["max_input_channels"] > 0:
        return d
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and "mic" in dev["name"].lower():
            return i
    raise SystemExit("No microphone found. Use --list-devices and pass --mic <index>.")


def stream_llm(model, facts, t, q, history):
    """Yield text chunks from Ollama as they are generated."""
    msgs = [{"role": "system", "content": ea.SYSTEM.format(t=t, facts=facts)}] + history[-6:] + \
           [{"role": "user", "content": q}]
    body = json.dumps({"model": model, "messages": msgs, "stream": True, "think": False,
                       "keep_alive": "2h", "options": {"temperature": 0.1, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(ea.OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            if not line.strip():
                continue
            d = json.loads(line)
            piece = (d.get("message") or {}).get("content", "")
            if piece:
                yield piece
            if d.get("done"):
                break


def clean_for_speech(s: str) -> str:
    s = re.sub(r"[*_`#]", "", s)
    return s.replace("\n", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default="ws://127.0.0.1:8000/ws/telemetry")
    ap.add_argument("--model", default="gemma4:26b")
    ap.add_argument("--whisper", default="base.en")
    ap.add_argument("--key", default="f9")
    ap.add_argument("--mic", type=int, default=None)
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--list-devices", action="store_true")
    a = ap.parse_args()
    if a.list_devices:
        print(sd.query_devices())
        return

    from faster_whisper import WhisperModel
    from kokoro_onnx import Kokoro
    from pynput import keyboard

    mic = pick_mic(a.mic)
    print(f"mic device {mic}: {sd.query_devices(mic)['name']}")
    try:
        stt = WhisperModel(a.whisper, device="cuda", compute_type="float16", download_root=str(MODELS / "whisper"))
        dev_note = "cuda"
    except Exception:
        stt = WhisperModel(a.whisper, device="cpu", compute_type="int8", download_root=str(MODELS / "whisper"))
        dev_note = "cpu"
    tts = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    print(f"whisper {a.whisper} on {dev_note}; kokoro voice {a.voice}; LLM {a.model}")

    # warm up so the first real turn is not slow
    stt.transcribe(np.zeros(SR_IN, dtype=np.float32), language="en")
    tts.create("ready", voice=a.voice, speed=1.05, lang="en-us")

    def poll():
        while True:
            try:
                ea.sample(ea.fetch_frame(a.ws))
            except Exception:
                pass
            time.sleep(2)
    threading.Thread(target=poll, daemon=True).start()

    frames, recording = [], threading.Event()
    key = getattr(keyboard.Key, a.key, None) or keyboard.KeyCode.from_char(a.key)
    jobs: queue.Queue = queue.Queue()

    def cb(indata, n, tinfo, status):
        if recording.is_set():
            frames.append(indata[:, 0].copy())

    ch = 1
    try:
        instream = sd.InputStream(device=mic, samplerate=SR_IN, channels=ch, dtype="float32", callback=cb)
    except Exception:
        # some devices refuse 16 kHz mono; fall back to their native rate and resample
        info = sd.query_devices(mic)
        ch, native = min(2, int(info["max_input_channels"])), int(info["default_samplerate"])
        instream = sd.InputStream(device=mic, samplerate=native, channels=ch, dtype="float32", callback=cb)
        print(f"(mic opened at {native} Hz, will resample to 16 kHz)")
    instream.start()
    native_sr = int(instream.samplerate)

    def on_press(k):
        if k == key and not recording.is_set():
            frames.clear()
            recording.set()
            print("\n[listening…]", flush=True)

    def on_release(k):
        if k == key and recording.is_set():
            recording.clear()
            jobs.put(np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32))

    keyboard.Listener(on_press=on_press, on_release=on_release).start()
    print(f"\nHold {a.key.upper()} to talk, release to send. Ctrl+C to quit.\n")

    history: list = []
    while True:
        audio = jobs.get()
        if len(audio) < native_sr * 0.3:
            print("(too short, ignored)")
            continue
        if native_sr != SR_IN:
            audio = np.interp(np.linspace(0, len(audio), int(len(audio) * SR_IN / native_sr), endpoint=False),
                              np.arange(len(audio)), audio).astype(np.float32)
        t0 = time.perf_counter()
        segs, _ = stt.transcribe(audio, language="en", beam_size=1, vad_filter=True)
        q = " ".join(s.text.strip() for s in segs).strip()
        t_stt = time.perf_counter() - t0
        if not q:
            print("(heard nothing)")
            continue
        print(f"you> {q}   [STT {t_stt*1000:.0f} ms]")

        try:
            frame = ea.fetch_frame(a.ws)
        except Exception as e:
            msg = "I can't reach the engine twin, so I have no live data."
            print("assistant>", msg, f"({e})")
            samples, sr = tts.create(msg, voice=a.voice, speed=1.05, lang="en-us")
            sd.play(samples, sr)
            sd.wait()
            continue

        first_tok = first_audio = None
        answer = ""
        t1 = time.perf_counter()
        if ea.DECISION_Q.search(q):
            chunks = iter([ea.decision_answer(frame)])
        else:
            facts, t = ea.render(frame)
            chunks = stream_llm(a.model, facts, t, q, history)

        buf, spoken = "", []
        for piece in chunks:
            if first_tok is None:
                first_tok = time.perf_counter() - t1
            buf += piece
            answer += piece
            # speak each finished sentence while the model keeps generating
            while True:
                m = re.search(r"(.+?[.!?])\s+", buf)
                if not m:
                    break
                sent, buf = m.group(1), buf[m.end():]
                spoken.append(sent)
                samples, sr = tts.create(clean_for_speech(sent), voice=a.voice, speed=1.05, lang="en-us")
                if first_audio is None:
                    first_audio = time.perf_counter() - t1
                sd.wait()
                sd.play(samples, sr)
        if buf.strip():
            samples, sr = tts.create(clean_for_speech(buf), voice=a.voice, speed=1.05, lang="en-us")
            if first_audio is None:
                first_audio = time.perf_counter() - t1
            sd.wait()
            sd.play(samples, sr)
        sd.wait()
        print(f"assistant> {answer.strip()}")
        print(f"   [LLM first token {first_tok*1000:.0f} ms | first audio {first_audio*1000:.0f} ms "
              f"| speech-to-first-audio {(t_stt + first_audio)*1000:.0f} ms]")
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": answer.strip()}]


if __name__ == "__main__":
    main()
