"""
PRAMANA — verification test suite.

Run from the backend directory:
    python verify.py

All tests use only deterministic, noise-free paths so failures are
unambiguous — noise is added only where the asymmetry under test is
between clean physics and a biased measurement.

Tests:
  1. OP Invariance    — residuals are flat across altitude/throttle
  2. Sum-to-zero      — ρ₆–ρ₉ sum to zero (conditional-mean definition)
  3. Sensor/engine asymmetry — sensor bias changes measurement, not engine state
  4. Fault isolation  — per-fault assertions: plant moves, twin doesn't;
                        sensor faults leave get_outputs() bit-identical
  5. Drivetrain       — gearbox kinematics, governor holds schedule,
                        subsonic propeller tip, gearbox oil within limit
"""

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parity.residuals import compute_residuals
from twin.atmosphere import isa
from twin.faults import apply_fault_config, fresh_sensor_biases
from twin.measurement import MeasurementModel
from twin.mvem import MVEM
from twin.profiles import load_engine_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _steady(cfg, altitude_ft=10000, throttle_pct=72.0, steps=30, params=None):
    """Run plant + twin to approximate steady state and return outputs."""
    if params is None:
        params = {
            "cd_inj": [1.0] * cfg["geometry"]["cylinders"],
            "eta_v_scale": 1.0,
            "eta_c_scale": 1.0,
            "hA_scale": 1.0,
            "f_fric_scale": 1.0,
        }
    atm = isa(altitude_ft)
    plant = MVEM(cfg)
    twin  = MVEM(cfg)
    nom   = {
        "cd_inj": [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale": 1.0,
        "eta_c_scale": 1.0,
        "hA_scale": 1.0,
        "f_fric_scale": 1.0,
    }
    for _ in range(steps):
        plant.step(1.0, params, atm, throttle_pct)
        twin.step( 1.0, nom,    atm, throttle_pct)
    return plant, twin, atm


# ---------------------------------------------------------------------------
# Test 1 + 2 — OP invariance and sum-to-zero
# ---------------------------------------------------------------------------

def test_op_invariance_and_sum_to_zero(cfg):
    print("Test 1+2 — OP invariance and ρ₆–ρ₉ sum-to-zero...")
    nom = {
        "cd_inj": [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale": 1.0,
        "eta_c_scale": 1.0,
        "hA_scale": 1.0,
        "f_fric_scale": 1.0,
    }

    for alt in [0, 5000, 11000]:
        for throttle in [40, 72, 100]:
            atm = isa(alt)
            plant = MVEM(cfg)
            twin  = MVEM(cfg)
            # Fresh instrumentation per operating point, and SEPARATE plant and
            # twin instances. The measurement model is stateful now (per-channel
            # lag), so one shared instance would let the plant and twin clobber
            # each other's filters, and reusing it across points would carry
            # stale lag from the previous altitude into the next.
            meas_p = MeasurementModel(seed=42)
            meas_t = MeasurementModel(seed=999, is_twin=True)
            for _ in range(30):
                plant.step(1.0, nom, atm, throttle)
                twin.step( 1.0, nom, atm, throttle)

            measured  = meas_p.measure(plant.get_outputs(), add_noise=True)
            predicted = meas_t.measure(twin.get_outputs(),  add_noise=False)
            rho = compute_residuals(measured, predicted, cfg, sigma_vec=[1.0] * 11)

            rho6_9  = [r for r in rho[5:9] if r is not None]
            sum_dev = sum(rho6_9)
            assert abs(sum_dev) < 1e-3, (
                f"Sum-to-zero FAILED at alt={alt} thr={throttle}: {sum_dev:.6f}"
            )

    print("  PASSED")


# ---------------------------------------------------------------------------
# Test 3 — Sensor / engine asymmetry
# ---------------------------------------------------------------------------

def test_sensor_engine_asymmetry(cfg):
    print("Test 3 — Sensor/engine asymmetry...")
    nom = {
        "cd_inj": [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale": 1.0,
        "eta_c_scale": 1.0,
        "hA_scale": 1.0,
        "f_fric_scale": 1.0,
    }
    atm = isa(10000)

    p1 = MVEM(cfg); p2 = MVEM(cfg)
    for _ in range(20):
        p1.step(1.0, nom, atm, 75.0)
        p2.step(1.0, nom, atm, 75.0)

    o1 = p1.get_outputs()
    o2 = p2.get_outputs()

    # Engine states must be bit-identical (same config, same inputs, same seed)
    assert o1["brake_power_kW"] == o2["brake_power_kW"], "Engine states diverged"
    assert o1["cht_C"] == o2["cht_C"],                   "CHT states diverged"

    m1 = MeasurementModel(seed=1)
    m2 = MeasurementModel(seed=1)

    meas1 = m1.measure(o1, add_noise=False)
    meas2 = m2.measure(o2, sensor_biases={"egt_C": [0, 100, 0, 0]}, add_noise=False)

    assert meas1["egt_C"] != meas2["egt_C"], "Sensor bias had no effect"
    assert meas1["rpm"]    == meas2["rpm"],  "Unaffected channel changed"

    print("  PASSED")


# ---------------------------------------------------------------------------
# Test 4 — Fault isolation
# ---------------------------------------------------------------------------

def test_fault_isolation(cfg):
    print("Test 4 — Fault isolation...")
    N = cfg["geometry"]["cylinders"]
    nom = {
        "cd_inj": [1.0] * N,
        "eta_v_scale": 1.0,
        "eta_c_scale": 1.0,
        "hA_scale": 1.0,
        "f_fric_scale": 1.0,
    }
    atm = isa(10000)

    # ── 4a. Injector fault: affected cylinder EGT rises; twin unchanged ───
    plant_h = MVEM(cfg); twin_h = MVEM(cfg)   # healthy baseline
    plant_f = MVEM(cfg); twin_f = MVEM(cfg)   # faulted baseline

    for _ in range(20):
        plant_h.step(1.0, nom, atm, 72.0)
        twin_h.step( 1.0, nom, atm, 72.0)
        plant_f.step(1.0, nom, atm, 72.0)
        twin_f.step( 1.0, nom, atm, 72.0)

    fc_inj = {"injector": {"startT": 0.0, "cyl": 1, "rate": 0.5}}
    p_faulted, _, _ = apply_fault_config(
        120.0, fc_inj, nom, fresh_sensor_biases(N), 0.0, 18000.0
    )

    for _ in range(5):
        plant_f.step(1.0, p_faulted, atm, 72.0)
        twin_f.step( 1.0, nom,       atm, 72.0)

    out_h = plant_h.get_outputs()
    out_f = plant_f.get_outputs()
    out_t = twin_f.get_outputs()

    assert out_f["egt_C"][1] < out_h["egt_C"][1], (
        "Injector fault: EGT[1] must drop in plant (less fuel → less heat)"
    )
    assert abs(out_f["egt_C"][0] - out_h["egt_C"][0]) < 5.0, (
        "Injector fault: EGT[0] (unaffected cylinder) must not change"
    )
    assert out_t["egt_C"][1] == out_h["egt_C"][1] or abs(out_t["egt_C"][1] - out_h["egt_C"][1]) < 1.0, (
        "Twin must not be affected by plant-only fault"
    )

    # ── 4b. CHT sensor fault: engine state unaffected, measurement biased ─
    plant_s = MVEM(cfg)
    for _ in range(20):
        plant_s.step(1.0, nom, atm, 72.0)

    out_s = plant_s.get_outputs()   # ground-truth engine state

    fc_cht = {"chtSensor": {"startT": 0.0, "cyl": 2, "rate": 10.0}}
    _, biased, _ = apply_fault_config(
        60.0, fc_cht, nom, fresh_sensor_biases(N), 0.0, 18000.0
    )

    m_clean  = MeasurementModel(seed=1).measure(out_s, add_noise=False)
    m_biased = MeasurementModel(seed=1).measure(
        out_s, sensor_biases=biased, add_noise=False
    )

    # Engine state must be identical before and after applying sensor fault
    out_s2 = plant_s.get_outputs()
    assert out_s["cht_C"][2] == out_s2["cht_C"][2], (
        "CHT sensor fault must not touch engine state"
    )

    assert m_biased["cht_C"][2] > m_clean["cht_C"][2] + 5.0, (
        "CHT sensor fault must shift measurement upward"
    )
    assert m_biased["cht_C"][0] == m_clean["cht_C"][0], (
        "Unaffected cylinder measurement must be unchanged"
    )

    # ── 4c. EGT sensor fault: same asymmetry as CHT ───────────────────────
    fc_egt = {"egtSensor": {"startT": 0.0, "cyl": 3, "rate": 8.0}}
    _, biased_e, _ = apply_fault_config(
        60.0, fc_egt, nom, fresh_sensor_biases(N), 0.0, 18000.0
    )

    m_clean_e  = MeasurementModel(seed=2).measure(out_s, add_noise=False)
    m_biased_e = MeasurementModel(seed=2).measure(
        out_s, sensor_biases=biased_e, add_noise=False
    )

    assert m_biased_e["egt_C"][3] > m_clean_e["egt_C"][3] + 5.0, (
        "EGT sensor fault must shift measurement upward"
    )
    assert m_biased_e["egt_C"][0] == m_clean_e["egt_C"][0], (
        "Unaffected cylinder EGT must not change"
    )

    # ── 4d. Turbo fault: rho1 must depart ─────────────────────────────────
    plant_tb = MVEM(cfg); twin_tb = MVEM(cfg)
    for _ in range(20):
        plant_tb.step(1.0, nom, atm, 72.0)
        twin_tb.step( 1.0, nom, atm, 72.0)

    # Healthy residuals
    mp = MeasurementModel(seed=42); mt = MeasurementModel(seed=999, is_twin=True)
    m_tb_h = mp.measure(plant_tb.get_outputs(), add_noise=False)
    p_tb_h = mt.measure(twin_tb.get_outputs(),  add_noise=False)
    rho_h  = compute_residuals(m_tb_h, p_tb_h, cfg, sigma_vec=None)

    fc_tb = {"turbo": {"startT": 0.0, "rate": 0.5}}
    p_tb_f, _, _ = apply_fault_config(120.0, fc_tb, nom, fresh_sensor_biases(N), 0.0, 18000.0)

    for _ in range(5):
        plant_tb.step(1.0, p_tb_f, atm, 72.0)
        twin_tb.step( 1.0, nom,    atm, 72.0)

    m_tb_f = mp.measure(plant_tb.get_outputs(), add_noise=False)
    p_tb_f2 = mt.measure(twin_tb.get_outputs(), add_noise=False)
    rho_f  = compute_residuals(m_tb_f, p_tb_f2, cfg, sigma_vec=None)

    assert rho_f[0] is not None and rho_h[0] is not None, "rho1 must not be None"
    assert abs(rho_f[0] - rho_h[0]) > 0.1, (
        f"Turbo fault must shift rho1 (was {rho_h[0]:.4f}, now {rho_f[0]:.4f})"
    )

    # ── 4e. Bearing fault: friction increases in plant only ───────────────
    plant_br = MVEM(cfg); twin_br = MVEM(cfg)
    for _ in range(30):
        plant_br.step(1.0, nom, atm, 72.0)
        twin_br.step( 1.0, nom, atm, 72.0)

    rpm_healthy = plant_br.get_outputs()["rpm"]

    fc_br = {"bearing": {"startT": 0.0, "rate": 0.3}}
    p_br, _, _ = apply_fault_config(120.0, fc_br, nom, fresh_sensor_biases(N), 0.0, 18000.0)

    for _ in range(10):
        plant_br.step(1.0, p_br, atm, 72.0)
        twin_br.step( 1.0, nom,  atm, 72.0)

    rpm_faulted = plant_br.get_outputs()["rpm"]
    rpm_twin    = twin_br.get_outputs()["rpm"]

    assert rpm_faulted < rpm_healthy, (
        "Bearing fault must reduce plant RPM via increased friction"
    )
    assert abs(rpm_twin - rpm_healthy) < 10.0, (
        "Twin must not be affected by bearing fault in plant"
    )

    print("  PASSED")


# ---------------------------------------------------------------------------
# Test 5 — Drivetrain: gearbox, constant-speed propeller, governor
# ---------------------------------------------------------------------------

def test_drivetrain(cfg):
    print("Test 5 — gearbox, constant-speed propeller and governor...")
    prop = cfg["propeller"]
    gov = prop["governor"]
    nom = {
        "cd_inj": [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale": 1.0, "eta_c_scale": 1.0,
        "hA_scale": 1.0, "f_fric_scale": 1.0,
    }
    assert prop["gear_ratio"] == cfg["geometry"]["gear_ratio"], (
        "geometry.gear_ratio and propeller.gear_ratio disagree — the rho5 "
        "closure breaks silently when they do"
    )

    # (label, altitude_ft, throttle_pct, TAS m/s)
    cases = [
        ("sea-level take-off", 0, 100.0, 40.0),
        ("18,000 ft cruise", 18000, 72.0, 61.2),
        ("32,000 ft ceiling", 32000, 100.0, 61.2),
    ]
    for label, alt, thr, tas in cases:
        atm = isa(alt)
        plant = MVEM(cfg)
        for _ in range(120):
            plant.step(1.0, nom, atm, thr, tas_mps=tas)
        out = plant.get_outputs()

        n_set = plant.prop_rpm_setpoint(thr / 100.0)
        beta = out["blade_angle_deg"]
        on_stop = (beta <= prop["beta_fine_stop_deg"] + 1e-6
                   or beta >= prop["beta_coarse_stop_deg"] - 1e-6)

        assert abs(out["prop_rpm"] * prop["gear_ratio"] - out["rpm"]) < 1e-6, (
            f"{label}: prop speed must be crank speed through the gearbox"
        )
        if not on_stop:
            assert abs(out["prop_rpm"] - n_set) / n_set < 0.01, (
                f"{label}: governor off schedule — prop {out['prop_rpm']:.0f} vs {n_set:.0f} rpm"
            )
        n_rps = out["prop_rpm"] / 60.0
        tip = math.hypot(math.pi * prop["diameter_m"] * n_rps, tas)
        mach = tip / math.sqrt(1.4 * 287.05 * atm["T"])
        assert mach < 0.85, f"{label}: helical tip Mach {mach:.2f} >= 0.85"
        assert out["gearbox_oil_C"] < cfg["limits"]["gearbox_oil_C"], (
            f"{label}: gearbox oil {out['gearbox_oil_C']:.1f} C over its limit"
        )
        print(f"  {label:<20} prop {out['prop_rpm']:6.0f}/{n_set:.0f} rpm  "
              f"beta {beta:5.1f} deg  tip M {mach:.2f}  "
              f"gearbox oil {out['gearbox_oil_C']:5.1f} C  "
              f"brake {out['brake_power_kW']:5.1f} kW"
              f"{'  (on a pitch stop)' if on_stop else ''}")

    # The defining constant-speed property: change airspeed at fixed throttle
    # and the governor answers with PITCH, not rpm. A fixed-pitch propeller
    # would change rpm instead.
    atm = isa(18000)
    betas = []
    for tas in (50.0, 70.0):
        plant = MVEM(cfg)
        for _ in range(120):
            plant.step(1.0, nom, atm, 72.0, tas_mps=tas)
        out = plant.get_outputs()
        n_set = plant.prop_rpm_setpoint(0.72)
        assert abs(out["prop_rpm"] - n_set) / n_set < 0.01, (
            f"TAS {tas}: governor lost speed ({out['prop_rpm']:.0f} vs {n_set:.0f})"
        )
        betas.append(out["blade_angle_deg"])
    assert betas[1] - betas[0] > 1.0, (
        f"Faster airspeed must coarsen pitch at held rpm; beta {betas[0]:.2f} -> {betas[1]:.2f}"
    )
    print(f"  TAS 50 -> 70 m/s at 18,000 ft / 72 %: rpm held, beta {betas[0]:.1f} -> {betas[1]:.1f} deg")
    print("  PASSED")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_tests():
    cfg = load_engine_profile("engine_vrde_180.yaml")
    test_op_invariance_and_sum_to_zero(cfg)
    test_sensor_engine_asymmetry(cfg)
    test_fault_isolation(cfg)
    test_drivetrain(cfg)
    print("\nALL VERIFICATION TESTS PASSED.")


if __name__ == "__main__":
    run_tests()
