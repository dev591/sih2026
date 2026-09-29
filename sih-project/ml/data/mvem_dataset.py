"""
ML training data from the engine model itself.

Every weight in ml/weights/ used to be fitted to ml/data/synthetic.py — AR(1)
noise around hand-written incidence columns with isotropic direction jitter.
Measured on 2026-09-24, those columns disagreed with what the engine model
actually does for several faults (injector fouling cosine 0.25, bearing 0.45),
and the live classifier named a fouled injector "ignition misfire" on every
frame. This generator replaces that data: each run steps the SAME
twin.session.EngineSession the live server steps, so a training window is
exactly what a live window looks like.

Physical variety per run (all sampled, all recorded in the metadata):
  * installation — the sensor-offset seed. Held-out installations never appear
    in training, so the score measures transfer to an engine we never saw.
  * operating point — altitude 0-14,000 ft, throttle 55-100 %, ISA -10..+20 K
  * fault — every backend fault key, random rate, onset and cylinder; 25 %
    healthy runs.
  * commissioning baseline — per installation, the mean feature vector over a
    healthy commissioning run at three operating points (standard engine
    condition-trend-monitoring practice). Subtracted before any model sees it.

Run:
    cd sih-project && python -m ml.data.mvem_dataset --installations 40 \
        --runs-per-installation 40 --seconds 300 --workers 7
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from ml.features import feature_names, feature_vector  # noqa: E402

OUT_DIR = ROOT / "data" / "mvem_v1"

# Backend fault key -> (M3 class name, rate range per minute, per-cylinder).
# Rates span "barely emerging by the end of the run" to "past the clamp".
FAULTS: dict[str, tuple[str, tuple[float, float], bool]] = {
    "injector":     ("injector_fouling",         (0.01, 0.12), True),
    "turbo":        ("turbo_degradation",        (0.01, 0.10), False),
    "cooling":      ("cooling_fouling",          (0.02, 0.20), False),
    "coolantPump":  ("coolant_pump_degradation", (0.02, 0.20), False),
    "bearing":      ("bearing_wear",             (0.03, 0.40), False),
    "ringWear":     ("ring_wear",                (0.01, 0.10), False),
    "oilLeak":      ("oil_pump_wear",            (0.03, 0.30), False),
    "fuelFilter":   ("fuel_filter_clog",         (0.02, 0.20), False),
    "misfire":      ("injection_misfire",         (0.03, 0.30), True),
    "detonation":   ("detonation",               (0.03, 0.30), True),
    "chtSensor":    ("cht_sensor_drift",         (5.0, 60.0), True),
    "egtSensor":    ("egt_sensor_drift",         (10.0, 150.0), True),
    "mapSensor":    ("map_sensor_drift",         (5.0, 60.0), False),
    "lambdaSensor": ("lambda_sensor_drift",      (0.005, 0.06), False),
}
CLASSES = ["healthy"] + [v[0] for v in FAULTS.values()]

COMMISSION_POINTS = [(3000.0, 72.0), (8000.0, 90.0), (12000.0, 60.0)]
COMMISSION_S, COMMISSION_SKIP = 45, 10


def _load():
    from twin.profiles import load_engine_profile, DEFAULT_ENGINE
    b = ROOT / "backend" / "config"
    engine = DEFAULT_ENGINE
    cfg = load_engine_profile(engine)
    eng_sigma_dir = b / "engines" / cfg["profile"]["id"]
    sigma_dir = eng_sigma_dir if eng_sigma_dir.exists() else b
    sig = json.loads((sigma_dir / "sigma_vector.json").read_text())
    sige = json.loads((sigma_dir / "sigma_ext.json").read_text())["sigma"]
    return cfg, sig, sige


def commission(installation: int) -> list[float]:
    """Healthy commissioning run for one installation -> baseline vector."""
    from twin.session import EngineSession
    cfg, sig, sige = _load()
    rows = []
    for alt, thr in COMMISSION_POINTS:
        s = EngineSession(cfg, sig, sige, plant_seed=installation * 7 + 1,
                          measure_seed=installation, with_engine_b=False,
                          altitude_fn=lambda t, c, a=alt: a,
                          throttle_fn=lambda t, th=thr: th,
                          tas_fn=lambda t, c: 61.2)
        for k in range(COMMISSION_S):
            tk = s.step()
            if k >= COMMISSION_SKIP:
                rows.append(feature_vector(tk.rho, tk.rho_ext))
    return np.mean(rows, axis=0).tolist()


def simulate(job: dict) -> dict:
    from twin.session import EngineSession
    cfg, sig, sige = _load()
    rng = np.random.default_rng(job["seed"])
    alt0 = float(rng.uniform(0, 14000))
    alt1 = float(np.clip(alt0 + rng.choice([0, 0, rng.uniform(-4000, 4000)]), 0, 14000))
    t_alt = float(rng.uniform(60, 240))
    thr = float(rng.uniform(55, 100))
    isa_k = float(rng.uniform(-10, 20))

    def alt_fn(t, c):
        if t < t_alt:
            return alt0
        u = min(1.0, (t - t_alt) / 30.0)
        return alt0 + (alt1 - alt0) * u * u * (3 - 2 * u)

    key = job["fault"]
    fc, cls, cyl, rate, onset = {}, "healthy", -1, 0.0, -1.0
    if key != "healthy":
        cls, (lo, hi), per_cyl = FAULTS[key]
        rate = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
        onset = float(rng.uniform(40, 140))
        cyl = int(rng.integers(0, cfg["geometry"]["cylinders"])) if per_cyl else -1
        spec = {"startT": onset, "rate": rate}
        if per_cyl:
            spec["cyl"] = cyl
        fc = {key: spec}

    s = EngineSession(cfg, sig, sige, plant_seed=int(rng.integers(1, 2**31)),
                      measure_seed=job["installation"], with_engine_b=False,
                      altitude_fn=alt_fn, throttle_fn=lambda t: thr,
                      tas_fn=lambda t, c: 61.2, isa_base_K=isa_k, fault_config=fc)
    X, sev = [], []
    for _ in range(job["seconds"]):
        tk = s.step()
        X.append(feature_vector(tk.rho, tk.rho_ext))
        sev.append(0.0 if onset < 0 else max(0.0, rate * (tk.t - onset) / 60.0))
    t = np.arange(job["seconds"], dtype=np.float32)
    label = np.where((onset >= 0) & (t >= onset), CLASSES.index(cls), 0).astype(np.int16)
    return {"X": np.asarray(X, np.float32), "label": label,
            "severity": np.asarray(sev, np.float32),
            "meta": {**job, "class": cls, "cyl": cyl, "rate": rate, "onset": onset,
                     "alt0": alt0, "alt1": alt1, "throttle": thr, "isa_K": isa_k}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--installations", type=int, default=40)
    ap.add_argument("--runs-per-installation", type=int, default=40)
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    installations = list(range(1001, 1001 + a.installations))
    rng = np.random.default_rng(20260924)
    keys = ["healthy"] + list(FAULTS)
    weights = np.array([len(FAULTS) / 3.0] + [1.0] * len(FAULTS))
    weights /= weights.sum()
    jobs = []
    for inst in installations:
        for _ in range(a.runs_per_installation):
            jobs.append({"installation": inst, "fault": str(rng.choice(keys, p=weights)),
                         "seconds": a.seconds, "seed": int(rng.integers(1, 2**31))})

    t0 = time.time()
    with Pool(a.workers) as pool:
        baselines = dict(zip(installations, pool.map(commission, installations)))
        print(f"commissioned {len(baselines)} installations in {time.time() - t0:.0f}s", flush=True)
        results = []
        for i, r in enumerate(pool.imap_unordered(simulate, jobs, chunksize=2)):
            results.append(r)
            if (i + 1) % 50 == 0:
                el = time.time() - t0
                print(f"{i + 1}/{len(jobs)} runs  {el:.0f}s  eta {el / (i + 1) * (len(jobs) - i - 1):.0f}s", flush=True)

    results.sort(key=lambda r: r["meta"]["seed"])
    np.savez_compressed(
        a.out / "runs.npz",
        X=np.stack([r["X"] for r in results]),
        label=np.stack([r["label"] for r in results]),
        severity=np.stack([r["severity"] for r in results]),
        installation=np.array([r["meta"]["installation"] for r in results]),
    )
    (a.out / "meta.json").write_text(json.dumps({
        "classes": CLASSES, "features": feature_names(int(_load()[0]["geometry"]["cylinders"])),
        "baselines": {str(k): v for k, v in baselines.items()},
        "commission_points": COMMISSION_POINTS,
        "runs": [r["meta"] for r in results],
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=1))
    print(f"wrote {len(results)} runs to {a.out} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
