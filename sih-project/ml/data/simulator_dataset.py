"""
BULK DATASET GENERATOR — real MVEM flights, not synthetic residual noise.

Why this exists
---------------
Every weight in ml/weights/ was fitted to ml/data/synthetic.py: AR(1) noise
plus jittered columns of the incidence matrix. No model has ever seen the
engine model's output. This script produces the dataset that fixes that.

It calls the REAL on-disk physics — twin/mvem.py, twin/faults.py,
twin/measurement.py, twin/damage.py, parity/residuals.py — through their own
APIs. Nothing here reimplements physics; if it did, the dataset would describe
a second, subtly different engine that nobody validated.

What one row is
---------------
One second of one flight: the operating point, every measured channel, the
11-element parity residual in BOTH raw and sigma-normalised form, and the
ground-truth damage/RUL labels from the damage integrator.

What one flight is
------------------
A profile (take-off -> climb -> cruise -> descent) at a randomly drawn cruise
altitude, ISA offset, throttle schedule and airspeed schedule, with at most one
fault that begins PART-WAY THROUGH — so the rows before onset are genuinely
healthy and detection delay and false-alarm rate become measurable. A quarter
of flights are healthy end to end.

Splitting
---------
BY FLIGHT, never by row or window. Windows drawn from one flight share probe
offsets, a fault ramp and an operating point; splitting by row leaks all three
across the boundary and inflates every score.

Run
---
    cd sih-project
    python -m ml.data.simulator_dataset --flights 400 --seconds 600 --workers 6

Outputs (default `data/sim_v1/`):
    train.csv / val.csv / test.csv    one row per simulated second
    flights.csv                       one row per flight (labels, split, seed)
    schema.json                       column -> dtype, unit, description
    MANIFEST.md                       provenance, caveats, how to regenerate
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Paths. Resolved from __file__ ONLY — never from the current directory, which
# has bitten this project repeatedly.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_PROJECT = _HERE.parent.parent.parent              # sih-project/
_BACKEND = _PROJECT / "backend"
sys.path.insert(0, str(_BACKEND))                  # twin/, parity/
sys.path.insert(0, str(_PROJECT))                  # ml/

from twin.profiles import load_engine_profile      # noqa: E402
from twin.atmosphere import isa                    # noqa: E402
from twin.mvem import MVEM                         # noqa: E402
from twin.measurement import MeasurementModel      # noqa: E402
from twin.faults import apply_fault_config, fresh_sensor_biases   # noqa: E402
from twin.damage import DamageIntegrator           # noqa: E402
from parity.residuals import compute_residuals     # noqa: E402

PROFILE = "engine_vrde_180.yaml"
SIGMA_PATH = _BACKEND / "config" / "sigma_vector.json"

# Physics files whose content defines this dataset. Hashed into the manifest so
# a future reader can tell whether the code moved under the data.
PROVENANCE_FILES = [
    _BACKEND / "twin" / "mvem.py",
    _BACKEND / "twin" / "faults.py",
    _BACKEND / "twin" / "measurement.py",
    _BACKEND / "twin" / "damage.py",
    _BACKEND / "twin" / "airpath.py",
    _BACKEND / "parity" / "residuals.py",
    _PROJECT / "config" / PROFILE,
    _PROJECT / "config" / "sensors.yaml",
]

N_CYL = 4
N_RHO = 13

# ---------------------------------------------------------------------------
# Fault library
# ---------------------------------------------------------------------------
# Keys are twin/faults.py's own names. `rate` ranges are per MINUTE and span
# from "barely detectable over a flight" to "obvious", so severity is a
# continuum in the data rather than one demo value. Units differ per fault and
# are stated, because a rate of 0.05 means something different for a fraction
# than for degC/min.
#
# `class_name` maps to ml/incidence.py's 13 fault classes where one exists.
# coolantPump has NO column in that matrix (the fault is newer than the
# matrix), so it carries its own name and is flagged in the manifest. Extending
# the matrix means editing ml/incidence.py, frontend/src/analysis/incidence.ts
# and docs/spec/residual-spec.md together, and re-deriving the novelty rank —
# a deliberate decision, not something to slip into a dataset script.
@dataclass(frozen=True)
class FaultSpecDef:
    key: str                 # twin/faults.py fault name
    class_name: str          # ml/incidence.py class, or a new name
    rate_lo: float
    rate_hi: float
    unit: str
    per_cylinder: bool
    is_sensor: bool


FAULT_LIBRARY: list[FaultSpecDef] = [
    FaultSpecDef("injector",     "injector_fouling",      0.010, 0.120, "fraction of C_d per min",        True,  False),
    FaultSpecDef("turbo",        "turbo_degradation",     0.008, 0.080, "fraction of eta_c per min",      False, False),
    FaultSpecDef("cooling",      "cooling_fouling",       0.008, 0.080, "fraction of radiator eff per min", False, False),
    FaultSpecDef("coolantPump",  "coolant_pump_degradation", 0.008, 0.080, "fraction of coolant flow per min", False, False),
    FaultSpecDef("bearing",      "bearing_wear",          0.010, 0.100, "friction fraction gained per min", False, False),
    FaultSpecDef("ringWear",     "ring_wear",             0.008, 0.080, "fraction of eta_v per min",      False, False),
    FaultSpecDef("oilLeak",      "oil_pump_wear",         0.010, 0.090, "fraction of oil pump per min",   False, False),
    FaultSpecDef("fuelFilter",   "fuel_filter_clog",      0.008, 0.070, "fraction of rail per min",       False, False),
    FaultSpecDef("misfire",      "ignition_misfire",      0.050, 0.500, "skip probability per min",       True,  False),
    FaultSpecDef("detonation",   "detonation",            0.050, 0.600, "severity per min",               True,  False),
    FaultSpecDef("chtSensor",    "cht_sensor_drift",      2.000, 30.000, "degC per min",                  True,  True),
    FaultSpecDef("egtSensor",    "egt_sensor_drift",      2.000, 40.000, "degC per min",                  True,  True),
    FaultSpecDef("mapSensor",    "map_sensor_drift",      2.000, 40.000, "hPa per min",                   False, True),
    FaultSpecDef("lambdaSensor", "lambda_sensor_drift",   0.010, 0.150, "lambda per min",                 False, True),
]
FAULT_BY_KEY = {f.key: f for f in FAULT_LIBRARY}

# Class index space used by the dataset: 0 = healthy, then the library order.
CLASS_NAMES = ["healthy"] + [f.class_name for f in FAULT_LIBRARY]
CLASS_INDEX = {n: i for i, n in enumerate(CLASS_NAMES)}


def nominal_params() -> dict:
    """
    All ELEVEN parameter keys, every time.

    mvem.py reads these with params.get(key, 1.0), so a missing key is not an
    error — it silently becomes nominal. That is exactly how a dataset ends up
    quietly describing a different engine than the one being flown, so the full
    dict is built in one place and never abbreviated.
    """
    return {
        "cd_inj":          [1.0] * N_CYL,
        "eta_v_scale":     1.0,
        "eta_c_scale":     1.0,
        "hA_scale":        1.0,
        "f_fric_scale":    1.0,
        "oil_pump_scale":  1.0,
        "fuel_rail_scale": 1.0,
        "misfire_prob":    [0.0] * N_CYL,
        "detonation_sev":  [0.0] * N_CYL,
        "rad_eff_scale":   1.0,
        "cool_pump_scale": 1.0,
    }


# ---------------------------------------------------------------------------
# Flight plan
# ---------------------------------------------------------------------------

@dataclass
class FlightPlan:
    flight_id: str
    seed: int
    duration_s: int
    cruise_alt_ft: float
    isa_offset_K: float
    cruise_throttle_pct: float
    cruise_tas_mps: float
    climb_s: int
    descent_s: int
    fault_key: str | None
    fault_rate: float
    fault_cyl: int
    onset_s: int
    split: str


def plan_flight(idx: int, rng: np.random.Generator, duration_s: int,
                fault_key: str | None) -> FlightPlan:
    """Draw one flight's operating envelope and fault assignment."""
    # Cruise altitude: the demo band plus the published high-altitude trial
    # condition and the engine's own ceiling region.
    cruise_alt = float(rng.choice([6000, 8000, 11000, 14000, 17664, 18000, 22000, 25000]))
    cruise_alt += float(rng.normal(0.0, 300.0))
    cruise_alt = float(np.clip(cruise_alt, 3000.0, 28000.0))

    # ISA offset: cold day through hot-and-high. 17,664 ft is DRDO's Leh /
    # Chang La trial altitude, so bias those flights hot, as that trial was.
    isa_off = float(rng.uniform(-10.0, 25.0))
    if abs(cruise_alt - 17664.0) < 400.0:
        isa_off = float(rng.uniform(10.0, 25.0))

    climb_s = int(rng.integers(120, 240))
    descent_s = int(rng.integers(60, 150))
    cruise_thr = float(rng.uniform(62.0, 82.0))
    cruise_tas = float(rng.uniform(55.0, 68.0))

    if fault_key is None:
        rate, cyl, onset = 0.0, -1, -1
    else:
        spec = FAULT_BY_KEY[fault_key]
        rate = float(rng.uniform(spec.rate_lo, spec.rate_hi))
        cyl = int(rng.integers(0, N_CYL)) if spec.per_cylinder else -1
        # Onset part-way through: the rows before it are honestly healthy, which
        # is what makes detection delay and false-alarm rate measurable at all.
        onset = int(rng.uniform(0.15, 0.60) * duration_s)

    return FlightPlan(
        flight_id=f"F{idx:05d}",
        seed=int(rng.integers(1, 2**31 - 1)),
        duration_s=duration_s,
        cruise_alt_ft=cruise_alt,
        isa_offset_K=isa_off,
        cruise_throttle_pct=cruise_thr,
        cruise_tas_mps=cruise_tas,
        climb_s=climb_s,
        descent_s=descent_s,
        fault_key=fault_key,
        fault_rate=rate,
        fault_cyl=cyl,
        onset_s=onset,
        split="",                    # assigned later, stratified
    )


def flight_point(plan: FlightPlan, t: float) -> tuple[float, float, float, str]:
    """
    (altitude_ft, throttle_pct, tas_mps, phase) at mission time t.

    Take-off and climb are flown at high power and low airspeed; cruise settles
    to the drawn throttle and TAS; descent pulls power back. The airspeed goes
    through MVEM.step's tas argument, so the propeller governor and the ram-air
    radiator both see a real schedule rather than one constant.
    """
    takeoff_s = 45.0
    climb_end = takeoff_s + plan.climb_s
    descent_start = plan.duration_s - plan.descent_s

    if t < takeoff_s:
        f = t / takeoff_s
        return (200.0 * f, 100.0, 40.0 + 8.0 * f, "takeoff")
    if t < climb_end:
        f = (t - takeoff_s) / max(plan.climb_s, 1)
        alt = 200.0 + (plan.cruise_alt_ft - 200.0) * f
        return (alt, 92.0, 48.0 + (plan.cruise_tas_mps - 48.0) * f, "climb")
    if t < descent_start:
        return (plan.cruise_alt_ft, plan.cruise_throttle_pct, plan.cruise_tas_mps, "cruise")
    f = (t - descent_start) / max(plan.descent_s, 1)
    alt = plan.cruise_alt_ft - (plan.cruise_alt_ft - 0.35 * plan.cruise_alt_ft) * f
    return (alt, 38.0, plan.cruise_tas_mps + 4.0 * f, "descent")


# ---------------------------------------------------------------------------
# Column schema
# ---------------------------------------------------------------------------

MEASURED_SCALARS = [
    ("rpm", "rpm", "crank speed, 60-2 VR pickup (tooth-quantised)"),
    ("map_hPa", "hPa", "manifold absolute pressure"),
    ("iat_K", "K", "intake air temperature, POST-intercooler"),
    ("oil_press_bar", "bar", "oil pressure"),
    ("oil_temp_C", "degC", "oil temperature"),
    ("fuel_flow_kgps", "kg/s", "fuel mass flow (turbine pulse counter)"),
    ("lambda_val", "-", "wideband UEGO lambda; empty if sensor invalid"),
    ("turbo_rpm", "rpm", "turbocharger shaft speed"),
    ("p_amb_hPa", "hPa", "ambient / compressor inlet pressure"),
    ("oat_K", "K", "outside air temperature"),
    ("coolant_temp_C", "degC", "coolant temperature (production cooling channel)"),
    ("comp_out_T_K", "K", "compressor delivery temperature, PRE-intercooler"),
    ("comp_out_p_hPa", "hPa", "compressor delivery pressure, PRE-intercooler"),
    ("tas_mps", "m/s", "true airspeed (air data)"),
    ("prop_rpm", "rpm", "propeller speed (separate pickup, not crank/gear)"),
    ("blade_angle_deg", "deg", "propeller blade angle (pitch feedback)"),
    ("gearbox_oil_C", "degC", "gearbox oil temperature"),
    ("air_mass_flow", "kg/s", "ESTIMATE (speed-density), not a transducer"),
    ("brake_power_kW", "kW", "ESTIMATE, no torque sensor exists"),
    ("fuel_cmd_per_cyl", "kg/s", "commanded fuel per cylinder"),
    ("ripple", "-", "0.5-order crank ripple magnitude"),
]

LABEL_COLUMNS = [
    ("flight_id", "-", "flight identity; SPLIT IS BY THIS"),
    ("split", "-", "train | val | test"),
    ("t_s", "s", "seconds since logging start (post warm-up)"),
    ("phase", "-", "takeoff | climb | cruise | descent"),
    ("fault_key", "-", "twin/faults.py fault name, or 'healthy'"),
    ("fault_class_name", "-", "ml/incidence.py class name, or 'healthy'"),
    ("fault_class_idx", "int", "0 = healthy, then FAULT_LIBRARY order"),
    ("fault_active", "int", "1 once t >= onset_s, else 0"),
    ("label_class_idx", "int", "fault_class_idx if fault_active else 0 — TRAIN ON THIS"),
    ("fault_cyl", "int", "0-3 for per-cylinder faults, -1 otherwise"),
    ("severity_rate", "float", "fault rate per minute, units in schema.json"),
    ("onset_s", "int", "fault start time; -1 on healthy flights"),
    ("time_since_onset_s", "float", "negative before onset, NaN on healthy"),
    ("is_sensor_fault", "int", "1 for instrumentation faults"),
    ("D", "-", "ground-truth damage state in [0,1] (twin/damage.py)"),
    ("dD_dt_per_s", "1/s", "ground-truth damage rate"),
    ("RUL_h", "h", "ground-truth remaining useful life; empty when no damage"),
    ("altitude_ft", "ft", "commanded altitude"),
    ("isa_offset_K", "K", "ISA temperature offset for the flight"),
    ("throttle_pct", "%", "power lever position"),
    ("seed", "int", "plant RNG seed for this flight"),
]


def build_columns() -> list[str]:
    cols = [c[0] for c in LABEL_COLUMNS]
    cols += [c[0] for c in MEASURED_SCALARS]
    cols += [f"cht_C_{i+1}" for i in range(N_CYL)]
    cols += [f"egt_C_{i+1}" for i in range(N_CYL)]
    cols += [f"rho{i+1}" for i in range(N_RHO)]
    cols += [f"rho{i+1}_n" for i in range(N_RHO)]
    return cols


COLUMNS = build_columns()


# ---------------------------------------------------------------------------
# One flight
# ---------------------------------------------------------------------------

def simulate_flight(args: tuple) -> dict:
    """
    Simulate one flight and write its own CSV shard. Runs in a worker process.

    Returns a summary dict; the rows never cross the process boundary, so peak
    memory stays at one flight's worth of rows no matter how large the dataset
    gets (this machine has 8 GB).
    """
    plan_d, out_dir, warmup_s = args
    plan = FlightPlan(**plan_d)
    try:
        cfg = load_engine_profile(PROFILE)
        sigma = json.loads(SIGMA_PATH.read_text())

        plant = MVEM(cfg, seed=plan.seed)
        twin = MVEM(cfg, seed=999)
        m_plant = MeasurementModel(seed=plan.seed, n_cyl=N_CYL)
        m_twin = MeasurementModel(seed=999, is_twin=True, n_cyl=N_CYL)
        damage = DamageIntegrator()
        nominal = nominal_params()

        spec = FAULT_BY_KEY[plan.fault_key] if plan.fault_key else None
        fault_cfg_full = (
            {plan.fault_key: {"startT": 0.0, "cyl": max(plan.fault_cyl, 0),
                              "rate": plan.fault_rate}}
            if plan.fault_key else {}
        )

        # WARM-UP, discarded. The coolant loop's time constant is ~45 s and the
        # oil bulk is slower still; logging from t=0 would fill the dataset with
        # a start transient that no in-flight engine ever shows.
        alt0, thr0, tas0, _ = flight_point(plan, 0.0)
        atm0 = isa(alt0, isa_offset_K=plan.isa_offset_K)
        for _ in range(warmup_s):
            plant.step(1.0, nominal, atm0, thr0, tas0)
            twin.step(1.0, nominal, atm0, thr0, tas0)

        shard = Path(out_dir) / "_shards" / f"{plan.flight_id}.csv"
        shard.parent.mkdir(parents=True, exist_ok=True)

        n_rows = 0
        with shard.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="raise")
            writer.writeheader()

            for step_i in range(plan.duration_s):
                t = float(step_i)
                alt, thr, tas, phase = flight_point(plan, t)
                atm = isa(alt, isa_offset_K=plan.isa_offset_K)

                active = bool(plan.fault_key) and t >= plan.onset_s
                if active:
                    # faults.py ramps from startT, so shift the clock rather
                    # than the rate: severity = rate * (t - onset)/60.
                    fcfg = {plan.fault_key: {"startT": float(plan.onset_s),
                                             "cyl": max(plan.fault_cyl, 0),
                                             "rate": plan.fault_rate}}
                else:
                    fcfg = {}

                params, biases, _ = apply_fault_config(
                    t, fcfg, nominal, fresh_sensor_biases(N_CYL),
                    0.0, alt, liquid_cooled=True,
                )

                plant.step(1.0, params, atm, thr, tas)
                twin.step(1.0, nominal, atm, thr, tas)

                out_p = plant.get_outputs()
                out_t = twin.get_outputs()
                meas = m_plant.measure(out_p, sensor_biases=biases, add_noise=True)
                pred = m_twin.measure(out_t, add_noise=False)
                rho = compute_residuals(meas, pred, cfg, sigma_vec=None)

                # Damage only accumulates once the fault is actually running.
                # Passing the config before onset would age the engine for a
                # fault that has not started, corrupting every RUL label.
                t_fric = 6.2 * params.get("f_fric_scale", 1.0) * plant.w / 100.0
                cht_mean_K = float(np.mean(out_p["cht_C"])) + 273.15
                damage.step(dt=1.0, fault_config=fault_cfg_full if active else {},
                            T_fric=t_fric, omega=plant.w, T_cht_K=cht_mean_K,
                            oil_temp_K=plant.T_oil, t=t - plan.onset_s if active else 0.0)
                dmg = damage.get_state()

                row = {
                    "flight_id": plan.flight_id,
                    "split": plan.split,
                    "t_s": t,
                    "phase": phase,
                    "fault_key": plan.fault_key or "healthy",
                    "fault_class_name": spec.class_name if spec else "healthy",
                    "fault_class_idx": CLASS_INDEX[spec.class_name] if spec else 0,
                    "fault_active": int(active),
                    "label_class_idx": (CLASS_INDEX[spec.class_name] if (spec and active) else 0),
                    "fault_cyl": plan.fault_cyl,
                    "severity_rate": plan.fault_rate,
                    "onset_s": plan.onset_s,
                    "time_since_onset_s": (t - plan.onset_s) if plan.fault_key else "",
                    "is_sensor_fault": int(bool(spec and spec.is_sensor)),
                    "D": dmg["damage_state"],
                    "dD_dt_per_s": dmg["damage_rate_per_hr"] / 3600.0,
                    "RUL_h": "" if dmg["rul_h"] is None else dmg["rul_h"],
                    "altitude_ft": alt,
                    "isa_offset_K": plan.isa_offset_K,
                    "throttle_pct": thr,
                    "seed": plan.seed,
                }
                for name, _u, _d in MEASURED_SCALARS:
                    v = meas.get(name)
                    row[name] = "" if v is None else v
                for i in range(N_CYL):
                    row[f"cht_C_{i+1}"] = meas["cht_C"][i]
                    row[f"egt_C_{i+1}"] = meas["egt_C"][i]
                for i in range(N_RHO):
                    r = rho[i]
                    row[f"rho{i+1}"] = "" if r is None else r
                    # Sigma normalisation, identical to residuals.py's rule
                    # (divide by sigma with a 0.05 floor) so the normalised
                    # columns match exactly what ml/inference.py is fed live.
                    row[f"rho{i+1}_n"] = "" if r is None else r / max(float(sigma[i]), 0.05)
                writer.writerow(row)
                n_rows += 1

        return {"flight_id": plan.flight_id, "split": plan.split, "rows": n_rows,
                "fault_key": plan.fault_key or "healthy", "ok": True, "error": ""}

    except Exception as exc:                        # noqa: BLE001
        import traceback
        return {"flight_id": plan.flight_id, "split": plan.split, "rows": 0,
                "fault_key": plan.fault_key or "healthy", "ok": False,
                "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def assign_splits(plans: list[FlightPlan], rng: np.random.Generator,
                  frac=(0.70, 0.15, 0.15)) -> None:
    """
    Stratify by fault key so every split sees every fault, then split BY FLIGHT.
    """
    by_key: dict[str, list[FlightPlan]] = {}
    for p in plans:
        by_key.setdefault(p.fault_key or "healthy", []).append(p)
    for _key, group in by_key.items():
        idx = rng.permutation(len(group))
        n = len(group)
        n_tr = max(1, int(round(frac[0] * n)))
        n_va = max(1, int(round(frac[1] * n))) if n >= 3 else 0
        for rank, j in enumerate(idx):
            if rank < n_tr:
                group[j].split = "train"
            elif rank < n_tr + n_va:
                group[j].split = "val"
            else:
                group[j].split = "test"


def merge_shards(out_dir: Path, splits=("train", "val", "test")) -> dict[str, int]:
    """Stream shards into one CSV per split. Never loads a whole split in RAM."""
    shard_dir = out_dir / "_shards"
    counts = {s: 0 for s in splits}
    handles = {}
    written_header = {s: False for s in splits}
    try:
        for s in splits:
            handles[s] = (out_dir / f"{s}.csv").open("w", newline="", encoding="utf-8")
        for shard in sorted(shard_dir.glob("*.csv")):
            with shard.open("r", encoding="utf-8") as fh:
                header = fh.readline()
                first = fh.readline()
                if not first:
                    continue
                split = first.split(",")[COLUMNS.index("split")]
                h = handles[split]
                if not written_header[split]:
                    h.write(header)
                    written_header[split] = True
                h.write(first)
                counts[split] += 1
                for line in fh:
                    h.write(line)
                    counts[split] += 1
    finally:
        for h in handles.values():
            h.close()
    return counts


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser(description="PRAMANA bulk flight dataset generator")
    ap.add_argument("--flights", type=int, default=400)
    ap.add_argument("--seconds", type=int, default=600, help="logged seconds per flight")
    ap.add_argument("--warmup", type=int, default=90, help="discarded settle seconds")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--healthy-frac", type=float, default=0.25)
    ap.add_argument("--out", type=str, default=str(_PROJECT / "data" / "sim_v1"))
    ap.add_argument("--seed", type=int, default=20260916)
    args = ap.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_shards").mkdir(exist_ok=True)

    print(f"interpreter : {sys.executable}")
    print(f"project     : {_PROJECT}")
    print(f"output      : {out_dir}")
    print(f"workers     : {args.workers}  (cpu_count={os.cpu_count()})")

    rng = np.random.default_rng(args.seed)

    # Fault assignment: a quarter healthy, the rest spread evenly over the 14.
    n_healthy = int(round(args.healthy_frac * args.flights))
    n_faulty = args.flights - n_healthy
    keys: list[str | None] = [None] * n_healthy
    for i in range(n_faulty):
        keys.append(FAULT_LIBRARY[i % len(FAULT_LIBRARY)].key)
    rng.shuffle(keys)

    plans = [plan_flight(i, rng, args.seconds, k) for i, k in enumerate(keys)]
    assign_splits(plans, rng)

    t0 = time.perf_counter()
    payload = [(asdict(p), str(out_dir), args.warmup) for p in plans]
    results = []
    with Pool(processes=args.workers) as pool:
        for i, res in enumerate(pool.imap_unordered(simulate_flight, payload), 1):
            results.append(res)
            if not res["ok"]:
                print(f"  !! {res['flight_id']} FAILED: {res['error'].splitlines()[0]}")
            if i % 20 == 0 or i == len(payload):
                el = time.perf_counter() - t0
                print(f"  {i}/{len(payload)} flights  {el:6.1f}s elapsed  "
                      f"({el / max(i, 1):.2f}s/flight)")

    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"\n{len(failed)} flight(s) failed. First traceback:\n{failed[0]['error']}")
        sys.exit(1)

    counts = merge_shards(out_dir)
    print(f"\nrows per split: {counts}")

    # flights.csv — one row per flight, the split key and every label.
    with (out_dir / "flights.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["flight_id", "split", "fault_key", "fault_class_name", "fault_class_idx",
                    "severity_rate", "fault_cyl", "onset_s", "duration_s", "cruise_alt_ft",
                    "isa_offset_K", "cruise_throttle_pct", "cruise_tas_mps", "seed", "rows"])
        rows_by_id = {r["flight_id"]: r["rows"] for r in results}
        for p in plans:
            spec = FAULT_BY_KEY[p.fault_key] if p.fault_key else None
            w.writerow([p.flight_id, p.split, p.fault_key or "healthy",
                        spec.class_name if spec else "healthy",
                        CLASS_INDEX[spec.class_name] if spec else 0,
                        f"{p.fault_rate:.5f}", p.fault_cyl, p.onset_s, p.duration_s,
                        f"{p.cruise_alt_ft:.1f}", f"{p.isa_offset_K:.2f}",
                        f"{p.cruise_throttle_pct:.2f}", f"{p.cruise_tas_mps:.2f}",
                        p.seed, rows_by_id.get(p.flight_id, 0)])

    # schema.json — machine-readable, for the training notebook.
    schema = {
        "columns": [],
        "n_columns": len(COLUMNS),
        "class_names": CLASS_NAMES,
        "residual_normalisation": {
            "rule": "rho{i}_n = rho{i} / max(sigma[i], 0.05)",
            "sigma_vector": json.loads(SIGMA_PATH.read_text()),
            "source": "backend/config/sigma_vector.json",
            "note": "ml/inference.py consumes sigma-normalised residuals; train on rho*_n.",
        },
    }
    described = {c[0]: (c[1], c[2]) for c in LABEL_COLUMNS + MEASURED_SCALARS}
    for c in COLUMNS:
        if c in described:
            unit, desc = described[c]
        elif c.startswith("cht_C_"):
            unit, desc = "degC", f"cylinder {c[-1]} head temperature (flight-test instrumentation)"
        elif c.startswith("egt_C_"):
            unit, desc = "degC", f"cylinder {c[-1]} exhaust gas temperature (flight-test instrumentation)"
        elif c.endswith("_n"):
            unit, desc = "sigma", f"{c[:-2]} normalised by healthy sigma — TRAIN ON THESE"
        else:
            unit, desc = "-", ("parity residual, raw units. rho3 is always empty "
                               "(no Path 4 on an unthrottled engine); rho12/rho13 "
                               "are the thermal closures, in degC")
        schema["columns"].append({"name": c, "unit": unit, "description": desc})
    (out_dir / "schema.json").write_text(json.dumps(schema, indent=2))

    elapsed = time.perf_counter() - t0
    print(f"\nGenerated {sum(counts.values())} rows from {len(plans)} flights "
          f"in {elapsed/60:.1f} min")
    print(f"Next: python -m ml.data.verify_dataset --dir {out_dir}")

    # Provenance for the manifest.
    prov = {str(p.relative_to(_PROJECT)): sha256(p) for p in PROVENANCE_FILES}
    (out_dir / "_provenance.json").write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "interpreter": sys.executable,
        "platform": platform.platform(),
        "args": vars(args),
        "sha256_16": prov,
        "rows": counts,
        "flights": len(plans),
    }, indent=2))


if __name__ == "__main__":
    main()
