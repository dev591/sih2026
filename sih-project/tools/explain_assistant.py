"""
PRAMANA explain assistant — "explain, never decide", grounded on the LIVE twin.

Every question grabs ONE fresh frame from the running backend's WebSocket,
renders it to plain facts (so a small local model cannot misread nested JSON),
and asks a local Ollama model to answer ONLY from those facts. Questions that
ask for a decision ("should I land?") never reach the model: the code answers
with the mission layer's own recommendation field, verbatim.

Run (backend must be up):
    ollama pull qwen2.5:7b            # 8 GB+ RAM box; phi3:mini on smaller ones
    python tools/explain_assistant.py --ws ws://127.0.0.1:8000/ws/telemetry

Nothing here is mocked: if the backend is down, it says so and exits.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.request

import websockets

OLLAMA_URL = "http://localhost:11434/api/chat"

RESIDUALS = {
    "rho1_sd_vs_comp": "rho1 — air mass: speed-density vs compressor map",
    "rho2_sd_vs_lambda": "rho2 — air mass: speed-density vs fuel flow and lambda",
    "rho3_sd_vs_restr": "rho3 — air mass: speed-density vs intake restriction (not available on this engine)",
    "rho4_energy": "rho4 — first-law energy balance: fuel energy in vs heat + work out",
    "rho5_power": "rho5 — shaft power: engine model vs propeller-as-dynamometer",
    "rho6_9_cyl_dev": "rho6-rho9 — per-cylinder thermal deviation (cylinders 1-4)",
    "rho10_oil": "rho10 — oil pressure vs bearing-clearance model",
    "rho11_ripple": "rho11 — crankshaft 0.5-order speed ripple (one cylinder weaker than the rest)",
}

DECISION_Q = re.compile(
    r"\b(should|shall|must|can|do) (i|we)\b|\b(land|abort|divert|return to base|rtb|derate|continue the mission)\b",
    re.I,
)

SYSTEM = """You are the PRAMANA engine-health assistant for a DRDO UAV aero-diesel.
You EXPLAIN the engine-health facts below in plain spoken English. Rules:
- Use ONLY the FACTS below. If something is not in them, say "I don't have that data."
- Never invent a number. Quote numbers exactly as given, with units.
- Never recommend, advise or decide an action. The mission layer owns decisions.
- Residuals are in sigma (standard deviations of healthy noise). |value| under 2 is normal.
- Answer in at most 3 short sentences.

FACTS (live, t = {t} s):
{facts}
"""


def fetch_frame(ws_url: str) -> dict:
    async def one():
        async with websockets.connect(ws_url, max_size=None) as ws:
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    return asyncio.run(one())


def fmt(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}"


def render(frame: dict) -> tuple[str, float]:
    s, h = frame.get("slow", {}), frame.get("health", {})
    lines = []
    lines.append(f"Engine: {fmt(s.get('rpm'),0)} rpm, MAP {fmt(s.get('map_hPa'),0)} hPa, lambda {fmt(s.get('lambda'))}, "
                 f"altitude {fmt(s.get('altitude_ft'),0)} ft, turbo {fmt(s.get('turbo_rpm'),0)} rpm.")
    if s.get("cht_C"):
        lines.append("Cylinder head temps (C): " + ", ".join(f"cyl {i+1} {v:.0f}" for i, v in enumerate(s["cht_C"])) + ".")
    if s.get("egt_C"):
        lines.append("Exhaust gas temps (C): " + ", ".join(f"cyl {i+1} {v:.0f}" for i, v in enumerate(s["egt_C"])) + ".")
    lines.append(f"Conventional threshold monitor state: {h.get('limits_state', 'n/a')}.")
    ml = h.get("ml_status") or {}
    lines.append(f"ML layer active: {ml.get('active')}" + (f" ({ml.get('reason')})" if ml.get("reason") else "") + ".")

    rho = h.get("rho") or {}
    for key, label in RESIDUALS.items():
        v = rho.get(key)
        if isinstance(v, list):
            lines.append(f"{label}: " + ", ".join(f"cyl {i+1} {fmt(x)} sigma" for i, x in enumerate(v)))
        else:
            lines.append(f"{label}: {fmt(v)} sigma")

    an = h.get("anomaly") or {}
    if an:
        lines.append("Anomaly gate: " + ", ".join(f"{k}={v}" for k, v in an.items() if not isinstance(v, (list, dict))) + ".")

    dg = h.get("diagnosis") or {}
    if dg.get("unavailable"):
        lines.append("Diagnosis: UNAVAILABLE this tick (ML layer offline) — there is no fault call.")
    elif dg.get("top"):
        tops = []
        for hyp in dg["top"][:3]:
            cyl = f", cylinder {hyp['cylinder'] + 1}" if hyp.get("cylinder") is not None else ""
            tops.append(f"{hyp.get('fault')} ({hyp.get('p', 0) * 100:.0f}%{cyl})")
        lines.append("Diagnosis ranking: " + "; ".join(tops) + ".")
        lines.append(f"Sensor fault rather than engine fault: {dg.get('is_sensor_fault')}.")
        if dg.get("inseparable_from"):
            lines.append("Cannot be separated with these sensors from: " + ", ".join(dg["inseparable_from"]) + ".")

    rul = h.get("rul")
    for r in (rul if isinstance(rul, list) else [rul] if rul else [])[:2]:
        lines.append(f"Remaining useful life ({r.get('component')}): reported {fmt(r.get('reported_h'))} h, "
                     f"p10 {fmt(r.get('p10_h'))} h, p90 {fmt(r.get('p90_h'))} h.")

    mi = h.get("mission") or {}
    if mi:
        lines.append(f"Mission layer: recommended = {mi.get('recommended')}, P(complete) continue {fmt(mi.get('p_complete_continue'))}, "
                     f"derate {fmt(mi.get('p_complete_derate'))}, return {fmt(mi.get('p_complete_rtb'))}.")
    unm = h.get("unmodelled")
    if unm:
        lines.append("Channels NOT modelled (no data, never guess them): " + ", ".join(map(str, unm)) + ".")
    return "\n".join(lines), h.get("t", 0.0)


def decision_answer(frame: dict) -> str:
    mi = (frame.get("health") or {}).get("mission") or {}
    rec = mi.get("recommended")
    if rec is None:
        return "I explain the engine's state; I don't make that call. The mission layer has no recommendation right now."
    return (f"I explain, I don't decide. The mission-reliability layer's current recommendation is "
            f"'{rec}' — the decision stays with you.")


def ask(model: str, facts: str, t: float, q: str, history: list) -> str:
    msgs = [{"role": "system", "content": SYSTEM.format(t=t, facts=facts)}] + history[-6:] + [{"role": "user", "content": q}]
    body = json.dumps({"model": model, "messages": msgs, "stream": False,
                       "options": {"temperature": 0.1, "num_ctx": 4096}}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["message"]["content"].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default="ws://127.0.0.1:8000/ws/telemetry")
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--once", help="ask one question and exit (for testing)")
    a = ap.parse_args()
    history: list = []
    qs = [a.once] if a.once else None
    print(f"PRAMANA explain assistant · model {a.model} · live feed {a.ws}")
    while True:
        q = qs.pop(0) if qs else (None if a.once else input("\nyou> ").strip())
        if not q:
            if a.once:
                break
            continue
        try:
            frame = fetch_frame(a.ws)
        except Exception as e:  # backend down: say so, never fall back to fake data
            print(f"\nassistant> I can't reach the engine twin ({e}). No live data, so no answer.")
            if a.once:
                sys.exit(1)
            continue
        if DECISION_Q.search(q):
            ans = decision_answer(frame)
        else:
            facts, t = render(frame)
            ans = ask(a.model, facts, t, q, history)
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]
        print("\nassistant>", ans)
        if a.once:
            break


if __name__ == "__main__":
    main()
