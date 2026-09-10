"""
PRAMANA — healthy-data sigma bootstrap + dataset writer.

Per residual-spec.md §0:
    "Its noise distribution is characterised ONCE on healthy data and
    reused everywhere."

This script is that characterisation.  It never runs a fault — plant and
twin share identical nominal parameters — so every variation in raw ρ is
measurement noise plus operating-point transients, exactly the population
the sigma vector is supposed to normalise against.

Usage
-----
    # Sigma bootstrap only (fast, run after any MVEM retune):
    python -m parity.sigma_generator

    # Bootstrap + write healthy dataset for BE-2:
    python -m parity.sigma_generator --with-dataset

Outputs
-------
    backend/config/sigma_vector.json          — 11-element σ list
    data/healthy_flights/healthy_001.parquet  — BE-2 training data
      (falls back to .csv if pandas/pyarrow are not installed)
"""

import json
import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parity.residuals import compute_residuals
from twin.atmosphere import isa
from twin.measurement import MeasurementModel
from twin.mvem import MVEM
from twin.profiles import load_engine_profile

# ---------------------------------------------------------------------------
# Operating envelope
# ---------------------------------------------------------------------------

# Spans sea level → critical altitude, idle → full power.
# Sigma characterised across the envelope so the detector does not
# false-alarm on normal throttle and altitude changes.
OPERATING_POINTS = [
    (0,     40), (0,     72), (0,     100),
    (5000,  40), (5000,  72), (5000,  100),
    (11000, 40), (11000, 72), (11000, 100),
]

STEPS_TO_STEADY_STATE = 10
SAMPLES_PER_POINT     = 30
DT                    = 1.0


# ---------------------------------------------------------------------------
# Sigma bootstrap
# ---------------------------------------------------------------------------

def collect_raw_rho(cfg: dict) -> np.ndarray:
    """Sample raw ρ across the operating envelope with a healthy engine."""
    nominal_params = {
        "cd_inj":      [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale": 1.0,
        "eta_c_scale": 1.0,
        "hA_scale":    1.0,
        "f_fric_scale": 1.0,
    }
    samples = []

    for altitude_ft, throttle_pct in OPERATING_POINTS:
        atm = isa(altitude_ft, isa_offset_K=0.0)

        plant        = MVEM(cfg)
        twin         = MVEM(cfg)
        measure_plant = MeasurementModel(seed=hash((altitude_ft, throttle_pct)) & 0xFFFF)
        measure_twin  = MeasurementModel(seed=999)

        for _ in range(STEPS_TO_STEADY_STATE):
            plant.step(DT, nominal_params, atm, throttle_pct)
            twin.step( DT, nominal_params, atm, throttle_pct)

        for _ in range(SAMPLES_PER_POINT):
            plant.step(DT, nominal_params, atm, throttle_pct)
            twin.step( DT, nominal_params, atm, throttle_pct)

            measured  = measure_plant.measure(plant.get_outputs(), add_noise=True)
            predicted = measure_twin.measure(twin.get_outputs(),   add_noise=False)
            rho = compute_residuals(measured, predicted, cfg, sigma_vec=None)
            samples.append(rho)

    # ρ₃ is None for VRDE (unthrottled, no Path 4) — replace with NaN so
    # the array stays numeric.
    return np.array(
        [[np.nan if v is None else v for v in row] for row in samples],
        dtype=float,
    )


def compute_sigma(raw: np.ndarray) -> list:
    """
    Compute per-channel standard deviation from healthy samples.

    ρ₃ column is all-NaN for the VRDE; nanstd returns NaN for it which
    is replaced with 1.0 (harmless placeholder — the channel is never
    populated, so the divisor is never used for real data).

    A floor of 1e-3 prevents channels that are structurally near-zero
    at healthy steady state from producing a zero sigma.  The normalisation
    floor in residuals.py (0.05) does the real guard against blow-up.
    """
    # Suppress the expected "degrees of freedom <= 0" warning for the
    # all-NaN ρ₃ column — this is intentional, not a data problem.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Degrees of freedom <= 0",
            category=RuntimeWarning,
        )
        sigma = np.nanstd(raw, axis=0)

    sigma = np.nan_to_num(sigma, nan=1.0)       # ρ₃ → 1.0
    sigma = np.maximum(sigma, 1e-3)             # floor
    return [float(s) for s in sigma]


# ---------------------------------------------------------------------------
# Healthy dataset for BE-2
# ---------------------------------------------------------------------------

def write_healthy_dataset(cfg: dict, out_dir: Path) -> None:
    """
    Write a labelled healthy-flight dataset for BE-2's autoencoder training.

    Same operating-point sweep as the sigma bootstrap but logs full
    telemetry frames.  Zero faults.

    Columns: altitude_ft, throttle_pct, step,
             rho1..rho11 (raw units, not sigma-normalised),
             rpm, map_hPa, iat_K, fuel_flow_kgps, brake_power_kW,
             turbo_rpm, cht_C_mean, egt_C_mean, lambda_val

    Must be called AFTER the MVEM air path is fixed.  Data from a broken
    engine would give BE-2 a pathological healthy baseline.
    """
    nominal_params = {
        "cd_inj":      [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale": 1.0,
        "eta_c_scale": 1.0,
        "hA_scale":    1.0,
        "f_fric_scale": 1.0,
    }
    rows: list[dict] = []

    for altitude_ft, throttle_pct in OPERATING_POINTS:
        atm = isa(altitude_ft, isa_offset_K=0.0)
        plant = MVEM(cfg);  twin = MVEM(cfg)
        mp = MeasurementModel(seed=hash((altitude_ft, throttle_pct)) & 0xFFFF)
        mt = MeasurementModel(seed=999)

        for _ in range(STEPS_TO_STEADY_STATE):
            plant.step(DT, nominal_params, atm, throttle_pct)
            twin.step( DT, nominal_params, atm, throttle_pct)

        for step_i in range(SAMPLES_PER_POINT):
            plant.step(DT, nominal_params, atm, throttle_pct)
            twin.step( DT, nominal_params, atm, throttle_pct)

            measured  = mp.measure(plant.get_outputs(), add_noise=True)
            predicted = mt.measure(twin.get_outputs(),  add_noise=False)
            rho = compute_residuals(measured, predicted, cfg, sigma_vec=None)

            row: dict = {
                "altitude_ft":  altitude_ft,
                "throttle_pct": throttle_pct,
                "step":         step_i,
            }
            for i, r in enumerate(rho):
                row[f"rho{i + 1}"] = float(r) if r is not None else float("nan")

            row.update({
                "rpm":            measured["rpm"],
                "map_hPa":        measured["map_hPa"],
                "iat_K":          measured["iat_K"],
                "fuel_flow_kgps": measured["fuel_flow_kgps"],
                "brake_power_kW": measured["brake_power_kW"],
                "turbo_rpm":      measured["turbo_rpm"],
                "cht_C_mean":     float(np.mean(measured["cht_C"])),
                "egt_C_mean":     float(np.mean(measured["egt_C"])),
                "lambda_val":     float(measured.get("lambda_val", float("nan"))),
            })
            rows.append(row)

    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import pandas as pd           # type: ignore
        df = pd.DataFrame(rows)
        try:
            out_path = out_dir / "healthy_001.parquet"
            df.to_parquet(out_path, index=False)
            print(f"Wrote {out_path}  ({len(df)} rows, {len(df.columns)} cols)")
        except Exception:
            out_path = out_dir / "healthy_001.csv"
            df.to_csv(out_path, index=False)
            print(f"pyarrow unavailable — wrote CSV: {out_path}  ({len(df)} rows)")
    except ImportError:
        import csv as _csv
        out_path = out_dir / "healthy_001.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"pandas unavailable — wrote CSV: {out_path}  ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="PRAMANA sigma bootstrap (+ optional healthy dataset)"
    )
    parser.add_argument(
        "--with-dataset",
        action="store_true",
        help="Also write data/healthy_flights/healthy_001.parquet for BE-2",
    )
    args = parser.parse_args()

    cfg = load_engine_profile("engine_vrde_180.yaml")

    # Sigma bootstrap
    raw   = collect_raw_rho(cfg)
    sigma = compute_sigma(raw)

    out_dir = Path(__file__).resolve().parent.parent / "config"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "sigma_vector.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sigma, f, indent=2)

    labels = [
        "rho1_sd_vs_comp",   "rho2_sd_vs_lambda", "rho3_sd_vs_restr",
        "rho4_energy",       "rho5_power",
        "rho6_cyl_dev",      "rho7_cyl_dev",       "rho8_cyl_dev",
        "rho9_cyl_dev",      "rho10_oil",           "rho11_ripple",
    ]
    print(
        f"Sampled {raw.shape[0]} healthy points across "
        f"{len(OPERATING_POINTS)} operating points."
    )
    for label, s in zip(labels, sigma):
        print(f"  {label:20s}  sigma = {s:.5f}")
    print(f"\nWrote {out_path}")

    # Optional healthy dataset
    if args.with_dataset:
        data_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "data" / "healthy_flights"
        )
        write_healthy_dataset(cfg, data_dir)


if __name__ == "__main__":
    main()
