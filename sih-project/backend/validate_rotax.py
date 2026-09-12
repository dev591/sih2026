"""Published-rating and healthy-parity validation for the Rotax 914 profile."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parity.residuals import compute_residuals
from twin.atmosphere import isa
from twin.measurement import MeasurementModel
from twin.mvem import MVEM
from twin.profiles import load_engine_profile


def nominal(cfg):
    return {
        "cd_inj": [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale": 1.0,
        "eta_c_scale": 1.0,
        "hA_scale": 1.0,
        "f_fric_scale": 1.0,
    }


def steady(cfg, altitude_ft, throttle_pct, seconds=180):
    plant = MVEM(cfg)
    params = nominal(cfg)
    atmosphere = isa(altitude_ft)
    for _ in range(seconds):
        plant.step(1.0, params, atmosphere, throttle_pct)
    return plant.get_outputs()


def main():
    cfg = load_engine_profile("engine_rotax_914.yaml")
    rating = cfg["ratings"]
    limits = cfg["limits"]
    out = steady(cfg, 0.0, 100.0)

    power_error = abs(out["brake_power_kW"] - rating["takeoff_power_kW"]) / rating["takeoff_power_kW"]
    rpm_error = abs(out["rpm"] - rating["takeoff_rpm"]) / rating["takeoff_rpm"]
    checks = [
        ("take-off power", power_error <= 0.05,
         f"{out['brake_power_kW']:.2f} kW vs {rating['takeoff_power_kW']:.1f} kW ({power_error:+.1%})"),
        ("take-off speed", rpm_error <= 0.05,
         f"{out['rpm']:.0f} rpm vs {rating['takeoff_rpm']:.0f} rpm ({rpm_error:+.1%})"),
        ("CHT", max(out["cht_C"]) <= limits["cht_C"],
         f"{max(out['cht_C']):.1f} C <= {limits['cht_C']:.0f} C"),
        ("oil temperature", out["oil_temp_C"] <= limits["oil_temp_inlet_C"],
         f"{out['oil_temp_C']:.1f} C <= {limits['oil_temp_inlet_C']:.0f} C"),
        ("oil pressure", limits["oil_pressure_bar"]["min_above_3500rpm"] <= out["oil_press_bar"] <= limits["oil_pressure_bar"]["max_above_3500rpm"],
         f"{out['oil_press_bar']:.2f} bar within [{limits['oil_pressure_bar']['min_above_3500rpm']:.1f}, {limits['oil_pressure_bar']['max_above_3500rpm']:.1f}] bar"),
    ]

    # A separate plant/twin pair confirms that the common parity machinery
    # transfers unchanged to the different engine architecture.
    atm = isa(10000.0)
    plant, twin = MVEM(cfg), MVEM(cfg)
    params = nominal(cfg)
    for _ in range(120):
        plant.step(1.0, params, atm, 72.0)
        twin.step(1.0, params, atm, 72.0)
    measured = MeasurementModel(seed=7).measure(plant.get_outputs(), add_noise=False)
    predicted = MeasurementModel(seed=8, is_twin=True).measure(twin.get_outputs(), add_noise=False)
    rho = compute_residuals(measured, predicted, cfg, sigma_vec=[1.0] * 11)
    active = [value for value in rho if value is not None]
    parity_ok = max(abs(value) for value in active) < 1e-8
    checks.append(("healthy parity transfer", parity_ok,
                   f"max |rho| = {max(abs(value) for value in active):.2e} at 10,000 ft / 72%"))

    print("Rotax 914 public-rating and cross-engine validation")
    passed = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        passed += int(ok)
    print(f"{passed}/{len(checks)} checks passed.")
    if passed != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
