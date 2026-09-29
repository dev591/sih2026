"""
Measure J = ∂features/∂θ on the engine model — the observation matrix of
ml/ukf/filter.py. Each health parameter is perturbed alone, the engine run to
thermal steady state against the nominal twin, and the change in every model
input feature (ml/features.py, σ-normalised) recorded per unit of parameter
change. Measured, noise-free, at the cruise reference (11,000 ft / 72 %).

    cd sih-project && python -m ml.ukf.sensitivity
"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from ml.features import feature_vector  # noqa: E402
from ml.ukf.filter import JACOBIAN_PATH, params_for  # noqa: E402

ALT, THR, SETTLE, AVG = 11000.0, 72.0, 420, 40
DELTA = 0.05


def _features(perturb: tuple[str, float] | None) -> np.ndarray:
    from parity.residuals import compute_extra_residuals, compute_residuals
    from twin.atmosphere import isa
    from twin.measurement import MeasurementModel
    from twin.mvem import MVEM
    from twin.profiles import load_engine_profile, DEFAULT_ENGINE
    from twin.session import nominal_params
    cfg = load_engine_profile(DEFAULT_ENGINE)
    b = ROOT / "backend" / "config"
    eng_sigma_dir = b / "engines" / cfg["profile"]["id"]
    sigma_dir = eng_sigma_dir if eng_sigma_dir.exists() else b
    sig = json.loads((sigma_dir / "sigma_vector.json").read_text())
    sige = json.loads((sigma_dir / "sigma_ext.json").read_text())["sigma"]
    n = cfg["geometry"]["cylinders"]
    nom = nominal_params(n)
    pp = nominal_params(n)
    if perturb:
        name, val = perturb
        if name.startswith("cd_inj_"):
            pp["cd_inj"][int(name[-1]) - 1] = val
        else:
            pp[name] = val
    plant, twin = MVEM(cfg, seed=7), MVEM(cfg, seed=8)
    mp, mt = MeasurementModel(seed=11, is_twin=True), MeasurementModel(seed=999, is_twin=True)
    atm = isa(ALT, 0.0)
    rows = []
    for k in range(SETTLE):
        plant.step(1.0, pp, atm, THR, 61.2)
        twin.step(1.0, nom, atm, THR, 61.2)
        if k >= SETTLE - AVG:
            m = mp.measure(plant.get_outputs(), add_noise=False)
            p = mt.measure(twin.get_outputs(), add_noise=False)
            rows.append(feature_vector(compute_residuals(m, p, cfg, sig),
                                       compute_extra_residuals(m, p, cfg, sige)))
    return np.mean(rows, axis=0)


def _job(item):
    name, nominal, lo, hi = item
    step = DELTA if name == "f_fric_scale" else -DELTA
    return name, step, _features((name, nominal + step))


def main():
    from twin.profiles import load_engine_profile, DEFAULT_ENGINE
    PARAMS = params_for(int(load_engine_profile(DEFAULT_ENGINE)["geometry"]["cylinders"]))
    with Pool(7) as pool:
        base = pool.apply_async(_features, (None,))
        cols = pool.map(_job, PARAMS)
        f0 = base.get()
    J = np.column_stack([(f - f0) / step for _, step, f in cols])
    weights = ROOT / "ml" / "weights" / "v2" / "config.json"
    r_std = (json.loads(weights.read_text())["healthy_std"] if weights.exists()
             else [1.0] * J.shape[0])
    JACOBIAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    JACOBIAN_PATH.write_text(json.dumps({
        "J": J.round(5).tolist(), "params": [p[0] for p in PARAMS],
        "reference": {"altitude_ft": ALT, "throttle_pct": THR, "delta": DELTA},
        "r_std": r_std}, indent=1))
    np.set_printoptions(precision=1, suppress=True, linewidth=200)
    for (name, *_), col in zip(PARAMS, J.T):
        print(f"{name:16s} {col}")
    print(f"wrote {JACOBIAN_PATH}")


if __name__ == "__main__":
    main()
