"""
Healthy-data sigma bootstrap.

Per docs/spec/residual-spec.md §0: "Its noise distribution is characterised
ONCE on healthy data and reused everywhere." This script is that
characterisation. It never runs a fault — plant and twin share identical
nominal parameters — so every volt of spread in raw rho is measurement
noise plus operating-point transients, exactly the population the sigma
vector is supposed to normalise against.

Run standalone:
    python3 -m parity.sigma_generator

Writes backend/config/sigma_vector.json, an 11-element list matching the
rho1..rho11 order in parity/residuals.py. main.py loads this file at
startup and divides every rho by it; if the file is missing, main.py falls
back to [1.0]*11, which is fine for wiring but wrong for anomaly scoring.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from twin.profiles import load_engine_profile
from twin.mvem import MVEM
from twin.atmosphere import isa
from twin.measurement import MeasurementModel
from parity.residuals import compute_residuals

# Operating envelope to sample: (altitude_ft, throttle_pct) pairs spanning
# sea level to critical altitude, idle-ish to full power. Sigma must be
# characterised across the envelope, not at one point, or it will be too
# tight off that point and the detector false-alarms on every throttle move.
OPERATING_POINTS = [
    (0, 40), (0, 72), (0, 100),
    (5000, 40), (5000, 72), (5000, 100),
    (11000, 40), (11000, 72), (11000, 100),
]

STEPS_TO_STEADY_STATE = 10
SAMPLES_PER_POINT = 30
DT = 1.0


def collect_raw_rho(cfg: dict) -> np.ndarray:
    nominal_params = {
        'cd_inj': [1.0] * cfg['geometry']['cylinders'],
        'eta_v_scale': 1.0,
        'eta_c_scale': 1.0,
        'hA_scale': 1.0,
        'f_fric_scale': 1.0,
    }

    samples = []

    for altitude_ft, throttle_pct in OPERATING_POINTS:
        atm = isa(altitude_ft, isa_offset_K=0.0)

        plant = MVEM(cfg)
        twin = MVEM(cfg)
        measure_plant = MeasurementModel(seed=hash((altitude_ft, throttle_pct)) & 0xFFFF)
        measure_twin = MeasurementModel(seed=999)

        for _ in range(STEPS_TO_STEADY_STATE):
            plant.step(DT, nominal_params, atm, throttle_pct)
            twin.step(DT, nominal_params, atm, throttle_pct)

        for _ in range(SAMPLES_PER_POINT):
            plant.step(DT, nominal_params, atm, throttle_pct)
            twin.step(DT, nominal_params, atm, throttle_pct)

            measured = measure_plant.measure(plant.get_outputs(), add_noise=True)
            predicted = measure_twin.measure(twin.get_outputs(), add_noise=False)

            rho = compute_residuals(measured, predicted, cfg, sigma_vec=None)
            samples.append(rho)

    return np.array(
        [[np.nan if v is None else v for v in row] for row in samples],
        dtype=float,
    )


def compute_sigma(raw: np.ndarray) -> list:
    sigma = np.nanstd(raw, axis=0)
    # rho3 is null on VRDE (unthrottled FADEC, no Path 4 sensor — see
    # residual-spec.md §1). nanstd over an all-NaN column returns NaN;
    # 1.0 is a harmless placeholder since that channel is never populated.
    sigma = np.nan_to_num(sigma, nan=1.0)
    # Floor so a channel that happens to be near-silent at these operating
    # points can't produce a near-zero sigma and blow up downstream division.
    sigma = np.maximum(sigma, 1e-3)
    return [float(s) for s in sigma]


def main():
    cfg = load_engine_profile("engine_vrde_180.yaml")
    raw = collect_raw_rho(cfg)
    sigma = compute_sigma(raw)

    out_dir = Path(__file__).resolve().parent.parent / "config"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "sigma_vector.json"
    with open(out_path, "w") as f:
        json.dump(sigma, f, indent=2)

    labels = [
        "rho1_sd_vs_comp", "rho2_sd_vs_lambda", "rho3_sd_vs_restr",
        "rho4_energy", "rho5_power",
        "rho6_cyl_dev", "rho7_cyl_dev", "rho8_cyl_dev", "rho9_cyl_dev",
        "rho10_oil", "rho11_ripple",
    ]
    print(f"Sampled {raw.shape[0]} healthy points across {len(OPERATING_POINTS)} operating points.")
    for label, s in zip(labels, sigma):
        print(f"  {label:20s} sigma = {s:.5f}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
