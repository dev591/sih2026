"""
What the assistant can DO beyond explaining the live engine — under strict rules.

  * "add a new engine" / "generate a yaml"   -> collects the specs (it asks for what is missing)
  * LOW level  : generate the profile YAML + validation + a 60 s physics smoke test (files only, ~a minute)
  * HIGH level : the full pipeline for that engine (noise levels, dataset, training, prognostics, commissioning).
                 The plan and its estimated time are shown first and NOTHING starts until the user says yes.
  * "how does X work" / "where is Y"          -> answers from the code map (tools/codemap.py), with file:line

Rules that hold here:
  - The model only turns the user's words into spec fields (JSON). Code validates them and decides what runs.
  - Every job runs in an isolated copy of the project (tools/onboard.py); the live model and the running
    engine are never touched, and no fault is ever injected into a live session.
  - Long jobs run in a background thread; "how is it going" reads their real log.
  - Estimates come from speeds measured on this PC and are labelled as estimates.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path

import codemap
import onboard

OLLAMA_URL = "http://localhost:11434/api/chat"

RX_ONBOARD = re.compile(r"\b(new engine|another engine|add (an? )?engine|onboard|generate (an? )?(yaml|profile)|"
                        r"create (an? )?(yaml|profile|engine)|engine profile)\b", re.I)
RX_HIGH = re.compile(r"\b(high[- ]?level|full (test|pipeline)|train|retrain|dataset|full setup)\b", re.I)
RX_LOW = re.compile(r"\b(low[- ]?level|files? only|just (generate|make|create)|only (the )?(yaml|profile|files?))\b", re.I)
RX_STATUS = re.compile(r"\b(how is (it|the (job|training|run))|progress|status|done yet|still running|how long)\b", re.I)
RX_YES = re.compile(r"^\s*(yes|yeah|yep|y|go|go ahead|start|do it|confirm|proceed|ok(ay)?)\b", re.I)
RX_NO = re.compile(r"^\s*(no|nope|n|cancel|stop|don'?t|abort|never ?mind)\b", re.I)
RX_CODEQ = re.compile(r"\b(code|file|function|module|script|source|how (does|do|is|are)|where (is|are|do)|"
                      r"architecture|implemented|algorithm|which (file|module)|explain the (model|pipeline|system))\b", re.I)
RX_SIZE_STD = re.compile(r"\b(standard|full|big|large|accurate|thorough)\b", re.I)

EXTRACT = """Extract engine specifications from the user's message into ONE JSON object with exactly these keys.
Use null for anything not stated. Do not guess. Numbers only for numeric keys.
id (short lowercase id like "myeng_120", else null), name, cycle ("diesel" or "spark_ignition"),
aspiration ("turbocharged" or "naturally_aspirated"), cylinders (integer), displacement_L (litres),
bore_mm, stroke_mm, compression_ratio, rated_power_kW (kilowatts; convert hp by x0.7457), rated_speed_rpm,
max_continuous_rpm, critical_altitude_ft, cht_limit_C.
Output JSON only."""


def _chat_json(model: str, system: str, user: str) -> dict:
    body = json.dumps({"model": model, "stream": False, "think": False, "format": "json", "keep_alive": "2h",
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       "options": {"temperature": 0, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(json.loads(r.read())["message"]["content"])


def _regex_specs(t: str) -> dict:
    """Deterministic parsing of the numbers people actually say — overrides the LLM where it matches."""
    out = {}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hp|horse ?power)\b", t, re.I)
    if m:
        out["rated_power_kW"] = round(float(m.group(1)) * 0.7457, 1)
    m = re.search(r"(\d+(?:\.\d+)?)\s*kw\b", t, re.I)
    if m:
        out["rated_power_kW"] = float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:l|litres?|liters?)\b", t, re.I)
    if m:
        out["displacement_L"] = float(m.group(1))
    m = re.search(r"(\d+)\s*[- ]?cyl", t, re.I) or re.search(r"\b(?:cylinders?|cyl)\s*[:=]?\s*(\d+)", t, re.I)
    if m:
        out["cylinders"] = int(m.group(1))
    m = re.search(r"(\d{3,5})\s*rpm", t, re.I)
    if m:
        out["rated_speed_rpm"] = float(m.group(1))
    if re.search(r"\bdiesel\b", t, re.I):
        out["cycle"] = "diesel"
    elif re.search(r"\b(petrol|gasoline|spark)\b", t, re.I):
        out["cycle"] = "spark_ignition"
    if re.search(r"\b(turbo(charged)?)\b", t, re.I):
        out["aspiration"] = "turbocharged"
    elif re.search(r"\b(naturally aspirated|n/?a)\b", t, re.I):
        out["aspiration"] = "naturally_aspirated"
    return out


class Actions:
    def __init__(self, model: str):
        self.model = model
        self.spec: dict = {}
        self.mode: str | None = None          # "low" | "high"
        self.pending: dict | None = None      # {"kind": "run", "plan": ...}
        self.job: dict | None = None
        self.active = False                   # we are in an onboarding conversation

    # ---------------------------------------------------------------- routing
    def route(self, q: str) -> str | None:
        """Return a reply if this turn is handled here, else None (normal explain path)."""
        if self.pending:
            if RX_YES.match(q):
                return self._start_job()
            if RX_NO.match(q):
                self.pending = None
                return "Okay, I won't run it. Nothing was started."
        if self.job and RX_STATUS.search(q):
            return self._status()
        if RX_ONBOARD.search(q) or (self.active and self._looks_like_spec(q)):
            return self._onboard_turn(q)
        return None

    def code_context(self, q: str) -> str | None:
        """Extra facts for the explain prompt when the question is about the code itself."""
        if not RX_CODEQ.search(q):
            return None
        chunks = codemap.search(q, 3)
        parts = ["CODE MAP (every module, one line each):\n" + codemap.overview(3500),
                 "MOST RELEVANT CODE FOR THIS QUESTION:"]
        for c in chunks:
            parts.append(f"--- {c['path']}:{c['line']} ({c['title']})\n{c['text'][:1200]}")
        return "\n".join(parts)

    # ---------------------------------------------------------------- onboarding conversation
    def _looks_like_spec(self, q: str) -> bool:
        return bool(_regex_specs(q)) or bool(re.search(r"\b(id|name|called)\b", q, re.I)) or bool(RX_LOW.search(q) or RX_HIGH.search(q))

    def _onboard_turn(self, q: str) -> str:
        self.active = True
        if RX_LOW.search(q):
            self.mode = "low"
        elif RX_HIGH.search(q):
            self.mode = "high"
        try:
            got = _chat_json(self.model, EXTRACT, q)
        except Exception:
            got = {}
        for k in onboard.SPEC_FIELDS:
            v = got.get(k)
            if v not in (None, "", "null"):
                self.spec[k] = v
        self.spec.update(_regex_specs(q))
        m = re.search(r"(?:id|called|call it|named?)\s*[:=]?\s*[\"']?([a-z][a-z0-9_]{2,30})", q, re.I)
        if m and "id" not in self.spec:
            self.spec["id"] = m.group(1).lower()
        if "cylinders" in self.spec:
            try:
                self.spec["cylinders"] = int(self.spec["cylinders"])
            except Exception:
                self.spec.pop("cylinders")
        miss = onboard.missing_spec(self.spec)
        if miss:
            have = ", ".join(f"{k} = {v}" for k, v in self.spec.items()) or "nothing yet"
            need = "; ".join(f"{k} ({onboard.SPEC_FIELDS[k][1]}{' in ' + onboard.SPEC_FIELDS[k][2] if onboard.SPEC_FIELDS[k][2] else ''})" for k in miss)
            return f"I have: {have}. I still need: {need}. Anything else you don't give me is copied from a base engine and marked as assumed."
        if self.mode is None:
            eid = str(self.spec["id"]).lower()
            return ("I have everything I need. Two options: LOW level — I generate the profile YAML, validate it and smoke-test "
                    "the physics, files only, about a minute. HIGH level — I also run the full pipeline (noise levels, dataset, training, "
                    f"prognostics, commissioning) in an isolated copy; the quick version takes about {onboard.plan(eid, 'quick')['total']}, "
                    f"the standard one about {onboard.plan(eid, 'standard')['total']}. Which one — low or high?")
        return self._do_mode(q)

    def _do_mode(self, q: str) -> str:
        eid = str(self.spec["id"]).lower()
        try:
            res = onboard.write_low_level(self.spec)
        except Exception as e:
            self.active = False
            return f"I could not generate the profile: {e}"
        c = res["check"]
        sm = c.get("smoke") or {}
        smoke = (f"physics smoke test passed ({sm.get('rpm')} rpm, {sm.get('brake_kW')} kW at 5,000 ft, 80% throttle)"
                 if sm.get("ok") else f"physics smoke test FAILED ({sm.get('error', 'non-finite output')})")
        msg = (f"Generated {res['yaml']}. Validation: {len(c['errors'])} errors, {len(c['warnings'])} assumed fields "
               f"(inherited from {res['provenance']['base']}, not rescaled to your engine); {smoke}. "
               f"Full report: {res['validation']}.")
        if c["errors"]:
            self.active = False
            return msg + " It has errors, so I will not run anything further."
        if self.mode == "low":
            self.active = False
            self.spec, self.mode = {}, None
            return msg + " That was the low-level run. Say 'high level' if you want me to plan the training too."
        size = "standard" if RX_SIZE_STD.search(q) else "quick"
        p = onboard.plan(eid, size, device=self._device())
        self.pending = {"kind": "run", "plan": p}
        return (msg + "\n" + onboard.describe(p) + "\nHonest limits: " + " ".join(onboard.LIMITS) +
                f"\nThis runs in an isolated copy and never touches the live model. Say 'yes' to start (about {p['total']}), or 'no'.")

    @staticmethod
    def _device() -> str:
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    # ---------------------------------------------------------------- job control
    def _start_job(self) -> str:
        p = self.pending["plan"]
        self.pending = None
        job = {"plan": p, "log": [], "status": "running", "started": time.time(), "result": None}
        self.job = job

        def work():
            try:
                job["result"] = onboard.run(p, log=lambda s: job["log"].append(s))
                ok = all(s["ok"] for s in job["result"]["steps"])
                job["status"] = "done" if ok else "failed"
            except Exception as e:
                job["status"] = "failed"
                job["log"].append(f"error: {e}")
        threading.Thread(target=work, daemon=True).start()
        self.active = False
        self.spec, self.mode = {}, None
        return f"Started. I'll report progress when you ask, and the result when it finishes. Estimated {p['total']}."

    def _status(self) -> str:
        j = self.job
        el = time.time() - j["started"]
        last = j["log"][-1] if j["log"] else "starting"
        if j["status"] == "running":
            return f"Running for {onboard._fmt(el)} of an estimated {j['plan']['total']}. Latest: {last}"
        if j["status"] == "failed":
            bad = [s for s in (j["result"] or {}).get("steps", []) if not s["ok"]]
            return f"It failed after {onboard._fmt(el)}. " + (f"Step '{bad[0]['step']}' — log: {bad[0]['log']}" if bad else last)
        return "Finished in " + onboard._fmt(el) + ". " + self._summary(j)

    @staticmethod
    def _summary(j: dict) -> str:
        rep = (j.get("result") or {}).get("report")
        if not rep or not Path(rep).exists():
            return "Steps completed but there is no report.json."
        t = json.loads(Path(rep).read_text())["test"]
        tr = sum(v["runs"] for v in t["per_class"].values())
        td = sum(v["detected_runs"] for v in t["per_class"].values())
        return (f"On held-out test installations of the twin: recall {td}/{tr} = {td / tr:.1%}, top-1 after alarm "
                f"{t['top1_after_alarm_all']:.1%}, {t['false_alarm_episodes_per_hour']:.2f} false-alarm episodes per hour. "
                f"Files are in {j['result']['workspace']}; it is NOT installed as the live model.")
