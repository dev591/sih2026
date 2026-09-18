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
from twin.faults import apply_fault_config, fresh_sensor_biases
from twin.damage import DamageIntegrator
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

# 150 s, not 10: the coolant loop's time constant is ~45 s and the oil bulk is
# slower still, so a 10 s settle sampled a WARM-UP TRANSIENT into rho4, rho12
# and rho13 rather than the steady healthy population sigma is supposed to
# describe.
STEPS_TO_STEADY_STATE = 150
SAMPLES_PER_POINT     = 30
DT                    = 1.0

# Independent instrumentation packages observing the SAME engine at each
# operating point.
#
# Why this exists: for several channels the healthy spread is dominated by
# FIXED per-probe calibration offsets, not by per-sample noise — measured
# between/within variance ratios of 3.6 (rho8) and 6.5 (rho12). With one probe
# set per operating point, sigma for those channels rested on nine draws, and
# rho12's 90 % CI was [0.60, 1.09] on a 0.97 estimate. Dividing a residual by
# an under-determined sigma is how a real fault gets scaled into or out of
# visibility.
#
# The plant physics is identical across probe sets — only the transducers
# differ — so one physics run can be observed by K instrumentation packages at
# essentially no extra cost. Each carries its own lag state and its own
# once-drawn offsets, which is exactly the population being characterised.
PROBE_SETS_PER_POINT  = 6


# ---------------------------------------------------------------------------
# Sigma bootstrap
# ---------------------------------------------------------------------------

def collect_raw_rho(cfg: dict) -> np.ndarray:
    """Sample raw ρ across the operating envelope with a healthy engine."""
    nominal_params = {
        "cd_inj":       [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale":  1.0,
        "eta_c_scale":  1.0,
        "hA_scale":     1.0,
        "f_fric_scale": 1.0,
        "oil_pump_scale": 1.0,
        "fuel_rail_scale": 1.0,
        "misfire_prob":  [0.0] * cfg["geometry"]["cylinders"],
        "detonation_sev": [0.0] * cfg["geometry"]["cylinders"],
        "rad_eff_scale": 1.0,
        "cool_pump_scale": 1.0,
    }
    samples = []

    for altitude_ft, throttle_pct in OPERATING_POINTS:
        atm = isa(altitude_ft, isa_offset_K=0.0)

        base_seed    = hash((altitude_ft, throttle_pct)) & 0xFFFF
        plant        = MVEM(cfg, seed=base_seed)
        twin         = MVEM(cfg, seed=999)
        # K independent instrumentation packages on the one engine.
        probes       = [MeasurementModel(seed=base_seed + 7919 * k)
                        for k in range(PROBE_SETS_PER_POINT)]
        measure_twin = MeasurementModel(seed=999, is_twin=True)

        for _ in range(STEPS_TO_STEADY_STATE):
            plant.step(DT, nominal_params, atm, throttle_pct)
            twin.step( DT, nominal_params, atm, throttle_pct)
            # Settle the lag filters too, or the first logged sample carries a
            # step response instead of a steady reading.
            out_p, out_t = plant.get_outputs(), twin.get_outputs()
            for probe in probes:
                probe.measure(out_p, add_noise=True)
            measure_twin.measure(out_t, add_noise=False)

        for _ in range(SAMPLES_PER_POINT):
            plant.step(DT, nominal_params, atm, throttle_pct)
            twin.step( DT, nominal_params, atm, throttle_pct)

            out_p, out_t = plant.get_outputs(), twin.get_outputs()
            predicted = measure_twin.measure(out_t, add_noise=False)
            for probe in probes:
                measured = probe.measure(out_p, add_noise=True)
                samples.append(
                    compute_residuals(measured, predicted, cfg, sigma_vec=None)
                )

    # ρ₃ is None for VRDE (unthrottled, no Path 4) — replace with NaN so
    # the array stays numeric.
    return np.array(
        [[np.nan if v is None else v for v in row] for row in samples],
        dtype=float,
    )


def compute_sigma(raw: np.ndarray) -> list:
    """
    Compute per-channel standard deviation from healthy samples.

    ρ₃ is all-NaN for the VRDE (and ρ₁₂ on a profile with no coolant
    loop); nanstd returns NaN for such a column, which
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
        "cd_inj":       [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale":  1.0,
        "eta_c_scale":  1.0,
        "hA_scale":     1.0,
        "f_fric_scale": 1.0,
        "oil_pump_scale": 1.0,
        "fuel_rail_scale": 1.0,
        "misfire_prob":  [0.0] * cfg["geometry"]["cylinders"],
        "detonation_sev": [0.0] * cfg["geometry"]["cylinders"],
    }
    rows: list[dict] = []

    for altitude_ft, throttle_pct in OPERATING_POINTS:
        atm = isa(altitude_ft, isa_offset_K=0.0)
        plant = MVEM(cfg, seed=hash((altitude_ft, throttle_pct)) & 0xFFFF)
        twin = MVEM(cfg, seed=999)
        mp = MeasurementModel(seed=hash((altitude_ft, throttle_pct)) & 0xFFFF)
        mt = MeasurementModel(seed=999, is_twin=True)

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
# Fault-run dataset generation for BE-2 (Directive §4)
# ---------------------------------------------------------------------------

FAULT_SCENARIOS = [
    {"name": "injector_cyl1",  "config": {"injector":  {"startT": 0, "cyl": 1, "rate": 0.045}}},
    {"name": "injector_cyl3",  "config": {"injector":  {"startT": 0, "cyl": 3, "rate": 0.060}}},
    {"name": "turbo_degrade",  "config": {"turbo":     {"startT": 0, "rate": 0.040}}},
    {"name": "cooling_foul",   "config": {"cooling":   {"startT": 0, "rate": 0.035}}},
    {"name": "bearing_wear",   "config": {"bearing":   {"startT": 0, "rate": 0.050}}},
    {"name": "ring_wear",      "config": {"ringWear":  {"startT": 0, "rate": 0.040}}},
    {"name": "oil_leak",       "config": {"oilLeak":   {"startT": 0, "rate": 0.045}}},
    {"name": "fuel_filter",    "config": {"fuelFilter":{"startT": 0, "rate": 0.035}}},
    {"name": "misfire_cyl2",   "config": {"misfire":   {"startT": 0, "cyl": 2, "rate": 0.30}}},
    {"name": "detonation_cyl0","config": {"detonation":{"startT": 0, "cyl": 0, "rate": 0.50}}},
]

FAULT_RUN_DURATION_S = 300     # 5-minute runs, matching demo script length
FAULT_ALTITUDE_FT    = 18000   # Demo cruise altitude
FAULT_THROTTLE_PCT   = 72      # Demo cruise throttle


def write_fault_dataset(cfg: dict, out_dir: Path) -> None:
    """
    Generate one labelled run per fault scenario.

    Each row contains full telemetry + residuals + ground-truth D and RUL_h
    from the damage integrator. This is what makes M3's RUL head trainable
    on real labels.
    """
    n_cyl = cfg["geometry"]["cylinders"]
    nominal_params = {
        "cd_inj":       [1.0] * n_cyl,
        "eta_v_scale":  1.0,
        "eta_c_scale":  1.0,
        "hA_scale":     1.0,
        "f_fric_scale": 1.0,
        "oil_pump_scale": 1.0,
        "fuel_rail_scale": 1.0,
        "misfire_prob":  [0.0] * n_cyl,
        "detonation_sev": [0.0] * n_cyl,
    }

    out_dir.mkdir(parents=True, exist_ok=True)

    for scenario in FAULT_SCENARIOS:
        name = scenario["name"]
        fault_config = scenario["config"]
        print(f"  Generating fault run: {name} ...")

        plant = MVEM(cfg, seed=42)
        twin  = MVEM(cfg, seed=43)
        mp = MeasurementModel(seed=42)
        mt = MeasurementModel(seed=999, is_twin=True)
        damage = DamageIntegrator()

        atm = isa(FAULT_ALTITUDE_FT, isa_offset_K=0.0)

        # Warm up to steady state
        for _ in range(STEPS_TO_STEADY_STATE):
            plant.step(DT, nominal_params, atm, FAULT_THROTTLE_PCT)
            twin.step(DT, nominal_params, atm, FAULT_THROTTLE_PCT)

        rows: list[dict] = []
        sigma_cfg_path = Path(__file__).resolve().parent.parent / "config" / "sigma_vector.json"
        sigma_vec = None
        if sigma_cfg_path.exists():
            with open(sigma_cfg_path) as f:
                sigma_vec = json.load(f)

        for step_i in range(FAULT_RUN_DURATION_S):
            t = float(step_i)

            # Apply fault ramp to plant params
            import copy
            faulted_params, sensor_biases, _ = apply_fault_config(
                t, fault_config, copy.deepcopy(nominal_params),
                fresh_sensor_biases(n_cyl), 0.0, FAULT_ALTITUDE_FT,
            )

            plant.step(DT, faulted_params, atm, FAULT_THROTTLE_PCT)
            twin.step(DT, nominal_params, atm, FAULT_THROTTLE_PCT)

            out_plant = plant.get_outputs()
            out_twin  = twin.get_outputs()

            measured  = mp.measure(out_plant, sensor_biases=sensor_biases, add_noise=True)
            predicted = mt.measure(out_twin, add_noise=False)
            rho = compute_residuals(measured, predicted, cfg, sigma_vec=sigma_vec)

            # Damage integration
            cht_vals = out_plant['cht_C']
            T_cht_mean_K = sum(cht_vals) / len(cht_vals) + 273.15
            T_fric = 6.2 * faulted_params.get('f_fric_scale', 1.0) * plant.w / 100.0
            damage.step(
                dt=DT,
                fault_config=fault_config,
                T_fric=T_fric,
                omega=plant.w,
                T_cht_K=T_cht_mean_K,
                oil_temp_K=plant.T_oil,
                t=t,
            )
            dmg = damage.get_state()

            row: dict = {
                "t":             t,
                "fault_name":    name,
                "altitude_ft":   FAULT_ALTITUDE_FT,
                "throttle_pct":  FAULT_THROTTLE_PCT,
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
                "oil_press_bar":  measured["oil_press_bar"],
                "oil_temp_C":     measured["oil_temp_C"],
                "D":              dmg["damage_state"],
                "dD_dt":          dmg["damage_rate_per_hr"] / 3600.0,
                "RUL_h":          dmg["rul_h"] if dmg["rul_h"] is not None else float("nan"),
            })
            rows.append(row)

        # Write output
        try:
            import pandas as pd  # type: ignore
            df = pd.DataFrame(rows)
            try:
                out_path = out_dir / f"{name}.parquet"
                df.to_parquet(out_path, index=False)
                print(f"    → {out_path}  ({len(df)} rows)")
            except Exception:
                out_path = out_dir / f"{name}.csv"
                df.to_csv(out_path, index=False)
                print(f"    → CSV fallback: {out_path}  ({len(df)} rows)")
        except ImportError:
            import csv as _csv
            out_path = out_dir / f"{name}.csv"
            with open(out_path, "w", newline="", encoding="utf-8") as fh:
                writer = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"    → CSV (no pandas): {out_path}  ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="PRAMANA sigma bootstrap (+ optional healthy/fault datasets)"
    )
    parser.add_argument(
        "--with-dataset",
        action="store_true",
        help="Also write data/healthy_flights/healthy_001.parquet for BE-2",
    )
    parser.add_argument(
        "--with-faults",
        action="store_true",
        help="Generate labelled fault-run datasets in data/fault_runs/",
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
        "rho9_cyl_dev",      "rho10_oil",          "rho11_ripple",
        "rho12_coolant",     "rho13_head_temp",
    ]
    print(
        f"Sampled {raw.shape[0]} healthy points across "
        f"{len(OPERATING_POINTS)} operating points."
    )
    for label, s in zip(labels, sigma):
        print(f"  {label:20s}  sigma = {s:.5f}")
    print(f"\nWrote {out_path}")

    # Optional healthy dataset
    data_root = Path(__file__).resolve().parent.parent.parent / "data"
    if args.with_dataset:
        write_healthy_dataset(cfg, data_root / "healthy_flights")

    # Optional fault-run datasets
    if args.with_faults:
        print(f"\nGenerating fault-run datasets...")
        write_fault_dataset(cfg, data_root / "fault_runs")
        print("Done.")


if __name__ == "__main__":
    main()
