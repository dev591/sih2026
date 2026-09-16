"""
PRAMANA — sensor/transducer layer tests.

Run from the backend directory:
    python test_sensors.py

Same flat style as verify.py and gates_check.py: print, assert, non-zero exit
on failure. No pytest dependency.

The first test is the important one. While building this layer, a transcribed
NIST coefficient set was wrong by 10x on its last two terms, and the resulting
polynomial was CORRECT at 0 degC and 100 degC while reading 23.85 mV instead of
20.644 mV at 500 degC — about +75 degC of error, hidden at exactly the points
anyone would spot-check first. It happened twice, once for each thermocouple
type. Table-value regression is the only thing that catches that.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from twin.measurement import (          # noqa: E402
    SENSOR_SPEC,
    MeasurementModel,
    ip_mA_to_lambda,
    lambda_to_ip_mA,
    ntc_resistance_ohm,
    ntc_temp_C,
    quantise,
    tc_emf_mV,
    tc_temp_C,
)

# NIST ITS-90 published table values, mV. The regression anchor.
_TYPE_K_TABLE = [
    (0, 0.000), (100, 4.096), (200, 8.138), (300, 12.209), (400, 16.397),
    (500, 20.644), (600, 24.905), (700, 29.129), (800, 33.275),
    (900, 37.326), (1000, 41.276), (1372, 54.886),
]
_TYPE_J_TABLE = [
    (0, 0.000), (100, 5.269), (200, 10.779), (300, 16.327), (400, 21.848),
    (500, 27.393), (600, 33.102), (760, 42.919),
]


def _phys(egt=720.0, cht=140.0, iat_K=318.0, n_cyl=4, rpm=3580.0):
    """A plausible plant-output dict, shaped like MVEM.get_outputs()."""
    return {
        "rpm": rpm,
        "map_hPa": 1400.0,
        "iat_K": iat_K,
        "cht_C": [cht] * n_cyl,
        "egt_C": [egt] * n_cyl,
        "oil_press_bar": 3.4,
        "oil_temp_C": 96.0,
        "fuel_flow_total": 0.0041,
        "lambda_val": 1.42,
        "turbo_rpm": 103000.0,
        "ripple": 0.004,
        "air_mass_flow": 0.089,
        "fuel_cmd_per_cyl": 1.0e-5,
        "brake_power_kW": 81.5,
        # Compressor inlet conditions — parity Path 2's own sensors.
        "p_amb_hPa": 506.0,      # ~18,000 ft
        "oat_K": 252.0,
        # Air data and drivetrain.
        "tas_mps": 61.2,
        "prop_rpm": 2123.0,
        "blade_angle_deg": 26.0,
        "gearbox_oil_C": 95.0,
        # Cooling and charge air.
        "coolant_temp_C": 88.0,
        "comp_out_T_K": 398.0,
        "comp_out_p_hPa": 1421.0,
    }


def test_reference_functions():
    """The regression test for the 10x transcription trap. Do not delete."""
    print("Test 1 — thermocouple reference functions vs NIST tables...")
    for kind, table in (("type_k", _TYPE_K_TABLE), ("type_j", _TYPE_J_TABLE)):
        for temp_C, ref_mV in table:
            got = tc_emf_mV(temp_C, kind)
            assert abs(got - ref_mV) < 1e-3, (
                f"{kind} at {temp_C} degC: got {got:.4f} mV, table says {ref_mV:.3f} mV. "
                "Check the coefficient transcription — NIST prints 0.9715E-22, "
                "which is 9.715e-23, NOT 9.715e-22."
            )
    print("  PASSED")


def test_inversion_round_trip():
    print("Test 2 — EMF inversion round-trips...")
    for kind, hi in (("type_k", 1372), ("type_j", 760)):
        for temp_C in range(0, hi, 37):
            back = tc_temp_C(tc_emf_mV(temp_C, kind), kind, guess_C=temp_C)
            assert abs(back - temp_C) < 1e-4, f"{kind} {temp_C}: round-trip {back}"
            # The unseeded bisection path must agree with the seeded Newton one.
            cold = tc_temp_C(tc_emf_mV(temp_C, kind), kind)
            assert abs(cold - temp_C) < 1e-3, f"{kind} {temp_C}: bisection {cold}"
    print("  PASSED")


def test_step_response_matches_spec():
    """
    A step input must decay toward the target with the tau declared in
    sensors.yaml. Checks the value after exactly one tau is ~63.2% of the way.
    """
    print("Test 3 — step response matches the declared tau...")
    spec_tau = SENSOR_SPEC["channels"]["oil_temp_C"]["tau_s"]

    m = MeasurementModel(seed=3)
    m.measure(_phys(), add_noise=False, dt=1.0)          # settle at 96 degC
    start = m.measure(_phys(), add_noise=False, dt=1.0)["oil_temp_C"]

    target = 130.0
    step = _phys()
    step["oil_temp_C"] = target
    # One tau of elapsed time, in small increments so the discretisation of the
    # exponential does not itself dominate.
    n = 40
    for _ in range(n):
        out = m.measure(step, add_noise=False, dt=spec_tau / n)["oil_temp_C"]

    frac = (out - start) / (target - start)
    assert 0.60 < frac < 0.67, (
        f"After one tau ({spec_tau}s) expected ~63.2% of the step, got {frac:.1%}"
    )
    print(f"  after one tau: {frac:.1%} of the step (expected ~63.2%)")
    print("  PASSED")


def test_first_sample_initialises():
    """
    A call site that constructs a model and measures ONCE must get the
    unlagged value — several tests do exactly that, and a filter starting from
    zero would silently return garbage.
    """
    print("Test 4 — first sample initialises the filter, no lag...")
    once = MeasurementModel(seed=5, is_twin=True).measure(_phys(), add_noise=False)
    assert abs(once["oil_temp_C"] - 96.0) < 2.0, (
        f"One-shot measure should read ~96 degC, got {once['oil_temp_C']:.2f}"
    )
    assert abs(once["egt_C"][0] - 720.0) < 3.0, (
        f"One-shot EGT should read ~720 degC, got {once['egt_C'][0]:.2f}"
    )
    print("  PASSED")


def test_probe_offsets_persist_and_twin_has_none():
    """
    Tolerance is a FIXED per-probe offset, not resampled noise. If it were
    resampled, the cylinder-to-cylinder spread would average away and
    rho6-rho9 would sit at an unrealistically clean zero.
    """
    print("Test 5 — per-probe offsets persist; twin carries none...")
    plant = MeasurementModel(seed=42)
    a = plant.measure(_phys(), add_noise=False, dt=1.0)["egt_C"]
    b = plant.measure(_phys(), add_noise=False, dt=1.0)["egt_C"]
    assert a == b, "Offsets must not be redrawn between samples"

    spread = max(a) - min(a)
    assert spread > 1e-6, "Plant must show a cylinder-to-cylinder spread"

    twin = MeasurementModel(seed=42, is_twin=True)
    t = twin.measure(_phys(), add_noise=False, dt=1.0)["egt_C"]
    assert max(t) - min(t) < 1e-6, (
        f"Twin must not invent per-probe offsets it cannot know; spread {max(t)-min(t)}"
    )
    print(f"  plant spread {spread:.3f} degC, twin spread 0.000 degC")
    print("  PASSED")


def test_lambda_validity_gating():
    """A cold UEGO reads NOTHING, not a wrong number."""
    print("Test 6 — lambda is invalid until the heater lights off...")
    light_off = SENSOR_SPEC["channels"]["lambda_val"]["heater"]["light_off_s"]

    cold = MeasurementModel(seed=7, cold_start=True)
    first = cold.measure(_phys(), add_noise=False, dt=1.0)["lambda_val"]
    assert first is None, f"Cold UEGO must read None, got {first}"

    for _ in range(int(light_off) + 2):
        out = cold.measure(_phys(), add_noise=False, dt=1.0)["lambda_val"]
    assert out is not None, "UEGO must become valid after light-off"
    assert abs(out - 1.42) < 0.05, f"Post-light-off lambda off: {out}"

    # The plant starts at cruise, so the default must be valid immediately.
    warm = MeasurementModel(seed=7).measure(_phys(), add_noise=False, dt=1.0)
    assert warm["lambda_val"] is not None, "Default (warm) start must read lambda"
    print("  PASSED")


def test_lsu_characteristic():
    print("Test 7 — LSU 4.9 Ip/lambda round-trip incl. the lean region...")
    for lam in (0.75, 0.9, 1.0, 1.1, 1.5, 2.4, 3.4, 5.4, 10.0):
        back = ip_mA_to_lambda(lambda_to_ip_mA(lam))
        assert abs(back - lam) < 0.02, f"lambda {lam} -> {back}"
    # Monotonic, and genuinely non-linear out lean.
    assert lambda_to_ip_mA(5.4) > lambda_to_ip_mA(2.4) > lambda_to_ip_mA(1.0)
    d_rich = lambda_to_ip_mA(1.1) - lambda_to_ip_mA(1.0)
    d_lean = lambda_to_ip_mA(5.4) - lambda_to_ip_mA(3.4)
    assert d_lean / max(d_rich, 1e-9) < 10.0, "Lean sensitivity should COLLAPSE, not grow"
    print("  PASSED")


def test_ntc_round_trip():
    print("Test 8 — NTC resistance round-trip...")
    for t in (-20, 0, 25, 60, 95, 140):
        assert abs(ntc_temp_C(ntc_resistance_ohm(t)) - t) < 1e-6
    # Resistance must fall with temperature, which is what compresses
    # resolution at the hot end.
    assert ntc_resistance_ohm(140) < ntc_resistance_ohm(25) < ntc_resistance_ohm(-20)
    print("  PASSED")


def test_quantisation_lands_on_grid():
    print("Test 9 — quantisation lands on ADC levels...")
    bits = SENSOR_SPEC["acquisition"]["adc_bits"]
    levels = 2 ** bits
    lo, hi = 0.0, 100.0
    step = (hi - lo) / (levels - 1)
    for raw in (0.0, 1.234567, 50.5, 99.9999, 100.0):
        q = quantise(raw, lo, hi, levels)
        k = (q - lo) / step
        assert abs(k - round(k)) < 1e-6, f"{raw} -> {q} is not on the grid"
        assert abs(q - raw) <= step, f"{raw} -> {q} moved more than one LSB"
    print(f"  {bits}-bit, LSB = {step:.6g} over [{lo}, {hi}]")
    print("  PASSED")


def test_rpm_is_tooth_quantised():
    """
    A VR pickup on a 60-2 wheel updates 58 times a revolution, so reported rpm
    is discrete. This is the professor's point about sensor type changing the
    equation, in its most literal form.
    """
    print("Test 10 — rpm is tooth-quantised, not continuous...")
    wheel = SENSOR_SPEC["channels"]["rpm"]["wheel"]
    updates = wheel["updates_per_rev"]
    m = MeasurementModel(seed=11, is_twin=True)
    seen = set()
    for rpm in [3580.0 + i * 0.05 for i in range(40)]:
        out = m.measure(_phys(rpm=rpm), add_noise=False, dt=1.0)["rpm"]
        seen.add(round(out, 6))
    assert len(seen) < 40, (
        f"Sweeping rpm in 0.05 steps produced {len(seen)} distinct values — "
        "quantisation is not being applied"
    )
    grid = 60.0 / updates
    for v in seen:
        k = v / grid
        assert abs(k - round(k)) < 1e-6, f"{v} rpm is not on the {grid:.4f} rpm grid"
    print(f"  58 updates/rev -> {grid:.4f} rpm grid; 40 inputs collapsed to {len(seen)} values")
    print("  PASSED")


def test_twin_and_plant_agree_when_physics_agrees():
    """
    The deterministic part of the sensor (lag, quantisation) must be modelled
    on BOTH sides. If only the plant were lagged, every transient would create
    a residual out of nothing and the detector would false-alarm on throttle
    and altitude changes.
    """
    print("Test 11 — identical physics gives centred residuals through a transient...")
    plant = MeasurementModel(seed=42)
    twin = MeasurementModel(seed=999, is_twin=True)

    # A 120 degC EGT ramp, which is far faster than anything in the mission.
    worst = 0.0
    for i in range(30):
        p = _phys(egt=720.0 + 4.0 * i)
        mp = plant.measure(p, add_noise=False, dt=1.0)
        mt = twin.measure(p, add_noise=False, dt=1.0)
        gap = abs(mp["egt_C"][0] - mt["egt_C"][0])
        worst = max(worst, gap)

    tol = SENSOR_SPEC["channels"]["egt_C"]["tolerance_degC"]
    assert worst < tol + 1.0, (
        f"Plant and twin diverged by {worst:.3f} degC through a ramp; only the "
        "fixed per-probe offset should separate them, not the lag"
    )
    print(f"  worst plant/twin gap through the ramp: {worst:.3f} degC "
          f"(probe tolerance is {tol} degC)")
    print("  PASSED")


def test_drivetrain_channels():
    """
    The four channels added with the gearbox and constant-speed propeller:
    fixed tolerances belong to the plant only, and a profile without a pitch
    or gearbox-oil sensor reads None rather than a fabricated number.
    """
    print("Test 12 — air-data, prop-speed, blade-angle and gearbox-oil channels...")
    ch = SENSOR_SPEC["channels"]
    plant = MeasurementModel(seed=21).measure(_phys(), add_noise=False)
    twin = MeasurementModel(seed=21, is_twin=True).measure(_phys(), add_noise=False)

    assert abs(plant["blade_angle_deg"] - 26.0) <= ch["blade_angle_deg"]["tolerance_abs"] + 1e-9
    assert abs(plant["tas_mps"] - 61.2) <= ch["tas_mps"]["tolerance_abs"] + 1e-9
    assert twin["blade_angle_deg"] == 26.0, "Twin must carry no blade-angle offset"
    assert twin["tas_mps"] == 61.2, "Twin must carry no air-data offset"
    assert abs(twin["gearbox_oil_C"] - 95.0) < 1.0, (
        f"Gearbox oil NTC should read ~95 degC, got {twin['gearbox_oil_C']:.2f}"
    )

    # A commissioned plant probe keeps only the calibration reference's own
    # error — checked across many probe draws, not one lucky seed.
    for name, nominal in (("blade_angle_deg", 26.0), ("tas_mps", 61.2)):
        r = ch[name]["commissioning_calibration"]["residual_error_abs"]
        worst = max(
            abs(MeasurementModel(seed=s).measure(_phys(), add_noise=False)[name] - nominal)
            for s in range(40)
        )
        assert worst <= r + 1e-9, f"{name}: post-calibration offset {worst:.3f} > {r}"
        assert worst > 1e-6, f"{name}: calibration must leave the reference's error, not zero"

    # Coolant/charge-air channels, and the null path for a profile with no loop.
    assert abs(twin["coolant_temp_C"] - 88.0) < 1.0, (
        f"Coolant NTC should read ~88 degC, got {twin['coolant_temp_C']:.2f}"
    )
    assert abs(twin["comp_out_T_K"] - 398.0) < 1.0
    assert abs(twin["comp_out_p_hPa"] - 1421.0) < 2.0
    no_loop = _phys()
    no_loop["coolant_temp_C"] = None
    assert MeasurementModel(seed=21).measure(no_loop, add_noise=False)["coolant_temp_C"] is None

    fixed = _phys()
    fixed["blade_angle_deg"] = None
    fixed["gearbox_oil_C"] = None
    out = MeasurementModel(seed=21).measure(fixed, add_noise=False)
    assert out["blade_angle_deg"] is None and out["gearbox_oil_C"] is None, (
        "A channel with no sensor must read None, not a number"
    )
    print("  PASSED")


if __name__ == "__main__":
    print("=" * 70)
    print("PRAMANA sensor layer tests")
    print("=" * 70)
    test_reference_functions()
    test_inversion_round_trip()
    test_step_response_matches_spec()
    test_first_sample_initialises()
    test_probe_offsets_persist_and_twin_has_none()
    test_lambda_validity_gating()
    test_lsu_characteristic()
    test_ntc_round_trip()
    test_quantisation_lands_on_grid()
    test_rpm_is_tooth_quantised()
    test_twin_and_plant_agree_when_physics_agrees()
    test_drivetrain_channels()
    print()
    print("ALL SENSOR TESTS PASSED.")
