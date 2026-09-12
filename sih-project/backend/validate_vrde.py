"""Report the VRDE model against the public DRDO validation anchors.

This is deliberately a report, rather than an acceptance gate: the public
source supplies two power-hold endpoints and confirmation of a high-altitude
trial, but no dynamometer trace, uncertainty band, ambient condition, or power
measurement at 17,664 ft.  A pass/fail tolerance would therefore be invented.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from twin.atmosphere import isa
from twin.mvem import MVEM
from twin.profiles import load_engine_profile


def steady_outputs(cfg, altitude_ft, isa_offset_K=0.0, seconds=180):
    plant = MVEM(cfg)
    atmosphere = isa(float(altitude_ft), isa_offset_K)
    nominal = {
        "cd_inj": [1.0] * cfg["geometry"]["cylinders"],
        "eta_v_scale": 1.0,
        "eta_c_scale": 1.0,
        "hA_scale": 1.0,
        "f_fric_scale": 1.0,
    }
    for _ in range(seconds):
        plant.step(1.0, nominal, atmosphere, 100.0)
    return plant.get_outputs()


def main():
    cfg = load_engine_profile("engine_vrde_180.yaml")
    anchors = cfg["validation_anchors"]
    target_hp = anchors["power_hold"]["target_hp"]

    print("VRDE published-anchor validation report")
    print(f"Source: {anchors['source']}")
    print(f"Power-hold observation: {target_hp:.0f} hp through "
          f"{max(anchors['power_hold']['altitude_ft']):,} ft")
    print()
    print("altitude   model power   published target   signed error")
    for altitude_ft in anchors["power_hold"]["altitude_ft"]:
        out = steady_outputs(cfg, altitude_ft)
        model_hp = out["brake_power_kW"] / 0.745699872
        error_pct = 100.0 * (model_hp - target_hp) / target_hp
        print(f"{altitude_ft:>6,} ft   {model_hp:>8.1f} hp    "
              f"{target_hp:>8.1f} hp       {error_pct:>+6.1f}%")

    trial = anchors["high_altitude_trial"]
    # ISA+20 is a scenario assumption from environment_presets, not DRDO data.
    hot_high = cfg["environment_presets"]["leh_hot_high"]
    out = steady_outputs(cfg, trial["altitude_ft"], hot_high["isa_offset_K"])
    print()
    print(f"High-altitude trial: {trial['altitude_ft']:,} ft")
    print(f"  model scenario: ISA+{hot_high['isa_offset_K']:.0f} K, "
          f"{out['brake_power_kW'] / 0.745699872:.1f} hp, "
          f"MAP {out['map_hPa'] / 1000.0:.2f} bar")
    print("  DRDO confirms high-altitude validation but publishes no power there;")
    print("  this is an operating-point report, not a quantitative validation.")
    print()
    print("Status: calibration gap identified. Obtain a VRDE dynamometer curve or")
    print("test-cell data before fitting compressor, fuel, or combustion parameters.")

    surrogate = anchors["comparable_engine_surrogate"]
    print()
    print(f"Comparable-engine check — {surrogate['name']} (not VRDE data)")
    print(f"Architecture: {surrogate['architecture']}")
    points = surrogate["altitude_power_hp"]
    results = []
    for point in points:
        out = steady_outputs(cfg, point["altitude_ft"])
        results.append((point["altitude_ft"], out["brake_power_kW"] / 0.745699872,
                        point["power_hp"]))
    model_base, reference_base = results[0][1:]
    print("altitude   model power   TEI power   absolute ratio   normalized lapse")
    for altitude_ft, model_hp, reference_hp in results:
        ratio = model_hp / reference_hp
        model_normalized = model_hp / model_base
        reference_normalized = reference_hp / reference_base
        print(f"{altitude_ft:>6,} ft   {model_hp:>8.1f} hp    "
              f"{reference_hp:>7.1f} hp     {ratio:>6.2%}       "
              f"{model_normalized:>5.1%} / {reference_normalized:>5.1%}")
    print(f"TEI published sea-level BSFC: {surrogate['bsfc_g_per_kWh_msl']:.0f} g/kWh")
    print("Use only as a model-form plausibility check; it is not a VRDE fit.")


if __name__ == "__main__":
    main()
