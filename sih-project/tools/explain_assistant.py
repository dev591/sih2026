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
import collections
import json
import re
import sys
import threading
import time
import urllib.request

import websockets

OLLAMA_URL = "http://localhost:11434/api/chat"

RESIDUALS = {
    "rho1_sd_vs_comp": "rho1 — air mass: speed-density vs compressor map",
    "rho2_sd_vs_lambda": "rho2 — air mass: speed-density vs fuel flow and lambda",
    "rho3_sd_vs_restr": "rho3 — air mass: speed-density vs intake restriction (not available on this engine)",
    "rho4_energy": "rho4 — first-law energy balance: fuel energy in vs heat + work out",
    "rho5_power": "rho5 — shaft power: engine model vs propeller-as-dynamometer",
    "rho6_9_cyl_dev": "rho6-rho9 — per-cylinder thermal deviation (one value per cylinder)",
    "rho10_oil": "rho10 — oil pressure vs bearing-clearance model",
    "rho11_ripple": "rho11 — crankshaft 0.5-order speed ripple (one cylinder weaker than the rest)",
}

DECISION_Q = re.compile(
    r"\b(should|shall|must|can|do) (i|we)\b|\b(land|abort|divert|return to base|rtb|derate|continue the mission)\b",
    re.I,
)

SYSTEM = """You are the PRAMANA engine-health analyst for a DRDO UAV aero-diesel. You can see everything the
digital twin knows about the engine right now (the FACTS below). Observe them, think, then tell the user what you
actually conclude, in plain spoken English, like a knowledgeable colleague.

How to think:
- Look at ALL the evidence: measured values against what a healthy twin predicts, the residuals (in sigma, standard
  deviations of healthy noise; |value| under 2 is normal), the estimated health parameters (1.0 = nominal), how things
  are changing, and what the ML layer says. The ML alarm is ONE input, not the verdict. You are not limited to it.
- If the evidence shows something off but the ML has not alarmed, say so plainly ("early sign: ...; the ML hasn't
  confirmed it yet"). If the ML alarms but the evidence looks weak, say that. If everything is inside normal, say the
  engine looks healthy. "No alarm" never proves health, and a quiet gate never overrides what the numbers show.
- Reason about causes physically (what a cylinder running cold, an oil-pressure drop, a boost shortfall usually mean),
  say "I think" for interpretation, and say how sure you are. If two causes look alike, say so.
- If the novelty test is exceeded, say the ML has not seen this kind of fault and its ranking is not reliable.

What you may and may not say:
- Every number you quote must come from the FACTS, with its unit; never invent one. If something is not in the FACTS,
  say you don't have it.
- If the FACTS include a CODE MAP and relevant code, that IS your knowledge of the project: explain from it and cite the
  file. Say you don't know only if the code shown does not answer.
- Never recommend, advise or decide an action (land, derate, continue): the mission layer owns decisions.
- There is no remaining-life estimate unless the FACTS give one; never predict a time to failure. You can still say how
  much has degraded and whether it is getting worse or stable.

How to answer:
- Answer the question that was asked. A question about one reading ("temp of cylinder 1", "oil pressure") gets that
  value in one short sentence ("temperature" of a cylinder means its head temperature); add one clause only if that
  reading is clearly off. A question about the engine's state, a fault or "what do you think" gets your analysis: what
  you observe, what you conclude, how sure you are.
- Do not recite lists of numbers. Pick the two or three that carry the point.
- Keep it to 2-4 short sentences.

FACTS (live, t = {t} s):
{facts}
"""


def fetch_frame(ws_url: str) -> dict:
    """The newest frame the backend sent to the UI (GET /latest). Opening our own
    websocket would start a NEW engine session — a fresh healthy engine, not the
    one on screen — so there is deliberately no fallback to that."""
    base = re.sub(r"^ws", "http", ws_url)
    base = base.split("/ws/")[0]
    with urllib.request.urlopen(base + "/latest", timeout=5) as r:
        data = json.loads(r.read())
    if not data.get("available"):
        raise RuntimeError("no frame yet — open the UI (or any client) so the engine is running")
    return data["frame"]


def fmt(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}"


# ---- short history of the live frame, so it can say what is CHANGING ----
HIST: collections.deque = collections.deque(maxlen=120)   # ~4 min at one sample / 2 s


def _flat_theta(h: dict) -> dict:
    out = {}
    for k, d in (h.get("theta") or {}).items():
        v = d.get("value")
        if isinstance(v, list):
            out.update({f"{k} cyl {i+1}": x for i, x in enumerate(v)})
        elif v is not None:
            out[k] = v
    return out


def sample(frame: dict) -> None:
    h = frame.get("health") or {}
    t = h.get("t")
    if t is None:
        return
    if HIST and t < HIST[-1]["t"]:      # backend session restarted: old history is another engine
        HIST.clear()
    if HIST and t == HIST[-1]["t"]:
        return
    an = h.get("anomaly") or {}
    HIST.append({"t": t, "alarm": bool((an.get("persistence") or {}).get("met")),
                 "score": an.get("score"), "theta": _flat_theta(h)})


def trend_lines(hist: list) -> list:
    if len(hist) < 3:
        return ["Trend: not enough history yet to say what is changing."]
    now = hist[-1]
    out = []
    since = None
    for x in reversed(hist):
        if not x["alarm"]:
            break
        since = x["t"]
    out.append(f"Alarm state: active for {now['t'] - since:.0f} s (since t={since:.0f})." if since is not None and now["alarm"]
               else "Alarm state: not active.")
    old = hist[0]
    dt = now["t"] - old["t"]
    if dt >= 10:
        if old.get("score") is not None and now.get("score") is not None:
            out.append(f"Anomaly score over the last {dt:.0f} s: {old['score']:.2f} -> {now['score']:.2f}.")
        moved = []
        for k, v in now["theta"].items():
            o = old["theta"].get(k)
            if o is not None and abs(v - o) >= 0.02:
                moved.append(f"{k} {o:.3f} -> {v:.3f}")
        out.append((f"Health parameters that CHANGED over the last {dt:.0f} s: " + "; ".join(moved) + ".")
                   if moved else f"No health parameter has moved noticeably over the last {dt:.0f} s (stable).")
    return out


def departures(frame: dict) -> str:
    """What is measurably off right now, found in code from the frame — so the model can observe it even when the
    ML has not alarmed yet. Thresholds are ~4x healthy noise; they only decide what gets LISTED, not what is a fault."""
    s, h, pr = frame.get("slow") or {}, frame.get("health") or {}, frame.get("predicted") or {}
    out = []
    for name, unit, lim in (("egt_C", "C", 8.0), ("cht_C", "C", 6.0)):
        meas, twin = s.get(name), pr.get(name)
        if isinstance(meas, list) and isinstance(twin, list):
            for i, (m, t) in enumerate(zip(meas, twin)):
                if m is not None and t is not None and abs(m - t) >= lim:
                    out.append(f"{name.split('_')[0].upper()} cylinder {i + 1}: {m:.0f} {unit} measured vs {t:.0f} {unit} healthy twin ({m - t:+.0f})")
    for name, label, unit, rel, ab, nd in (("oil_press_bar", "oil pressure", "bar", None, 0.15, 2),
                                           ("map_hPa", "manifold pressure", "hPa", 0.03, None, 0),
                                           ("fuel_flow_kgps", "fuel flow", "kg/s", 0.04, None, 5)):
        m, t = s.get(name), pr.get(name)
        if m is None or t in (None, 0):
            continue
        if (rel and abs(m - t) / abs(t) >= rel) or (ab and abs(m - t) >= ab):
            out.append(f"{label}: {m:.{nd}f} {unit} measured vs {t:.{nd}f} {unit} healthy twin ({(m - t) / t * 100:+.0f}%)")
    rho = h.get("rho") or {}
    for key, label in RESIDUALS.items():
        v = rho.get(key)
        vals = v if isinstance(v, list) else [v]
        for i, x in enumerate(vals):
            if x is not None and abs(x) >= 2.0:
                where = f" cylinder {i + 1}" if isinstance(v, list) else ""
                out.append(f"residual {key}{where} = {x:+.1f} sigma")
    for k, x in (h.get("rho_ext") or {}).items():
        if x is not None and abs(x) >= 2.0:
            out.append(f"residual {k} = {x:+.1f} sigma")
    for k, d in (h.get("theta") or {}).items():
        v = d.get("value")
        vs = v if isinstance(v, list) else [v]
        for i, x in enumerate(vs):
            if x is not None and abs(x - 1.0) >= 0.05:
                out.append(f"health parameter {k}{'' if not isinstance(v, list) else f' cylinder {i + 1}'} = {x:.2f} (nominal 1.0)")
    if not out:
        return "Notable departures from healthy: NONE — every residual is within 2 sigma and the measured values match the healthy twin."
    return "Notable departures from healthy (found automatically, strongest evidence first-hand): " + "; ".join(out[:10]) + "."


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
    elif an and not (an.get("persistence") or {}).get("met") and an.get("score") is not None:
        # The classifier only runs after the anomaly gate alarms; before that its percentages mean nothing
        # (the served 'healthy 39%' is not a health score).
        lines.append(f"ML status: the anomaly gate has NOT alarmed (score {an['score']:.3f}, threshold {an.get('threshold')}). "
                     "That does not prove the engine is healthy — judge from the departures listed below. The classifier's "
                     "percentages are not computed before an alarm: never quote them.")
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
    # ---- everything else the backend knows (read-only) ----
    lines.append(f"Other sensors: oil pressure {fmt(s.get('oil_press_bar'))} bar, oil temp {fmt(s.get('oil_temp_C'),1)} C, "
                 f"coolant {fmt(s.get('coolant_temp_C'),1)} C, gearbox oil {fmt(s.get('gearbox_oil_C'),1)} C, "
                 f"fuel flow {fmt(s.get('fuel_flow_kgps'),5)} kg/s, fuel rail {fmt(s.get('fuel_rail_bar'),0)} bar, "
                 f"throttle {fmt(s.get('throttle_pct'),0)} %, vibration {fmt(s.get('vib_rms_g'),2)} g rms, "
                 f"intake air {fmt(s.get('iat_K'),1)} K, ambient {fmt(s.get('p_amb_hPa'),0)} hPa.")
    pr = frame.get("predicted") or {}
    if pr:
        def pv(name, meas, unit, nd=1):
            p = pr.get(name)
            if isinstance(p, list):
                return f"{name}: " + ", ".join(f"cyl {i+1} measured {fmt(m,0)} vs healthy-twin {fmt(x,0)}" for i, (m, x) in enumerate(zip(meas or [], p))) + f" {unit}"
            return f"{name}: measured {fmt(meas,nd)} vs healthy-twin {fmt(p,nd)} {unit}"
        lines.append("Measured vs what a HEALTHY twin predicts at this operating point — "
                     + "; ".join([pv("egt_C", s.get("egt_C"), "C"), pv("cht_C", s.get("cht_C"), "C"),
                                  pv("oil_press_bar", s.get("oil_press_bar"), "bar", 2),
                                  pv("map_hPa", s.get("map_hPa"), "hPa", 0),
                                  pv("fuel_flow_kgps", s.get("fuel_flow_kgps"), "kg/s", 5)]) + ".")
    v = h.get("virtual") or {}
    if v:
        lines.append(f"Twin estimates: brake power {fmt(v.get('brake_power_kW'),1)} kW, BSFC {fmt(v.get('bsfc_g_per_kWh'),0)} g/kWh, "
                     f"air mass flow {fmt(v.get('air_mass_flow_kgps'),4)} kg/s, turbo shaft {fmt(v.get('turbo_shaft_rpm_est'),0)} rpm.")
    th = h.get("theta") or {}
    if th:
        parts = []
        for k, d in th.items():
            val, sg = d.get("value"), d.get("sigma")
            if isinstance(val, list):
                parts.append(f"{k} = " + ", ".join(f"cyl {i+1} {x:.3f}" for i, x in enumerate(val)))
            elif val is not None:
                parts.append(f"{k} = {val:.3f} (+/-{sg})")
        lines.append("Estimated health parameters (1.0 = nominal healthy; lower = degraded): " + "; ".join(parts) + ".")
    nv = h.get("novelty") or {}
    if nv:
        lines.append(f"Novelty test (is this a fault the ML was NOT trained on): index {nv.get('index')} vs threshold "
                     f"{nv.get('threshold')}, exceeded = {nv.get('exceeded')}.")
    tc = h.get("twin_confidence") or {}
    if tc:
        lines.append(f"Twin confidence: {tc.get('value')} (how close the pattern is to a measured fault signature).")
    ex = h.get("explain") or {}
    if ex.get("features") and ex.get("live"):
        pairs = sorted(zip(ex["features"], ex["live"]), key=lambda kv: -abs(kv[1]))[:5]
        lines.append(f"Best-matching fault signature: {ex.get('signature_key')} (match {ex.get('match_cosine')}). "
                     "Strongest signals: " + ", ".join(f"{n} {x:.1f}" for n, x in pairs) + ".")
    for r in (rul if isinstance(rul, list) else [rul] if rul else [])[:1]:
        if r.get("reported_h") is None:
            lines.append(f"Remaining life for {r.get('component')}: NOT AVAILABLE (no redline crossing estimated) — say so, never guess.")
    if mi:
        lines.append(f"Mission: point of no return in {fmt(mi.get('point_of_no_return_s'),0)} s; "
                     f"recommended power {fmt(mi.get('recommended_power_pct'),0)} %.")
    lines.extend(trend_lines(list(HIST)))
    lines.insert(1, departures(frame))
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
    # think=False: reasoning models (gemma4, qwen3) otherwise burn tokens on hidden
    # reasoning; keep_alive avoids a ~14 s cold load between questions.
    body = json.dumps({"model": model, "messages": msgs, "stream": False,
                       "think": False, "keep_alive": "2h",
                       "options": {"temperature": 0.1, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["message"]["content"].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default="ws://127.0.0.1:8000/ws/telemetry")
    ap.add_argument("--model", default="gemma4:26b")
    ap.add_argument("--once", help="ask one question and exit (for testing)")
    a = ap.parse_args()
    history: list = []

    def poll():
        while True:
            try:
                sample(fetch_frame(a.ws))
            except Exception:
                pass
            time.sleep(2)
    threading.Thread(target=poll, daemon=True).start()
    if a.once:
        time.sleep(12)   # let the sampler collect a little history
    qs = [x.strip() for x in a.once.split("||")] if a.once else None   # "a || b" = several turns, one session
    import assistant_actions
    act = assistant_actions.Actions(a.model)
    print(f"PRAMANA explain assistant · model {a.model} · live feed {a.ws}")
    while True:
        q = qs.pop(0) if qs else (None if a.once else input("\nyou> ").strip())
        if not q:
            if a.once:
                break
            continue
        if a.once:
            print(f"\nyou> {q}")
        acted = act.route(q)          # onboarding / low- and high-level jobs / job status (no engine needed)
        if acted is not None:
            print("\nassistant>", acted)
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": acted}]
            if a.once and not qs:
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
            extra = act.code_context(q)
            if extra:
                facts += "\n\n" + extra
            ans = ask(a.model, facts, t, q, history)
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]
        print("\nassistant>", ans)
        if a.once and not qs:
            break


if __name__ == "__main__":
    main()
