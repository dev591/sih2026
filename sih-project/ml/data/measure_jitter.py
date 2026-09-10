"""
Measure how far a REAL fault's residual signature sits from the textbook
incidence column — instead of assuming it.

Why this exists
---------------
`synthetic.py` perturbs each training sample off its nominal incidence column
by `direction_jitter`. That perturbation is necessary: without it every sample
lies exactly along a column of the same matrix the cosine matcher scores
against, so "the network and the matrix agree" is an identity, not evidence.

But the VALUE was invented, and it turns out to set the headline accuracy
almost single-handedly. Measured oracle ceiling (the best ANY model can do):

    jitter 0.00 -> 0.871      jitter 0.20 -> 0.799
    jitter 0.10 -> 0.867      jitter 0.30 -> 0.644   <- the guess
                              jitter 0.40 -> 0.497

A number worth 23 accuracy points cannot stay a guess. How far a fouled
injector's actual residual vector lands from the textbook column is a physical
question, and BE-1's MVEM can answer it: perturb the parameter, integrate the
plant against a nominal twin, push the result through the real parity layer,
and measure the angle.

Run:
    python3 -m ml.data.measure_jitter
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent.parent      # sih-project/
sys.path.insert(0, str(_ROOT / "backend"))                 # twin/, parity/

from twin.profiles import load_engine_profile               # noqa: E402
from twin.atmosphere import isa                             # noqa: E402
from twin.mvem import MVEM                                  # noqa: E402
from twin.measurement import MeasurementModel               # noqa: E402
from parity.residuals import compute_residuals              # noqa: E402

from ml.incidence import INCIDENCE, N_RESIDUALS             # noqa: E402


# Fault -> how it enters the MVEM. Only modes BE-1's model actually exposes can
# be measured; the rest are reported as null rather than invented.
#   'param'  : (key, direction) on the plant's parameter dict
#   'sensor' : (channel, direction) applied in the measurement layer only
FAULT_HOOKS: dict[str, dict] = {
    "injector_fouling":    {"kind": "param",  "key": "cd_inj",       "sign": -1, "per_cyl": True},
    "turbo_degradation":   {"kind": "param",  "key": "eta_c_scale",  "sign": -1},
    "cooling_fouling":     {"kind": "param",  "key": "hA_scale",     "sign": -1},
    "bearing_wear":        {"kind": "param",  "key": "f_fric_scale", "sign": +1},
    "ring_wear":           {"kind": "param",  "key": "eta_v_scale",  "sign": -1},
    "cht_sensor_drift":    {"kind": "sensor", "key": "cht_C",        "sign": +1, "per_cyl": True},
    "egt_sensor_drift":    {"kind": "sensor", "key": "egt_C",        "sign": +1, "per_cyl": True},
    # No hook in the current MVEM — cannot be measured, must not be guessed:
    #   oil_pump_wear, detonation, fuel_filter_clog, ignition_misfire,
    #   map_sensor_drift, lambda_sensor_drift
}

ALT_FT, THROTTLE, N_SETTLE = 18000.0, 72.0, 12


def _sigma_vector(n_cyl: int) -> np.ndarray:
    p = _ROOT / "backend" / "config" / "sigma_vector.json"
    try:
        return np.array(json.loads(p.read_text()), dtype=float)
    except FileNotFoundError:
        return np.ones(N_RESIDUALS)


def _residual_for(cfg, hook: dict, severity: float, cyl: int, sigma: np.ndarray):
    """Run plant (faulted) against twin (nominal) and return the sigma-normalised rho."""
    n_cyl = cfg["geometry"]["cylinders"]
    atm = isa(ALT_FT, isa_offset_K=0.0)

    nominal = {
        "cd_inj": [1.0] * n_cyl, "eta_v_scale": 1.0, "eta_c_scale": 1.0,
        "hA_scale": 1.0, "f_fric_scale": 1.0,
    }
    plant_params = {k: (list(v) if isinstance(v, list) else v) for k, v in nominal.items()}
    biases = {"cht_C": [0.0] * n_cyl, "egt_C": [0.0] * n_cyl,
              "map_hPa": 0.0, "lambda": 0.0}

    if hook["kind"] == "param":
        key, sign = hook["key"], hook["sign"]
        if hook.get("per_cyl"):
            plant_params[key][cyl] = 1.0 + sign * severity
        else:
            plant_params[key] = 1.0 + sign * severity
    else:
        # A SENSOR fault touches the measurement only — never the engine state.
        # That asymmetry is the whole sensor-vs-engine discriminator.
        biases[hook["key"]][cyl] = hook["sign"] * severity * 100.0

    plant, twin = MVEM(cfg), MVEM(cfg)
    mp, mt = MeasurementModel(seed=7), MeasurementModel(seed=999)
    for _ in range(N_SETTLE):
        plant.step(1.0, plant_params, atm, THROTTLE)
        twin.step(1.0, nominal, atm, THROTTLE)

    measured  = mp.measure(plant.get_outputs(), sensor_biases=biases, add_noise=False)
    predicted = mt.measure(twin.get_outputs(), add_noise=False)
    rho = compute_residuals(measured, predicted, cfg, sigma_vec=list(sigma))
    return np.array([0.0 if v is None else v for v in rho], dtype=float)


def _column(fault: str, cyl: int, n_cyl: int) -> np.ndarray:
    """Nominal incidence column, expanded so rho6-9 carries only this cylinder."""
    base = np.array(INCIDENCE[fault], dtype=float).copy()
    per_cyl_slice = slice(5, 5 + n_cyl)
    if np.any(base[per_cyl_slice] != 0):
        v = base[per_cyl_slice].copy()
        base[per_cyl_slice] = 0.0
        base[5 + cyl] = v[cyl] if v[cyl] != 0 else v.max()
    return base


def _jitter(measured: np.ndarray, column: np.ndarray) -> float | None:
    """
    Equivalent gaussian jitter: synthetic.py builds a direction as
    normalise(col + N(0, j^2 I)), so the expected angle between the jittered
    and nominal directions maps back to j. We invert that numerically rather
    than eyeballing an angle.
    """
    a, b = np.linalg.norm(measured), np.linalg.norm(column)
    if a < 1e-9 or b < 1e-9:
        return None
    cos = float(np.clip(measured @ column / (a * b), -1.0, 1.0))
    if cos <= 0:
        return None
    # For unit col and jitter j, E[cos] ~ 1/sqrt(1 + j^2 * (d-1)/1) is a decent
    # approximation in d dimensions; invert it.
    d = len(column)
    val = (1.0 / max(cos, 1e-6) ** 2 - 1.0) / max(d - 1, 1)
    return float(np.sqrt(max(val, 0.0)))


def main() -> None:
    cfg = load_engine_profile("engine_vrde_180.yaml")
    n_cyl = cfg["geometry"]["cylinders"]
    sigma = _sigma_vector(n_cyl)

    # Channels BE-1's collapsed air path leaves dead — a measurement dominated
    # by these would be measuring the bug, not the fault.
    dead = [i for i, s in enumerate(sigma) if s <= 1.1e-3]

    per_fault: dict[str, dict] = {}
    pooled: list[float] = []

    for fault, hook in FAULT_HOOKS.items():
        vals = []
        for severity in (0.05, 0.10, 0.20, 0.30):
            for cyl in (range(n_cyl) if hook.get("per_cyl") else [0]):
                try:
                    rho = _residual_for(cfg, hook, severity, cyl, sigma)
                except Exception:
                    continue
                col = _column(fault, cyl, n_cyl)
                j = _jitter(rho, col)
                if j is not None and np.isfinite(j):
                    vals.append(j)
        if vals:
            per_fault[fault] = {
                "jitter_median": round(float(np.median(vals)), 4),
                "jitter_p90":    round(float(np.percentile(vals, 90)), 4),
                "n_samples":     len(vals),
            }
            pooled.extend(vals)
        else:
            per_fault[fault] = {"jitter_median": None, "note": "no usable residual"}

    unmeasurable = sorted(set(INCIDENCE) - set(FAULT_HOOKS))
    report = {
        "pooled_jitter_median": round(float(np.median(pooled)), 4) if pooled else None,
        "pooled_jitter_p90":    round(float(np.percentile(pooled, 90)), 4) if pooled else None,
        "n_total_samples":      len(pooled),
        "per_fault":            per_fault,
        "unmeasurable_faults":  unmeasurable,
        "dead_residual_channels": [f"rho{i+1}" for i in dead],
        "caveat": (
            "Measured against BE-1's MVEM at the commit noted below. Channels "
            "listed in dead_residual_channels sit on the sigma floor because "
            "the air path is collapsed (docs/qa/known-issues.md), so faults "
            "living mainly on them are under-measured. Faults in "
            "unmeasurable_faults have no parameter hook in the current MVEM "
            "and are NOT estimated — the pooled value is used for them, and "
            "that substitution is recorded here rather than hidden. Re-run "
            "after BE-1's Task 0."
        ),
    }

    out = Path(__file__).parent / "jitter_report.json"
    out.write_text(json.dumps(report, indent=2))

    print(f"pooled jitter median = {report['pooled_jitter_median']}  "
          f"p90 = {report['pooled_jitter_p90']}  (n={report['n_total_samples']})")
    for f, d in per_fault.items():
        print(f"  {f:<22} {d.get('jitter_median')}")
    print(f"\ndead channels: {report['dead_residual_channels']}")
    print(f"unmeasurable : {unmeasurable}")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
