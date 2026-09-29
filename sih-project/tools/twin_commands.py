"""
Spoken / typed COMMANDS to the digital twin: "test my engine at 12,000 feet with injector fouling on cylinder 3".

What it can do — exactly what the Fault console already does, through the same code path in the app:
    * set the commanded altitude
    * inject catalogued faults (one or several) with a cylinder and a severity
    * reset to healthy
It acts on the TWIN (the simulation) of the engine that is open, in the user's own tab. It cannot touch a real
engine, other engines, models or files.

The model only turns the user's words into a structured request (JSON). Everything else is code: the fault must be
in the catalog, a per-cylinder fault must name a cylinder that exists (otherwise it asks), and severities are clamped
to the same ranges the console allows. The result is a list of actions the app executes; the reply says exactly what
was applied, and what the ML is expected to do (measured alarm delays from the model's own test report).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import assistant_actions as aa

# key -> (label, per_cylinder, min, max, default, ML class, spoken synonyms)   — mirrors FaultConsole.tsx
CATALOG = {
    "injector":     ("injector fouling", True, 0.01, 0.12, 0.045, "injector_fouling", "clogged/fouled/dirty injector, injector"),
    "misfire":      ("misfire", True, 0.03, 0.30, 0.10, "injection_misfire", "misfire, skipping"),
    "detonation":   ("detonation", True, 0.03, 0.30, 0.10, "detonation", "knock, knocking, detonation"),
    "turbo":        ("turbocharger degradation", False, 0.01, 0.10, 0.04, "turbo_degradation", "turbo, turbocharger, compressor degradation"),
    "cooling":      ("radiator fouling", False, 0.02, 0.20, 0.08, "cooling_fouling", "radiator/cooling fouling, blocked radiator"),
    "coolantPump":  ("coolant pump degradation", False, 0.02, 0.20, 0.08, "coolant_pump_degradation", "coolant pump, water pump"),
    "bearing":      ("bearing wear", False, 0.03, 0.40, 0.12, "bearing_wear", "bearing wear, friction"),
    "oilLeak":      ("oil pump wear / leak", False, 0.03, 0.30, 0.10, "oil_pump_wear", "oil leak, oil pump wear, low oil pressure"),
    "ringWear":     ("piston ring wear", False, 0.01, 0.10, 0.04, "ring_wear", "ring wear, piston rings, blow-by"),
    "fuelFilter":   ("fuel filter clog", False, 0.02, 0.20, 0.06, "fuel_filter_clog", "fuel filter, clogged fuel filter, fuel starvation"),
    "chtSensor":    ("CHT sensor drift", True, 5.0, 60.0, 24.0, "cht_sensor_drift", "CHT / head temperature sensor drift"),
    "egtSensor":    ("EGT sensor drift", True, 10.0, 150.0, 70.0, "egt_sensor_drift", "EGT / exhaust temperature sensor drift"),
    "mapSensor":    ("MAP sensor drift", False, 5.0, 60.0, 20.0, "map_sensor_drift", "MAP / boost / manifold pressure sensor drift"),
    "lambdaSensor": ("lambda sensor drift", False, 0.005, 0.06, 0.03, "lambda_sensor_drift", "lambda / air-fuel sensor drift"),
    "unmodelled":   ("propeller blade damage (outside the fault library)", False, 0.01, 0.10, 0.04, "", "propeller damage, blade damage, unknown fault"),
}
SEVERITY = {"mild": 0.25, "light": 0.25, "small": 0.25, "moderate": 0.5, "medium": 0.5, "severe": 1.0, "heavy": 1.0, "big": 1.0}
ALT_MIN, ALT_MAX = 0.0, 20000.0        # the twin's altitude sweep was verified to 20,000 ft

RX_COMMAND = re.compile(
    r"\b(test|inject|simulate|introduce|apply|create|cause|trigger|induce|set (the )?altitude|climb|descend|fly at|"
    r"reset|clear (all )?(the )?faults?|remove (all )?(the )?faults?|make (it )?healthy|go back to healthy|run (a )?test)\b", re.I)
RX_QUESTION = re.compile(r"^\s*(what|which|why|how|when|where|is|are|does|do|can you explain|explain|tell me about)\b", re.I)
# Natural scenario phrasing ("what if the turbo fails", "how would it behave with a misfire", "show me what
# detonation looks like", "demonstrate a bearing fault") names no trigger verb above and starts with a question
# word, so RX_COMMAND misses it and RX_QUESTION actively blocks it — it used to fall through to the read-only
# explain model, which correctly said it could not run a test. These phrase a scenario, not a status question:
# if one names an actual fault from the catalog, treat it as a command too.
RX_SCENARIO = re.compile(
    r"\b(what (if|happens|would happen)|how would|how does it (behave|respond|react)|show me what|"
    r"demonstrate|what does it look like|walk me through)\b", re.I)
def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[,/\s]+", text.lower()) if len(w) >= 4}


_FAULT_WORD_SET = {w for v in CATALOG.values() for w in _words(v[0])} | {w for v in CATALOG.values() for w in _words(v[6])}
_FAULT_WORDS = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(_FAULT_WORD_SET)) + r")\b", re.I)

EXTRACT = """You convert an engine-test command into ONE JSON object. Output JSON only.
Keys:
  "reset": true if the user wants to clear faults / return to healthy, else false
  "add": true only if the user says also / in addition / another / on top of that (keep existing faults), else false
  "altitude": number or null   (as spoken; if they say 12k or twelve thousand, give 12000)
  "altitude_unit": "ft" or "m"
  "faults": list of {"fault": <key>, "cylinder": integer 1-based or null, "severity": "mild"|"moderate"|"severe"|null, "rate": number or null}
Fault keys and how people say them:
""" + "\n".join(f'  {k}: {v[0]} ({v[6]})' for k, v in CATALOG.items()) + """
Use "rate" only if the user gave an explicit number for the rate; otherwise use "severity" if they gave a word, else null.
Never invent a fault the user did not ask for. If the message is not a command, output {"reset": false, "add": false, "altitude": null, "faults": []}."""

_ORD = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4, "fourth": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8}


def looks_like_command(q: str) -> bool:
    if RX_SCENARIO.search(q) and _FAULT_WORDS.search(q):
        return True
    return bool(RX_COMMAND.search(q)) and not RX_QUESTION.match(q)


def _altitude_regex(t: str):
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand)?\s*(ft|feet|foot|')\b", t, re.I)
    if m:
        v = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        return v, "ft"
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(m|meters?|metres?)\b", t, re.I)
    if m and not re.search(r"\d\s*m(in|inutes?)\b", t, re.I):
        return float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1), "m"
    m = re.search(r"\b(\d{1,2})\s*k\b", t, re.I)
    if m:
        return float(m.group(1)) * 1000, "ft"
    return None


def _cyl_regex(t: str):
    m = re.search(r"\bcyl(?:inder)?\.?\s*(?:number\s*)?(\d|one|two|three|four|five|six)\b", t, re.I)
    if m:
        g = m.group(1).lower()
        return int(g) if g.isdigit() else _ORD.get(g)
    return None


def _expected(report_path: Path | None, cls: str, label: str = "") -> str:
    """Median alarm delay for this fault class from the model's own held-out test report (never a guess)."""
    if not report_path or not cls or not report_path.exists():
        return ""
    try:
        v = json.loads(report_path.read_text())["test"]["per_class"].get(cls)
        if v:
            return (f"In the model's held-out tests {label or 'this fault'} was detected in {v['detected_runs']} of {v['runs']} runs, "
                    f"typically about {v['median_latency_s']:.0f} s after it starts.")
    except Exception:
        pass
    return ""


def handle(model: str, text: str, n_cyl: int, report_path: Path | None, pending: str = "") -> dict:
    """-> {"reply": str, "actions": [...], "pending": str}.  actions is empty when the command is incomplete."""
    full = (pending + " " + text).strip() if pending else text
    try:
        got = aa._chat_json(model, EXTRACT, full)
    except Exception:
        got = {}
    faults_in = got.get("faults") or []
    reset = bool(got.get("reset"))
    add = bool(got.get("add"))
    alt, unit = got.get("altitude"), (got.get("altitude_unit") or "ft")
    rx = _altitude_regex(full)
    if rx:                                    # deterministic parsing beats the model on numbers
        alt, unit = rx
    cyl_rx = _cyl_regex(full)

    notes, actions, apply = [], [], []

    if alt is not None:
        try:
            ft = float(alt) * (3.28084 if str(unit).lower().startswith("m") else 1.0)
            clamped = min(max(ft, ALT_MIN), ALT_MAX)
            if abs(clamped - ft) > 1:
                notes.append(f"I limited the altitude to {clamped:,.0f} ft (the twin is verified between 0 and 20,000 ft).")
            actions.append({"type": "altitude", "ft": round(clamped)})
            apply.append(f"set the altitude to {clamped:,.0f} ft")
        except (TypeError, ValueError):
            pass

    config, applied_faults = {}, []
    for f in faults_in:
        key = f.get("fault")
        if key not in CATALOG:
            notes.append(f"I don't have a fault called '{key}' in the fault library, so I skipped it.")
            continue
        label, per_cyl, lo, hi, dflt, cls, _ = CATALOG[key]
        spec = {}
        if per_cyl:
            c = cyl_rx or f.get("cylinder")
            try:
                c = int(c) if c is not None else None
            except (TypeError, ValueError):
                c = None
            if c is None or not (1 <= c <= n_cyl):
                return {"reply": f"{label.capitalize()} happens in one cylinder. Which cylinder, 1 to {n_cyl}?",
                        "actions": [], "pending": full}
            spec["cyl"] = c - 1
        rate = f.get("rate")
        if isinstance(rate, (int, float)) and rate > 0:
            r = min(max(float(rate), lo), hi)
            if abs(r - float(rate)) > 1e-9:
                notes.append(f"I set the {label} rate to {r:g}, the nearest value the fault console allows ({lo:g} to {hi:g}).")
        else:
            sev = str(f.get("severity") or "").lower()
            r = dflt if sev not in SEVERITY else lo + (hi - lo) * SEVERITY[sev] * (0.6 if sev in ("severe", "heavy", "big") else 0.4)
            r = min(max(r, lo), hi)
        spec["rate"] = round(float(r), 4)
        config[key] = spec
        where = f" on cylinder {spec['cyl'] + 1}" if per_cyl else ""
        applied_faults.append(f"{label}{where} at rate {spec['rate']:g}")
        exp = _expected(report_path, cls, label)
        if exp:
            notes.append(exp)

    if config:
        actions.append({"type": "faults", "mode": "add" if add else "replace", "config": config})
        apply.append(("add " if add else "inject ") + " and ".join(applied_faults))
    if reset and not config:
        actions.append({"type": "reset"})
        apply.append("reset the engine to healthy")

    if not actions:
        return {"reply": "I didn't catch a test to run. Tell me an altitude and/or a fault, for example: "
                         "\"test at 12,000 feet with injector fouling on cylinder 3\", or say \"reset to healthy\".",
                "actions": [], "pending": ""}
    if [x["type"] for x in actions] == ["reset"]:
        return {"reply": "Done — the engine is reset to healthy and every injected fault is cleared.", "actions": actions, "pending": ""}
    reply = "Done — I'll " + " and ".join(apply) + ". It starts now on the live twin." + (
        " " + " ".join(dict.fromkeys(notes)) if notes else "") + " Say \"reset to healthy\" to undo it."
    return {"reply": reply, "actions": actions, "pending": ""}
