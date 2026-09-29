"""
Prognostics and mission reliability from the twin's OWN estimates.

What this replaces: backend/main.py's `_mission_probabilities(fault_config)`
read the injected fault — the answer key — and turned its rate into
probabilities with a hand-written formula, under a UI label that said
"Monte Carlo, M = 200". The served RUL came from twin/damage.py integrated on
that same answer key. Neither used anything the twin had inferred.

Two parts:

1. REDLINE TABLE (offline, `python -m ml.prognostics`).
   For each engine fault, altitude and power setting, bisect on fault severity
   for the smallest severity at which the engine model — run to thermal steady
   state — crosses a configured limit (CHT, coolant, oil temperature, gearbox
   oil, minimum oil pressure). A fault that never crosses one within its
   modelled range gets None: "no limit exceedance predicted", not a number.

2. MONTE CARLO (live, M = 200 draws per tick).
   Inputs are all estimates: the classifier's fault probabilities, its
   per-fault severity estimate, and the degradation rate fitted to that
   estimate's recent history. Each draw samples a fault, a current severity
   and a rate, and computes time-to-redline for each option (continue, derate,
   return to base) from the table. P(complete) = fraction of draws whose
   time-to-redline exceeds the time the option needs.

Assumptions, stated rather than hidden (also served in mission.assumed_fields):
  * the table is steady-state — the fault is assumed to progress slowly
    compared with the engine's thermal time constants;
  * degradation rate is assumed not to depend on power setting — nothing in
    the engine model couples them — so derating helps only through the
    larger redline margin at lower power, which the table does compute;
  * return-to-base transit time is a scenario input (no mission geometry).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TABLE_PATH = ROOT / "ml" / "weights" / "redline_table.json"

# M3 class -> (backend fault key, severity at the model's physical clamp).
# Severity is in faults.py's own units (fraction of the parameter lost/gained).
ENGINE_FAULTS: dict[str, tuple[str, float]] = {
    "injector_fouling":         ("injector", 0.60),
    "turbo_degradation":        ("turbo", 0.45),
    "cooling_fouling":          ("cooling", 0.50),
    "coolant_pump_degradation": ("coolantPump", 0.60),
    "bearing_wear":             ("bearing", 1.20),
    "ring_wear":                ("ringWear", 0.40),
    "oil_pump_wear":            ("oilLeak", 0.80),
    "fuel_filter_clog":         ("fuelFilter", 0.70),
    "injection_misfire":         ("misfire", 1.00),
    "detonation":               ("detonation", 1.00),
}
# Instrumentation faults change what a sensor reports, never the engine:
# no redline is ever approached because of them.
SENSOR_FAULTS = {"cht_sensor_drift", "egt_sensor_drift", "map_sensor_drift",
                 "lambda_sensor_drift"}

ALTITUDES_FT = [5000.0, 11000.0]
THROTTLES = [56.0, 72.0, 100.0]      # derate (78 % of cruise), cruise, full
SETTLE_S = 420


# ── 1. Redline table ─────────────────────────────────────────────────────────

def _exceeds(out: dict, lim: dict) -> str | None:
    if max(out["cht_C"]) > lim["cht_C"]:
        return "cht"
    if out.get("coolant_temp_C") is not None and out["coolant_temp_C"] > lim["coolant_C"]:
        return "coolant"
    if out["oil_temp_C"] > lim["oil_temp_C"]:
        return "oil_temp"
    if out.get("gearbox_oil_C") is not None and out["gearbox_oil_C"] > lim["gearbox_oil_C"]:
        return "gearbox_oil"
    if out["rpm"] > 2500 and out["oil_press_bar"] < lim["oil_pressure_bar"]["min_above_2500rpm"]:
        return "oil_pressure"
    return None


def _steady(cfg, key, sev, alt, thr, cyl=1):
    from twin.atmosphere import isa
    from twin.faults import apply_fault_config, fresh_sensor_biases
    from twin.mvem import MVEM
    from twin.session import nominal_params
    from twin.profiles import load_engine_profile
    cfg = load_engine_profile("engine_vrde_180.yaml")
    n = cfg["geometry"]["cylinders"]
    fc = {key: {"startT": 0.0, "rate": sev * 60.0, "cyl": cyl}} if sev > 0 else {}
    liquid = cfg["mvem"].get("cooling", {}).get("type") == "liquid"
    params, _, isa_k = apply_fault_config(60.0, fc, nominal_params(n),
                                          fresh_sensor_biases(n), 0.0, alt, liquid)
    plant = MVEM(cfg, seed=7)
    atm = isa(alt, isa_offset_K=isa_k)
    worst = None
    for k in range(SETTLE_S):
        plant.step(1.0, params, atm, thr, 61.2)
        if k >= SETTLE_S - 30:
            hit = _exceeds(plant.get_outputs(), cfg["limits"])
            worst = worst or hit
    return worst


def _bisect(args):
    cls, alt, thr = args
    sys.path.insert(0, str(ROOT / "backend"))
    from twin.profiles import load_engine_profile, DEFAULT_ENGINE
    cfg = load_engine_profile(DEFAULT_ENGINE)
    key, smax = ENGINE_FAULTS[cls]
    if _steady(cfg, key, 0.0, alt, thr):
        return cls, alt, thr, 0.0, "healthy_engine_exceeds"
    hit = _steady(cfg, key, smax, alt, thr)
    if not hit:
        return cls, alt, thr, None, None
    lo, hi = 0.0, smax
    for _ in range(8):
        mid = 0.5 * (lo + hi)
        if _steady(cfg, key, mid, alt, thr):
            hi = mid
        else:
            lo = mid
    return cls, alt, thr, hi / smax, hit


def build_table(workers: int = 7) -> dict:
    from multiprocessing import Pool
    jobs = [(c, a, t) for c in ENGINE_FAULTS for a in ALTITUDES_FT for t in THROTTLES]
    with Pool(workers) as pool:
        rows = pool.map(_bisect, jobs)
    table: dict = {"altitudes_ft": ALTITUDES_FT, "throttles": THROTTLES,
                   "severity_units": "fraction of the fault's modelled range",
                   "settle_s": SETTLE_S, "faults": {}}
    for cls, alt, thr, s, why in rows:
        table["faults"].setdefault(cls, {})[f"{int(alt)}@{int(thr)}"] = {"s_limit": s, "limit": why}
    return table


# ── 2. Live Monte Carlo ──────────────────────────────────────────────────────

class MissionMonteCarlo:
    def __init__(self, classes: list[str], table: dict | None = None,
                 m: int = 200, seed: int = 0):
        self.classes = classes
        self.table = table or json.loads(TABLE_PATH.read_text())
        self.m = m
        self.rng = np.random.default_rng(seed)

    def s_limit(self, cls: str, alt: float, thr: float) -> float | None:
        """Severity (fraction of range) at which a redline is crossed, taken
        from the nearest tabulated altitude and linearly interpolated in
        throttle between its bracketing entries."""
        rows = self.table["faults"].get(cls)
        if rows is None:
            return None
        a = min(self.table["altitudes_ft"], key=lambda x: abs(x - alt))
        ths = self.table["throttles"]
        vals = [rows[f"{int(a)}@{int(t)}"]["s_limit"] for t in ths]
        if all(v is None for v in vals):
            return None
        big = 10.0   # "no exceedance inside the modelled range"
        v = [big if x is None else x for x in vals]
        out = float(np.interp(thr, ths, v))
        return None if out >= big else out

    def run(self, probs: np.ndarray, sev_mean: np.ndarray, sev_std: np.ndarray,
            rate_mean: np.ndarray, rate_std: np.ndarray, alt: float,
            thr_now: float, needs_s: dict[str, float]) -> dict:
        """
        probs, sev_*, rate_*: per class (severity as fraction of range, rate
        in fraction per second). needs_s: seconds each option must survive,
        keys 'continue', 'derate', 'rtb'. Returns P(complete) per option and
        the time-to-redline distribution under 'continue'.
        """
        thr = {"continue": thr_now, "derate": 0.78 * thr_now, "rtb": 0.78 * thr_now}
        p = np.clip(probs, 0, None)
        p = p / p.sum() if p.sum() > 0 else np.full_like(p, 1.0 / len(p))
        draws = self.rng.choice(len(self.classes), size=self.m, p=p)
        ok = {k: 0 for k in needs_s}
        ttl_continue = []
        for c in draws:
            cls = self.classes[c]
            s = max(0.0, self.rng.normal(sev_mean[c], sev_std[c]))
            r = self.rng.normal(rate_mean[c], rate_std[c])
            for opt, need in needs_s.items():
                lim = self.s_limit(cls, alt, thr[opt])
                if cls not in ENGINE_FAULTS or lim is None:
                    ttl = np.inf
                elif s >= lim:
                    ttl = 0.0
                elif r <= 0:
                    ttl = np.inf
                else:
                    ttl = (lim - s) / r
                ok[opt] += ttl > need
                if opt == "continue":
                    ttl_continue.append(ttl)
        ttl = np.array(ttl_continue)
        return {"p": {k: v / self.m for k, v in ok.items()}, "ttl_continue_s": ttl}


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "backend"))
    t = build_table()
    TABLE_PATH.write_text(json.dumps(t, indent=1))
    for cls, rows in t["faults"].items():
        print(cls, {k: (None if v["s_limit"] is None else round(v["s_limit"], 3), v["limit"])
                    for k, v in rows.items()})
    print(f"wrote {TABLE_PATH}")
