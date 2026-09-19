"""
Phase 6, step 1: air-side-only compressor fit against Gates 1-3.

Scope, deliberately narrow: after swapping impeller_diameter_m to the
GT1749V-sourced 49mm and deriving n_corr_design_rpm=168,600 from N~1/D turbo
similarity, psi_max_design/phi_max_design (1.8 / 0.095) are leftover numbers
that were only ever validated in combination with the OLD (wrong, 70mm)
diameter -- pairing them with the new, correctly-derived design speed produced
an internally-inconsistent map (Gates 1 and 3 newly failed: MAP 0.834 bar,
50% rated power, both at 18kft/72% cruise).

2026-09-19 update: the gate reference condition itself moved from 18,000 ft
(an unsourced demo point) to 11,000 ft (DRDO's published VRDE critical
altitude) -- see gates_check.py's module docstring. The 18kft target was
physically unreachable for any real single-stage turbo in this class (needed
PR~2.7); the provisional fit against it (psi=1.86, phi=0.098, n_corr pinned
at its lower bound) is discarded and superseded by this run against the
corrected 11kft/72% target (needs only PR~2.04).

This script fits psi_max_design and phi_max_design jointly with
n_corr_design_rpm to find a coherent, self-consistent 49mm-class compressor
map -- bounded so the result stays "same compressor family", not an
arbitrary shape:
  - psi_max_design, phi_max_design: +-30% of their current values (loose
    enough to find a real fit, tight enough that they cannot degenerate into
    fitting Gate 1/3 by brute force)
  - n_corr_design_rpm: +-5% of the analytically-derived 168,600 (that number
    is physically grounded via similarity, not fitted; the small band just
    absorbs the fact that 168,600 came from rounding 70/49)

Deliberately does NOT touch fuel-side parameters (rated_fuel_flow_kgps,
eta_i_nominal) or the wastegate MAP target. Fitting those now would risk the
Kennedy-O'Hagan failure mode: a fuel-side residual getting silently absorbed
by distorting the compressor map instead of being visible as a fuel-side gap.
Gates 1-3 only; Gates 4-6 are untouched by this fit (4/5 are fault-detection
residuals, 6 is a full-power altitude sweep the fuel-side fit will govern).

Run: python3 calibrate_compressor.py
"""
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent))

from twin.profiles import load_engine_profile
from twin.mvem import MVEM
from twin.atmosphere import isa
from gates_check import run_to_steady_state, cruise_rpm_target, ALT_FT, THROTTLE

PSI0, PHI0, NCORR0 = 1.8, 0.095, 168_600.0
BOUNDS = [
    (PSI0 * 0.70, PSI0 * 1.30),
    (PHI0 * 0.70, PHI0 * 1.30),
    (NCORR0 * 0.95, NCORR0 * 1.05),
]

MAP_LO, MAP_HI = 0.9, 1.4          # bar, Gate 1
TURBO_LO, TURBO_HI = 114_000.0, 172_000.0  # rpm, Gate 2 (upper is the mechanical clamp)
POWER_MIN = 0.50                   # fraction rated, Gate 3


def hinge(x, lo=None, hi=None):
    pen = 0.0
    if lo is not None and x < lo:
        pen += (lo - x) ** 2
    if hi is not None and x > hi:
        pen += (x - hi) ** 2
    return pen


def evaluate(params, cfg_base):
    psi, phi, ncorr = params
    cfg = load_engine_profile("engine_vrde_180.yaml")
    comp = cfg["mvem"]["compressor"]
    comp["psi_max_design"] = float(psi)
    comp["phi_max_design"] = float(phi)
    comp["n_corr_design_rpm"] = float(ncorr)

    rated_kW = cfg["ratings"]["rated_power_kW"]
    plant, atm = run_to_steady_state(cfg, ALT_FT, THROTTLE)
    out = plant.get_outputs()

    p_im_bar = out["map_hPa"] * 100.0 / 1e5
    turbo_rpm = out["turbo_rpm"]
    power_frac = out["brake_power_kW"] / rated_kW

    return p_im_bar, turbo_rpm, power_frac


def loss(params, cfg_base):
    p_im_bar, turbo_rpm, power_frac = evaluate(params, cfg_base)
    # normalize each penalty to a comparable scale so no single gate dominates
    l_map = hinge(p_im_bar, MAP_LO, MAP_HI) / (0.5 ** 2)
    # strictly below the clamp, not just "not yet exceeding it" -- pinned
    # exactly at TURBO_HI is Gate 2's failure mode and must be penalized
    l_turbo = hinge(turbo_rpm, TURBO_LO, TURBO_HI - 1000.0) / (30_000.0 ** 2)
    l_power = hinge(power_frac, POWER_MIN, None) / (0.15 ** 2)
    return l_map + l_turbo + l_power


def main():
    cfg_base = load_engine_profile("engine_vrde_180.yaml")
    x0 = np.array([PSI0, PHI0, NCORR0])

    print("Baseline (leftover psi/phi + derived n_corr):")
    p_im, trpm, pfrac = evaluate(x0, cfg_base)
    print(f"  MAP={p_im:.3f} bar  turbo={trpm:,.0f} rpm  power={pfrac*100:.1f}% rated")
    print(f"  loss={loss(x0, cfg_base):.4f}")

    res = minimize(
        loss, x0, args=(cfg_base,), method="Nelder-Mead", bounds=BOUNDS,
        options={"xatol": 1e-3, "fatol": 1e-4, "maxiter": 200, "adaptive": True},
    )

    psi, phi, ncorr = res.x
    print("\nFit result:")
    print(f"  psi_max_design   = {psi:.4f}  (was {PSI0}, bound [{BOUNDS[0][0]:.3f}, {BOUNDS[0][1]:.3f}])")
    print(f"  phi_max_design   = {phi:.5f}  (was {PHI0}, bound [{BOUNDS[1][0]:.4f}, {BOUNDS[1][1]:.4f}])")
    print(f"  n_corr_design_rpm = {ncorr:,.0f}  (was {NCORR0:,.0f}, bound [{BOUNDS[2][0]:,.0f}, {BOUNDS[2][1]:,.0f}])")
    print(f"  final loss = {res.fun:.4f}  (success={res.success})")

    p_im, trpm, pfrac = evaluate(res.x, cfg_base)
    print(f"\n  MAP={p_im:.3f} bar  target [{MAP_LO}, {MAP_HI}]  {'PASS' if MAP_LO<=p_im<=MAP_HI else 'FAIL'}")
    print(f"  turbo={trpm:,.0f} rpm  target [{TURBO_LO:,.0f}, {TURBO_HI:,.0f})  {'PASS' if TURBO_LO<=trpm<TURBO_HI else 'FAIL'}")
    print(f"  power={pfrac*100:.1f}% rated  target >={POWER_MIN*100:.0f}%  {'PASS' if pfrac>=POWER_MIN else 'FAIL'}")


if __name__ == "__main__":
    main()
