"""Report the VRDE model against every public anchor, each labelled by kind.

A report, not an acceptance gate. Anchors, in order of authority:
  1. DRDO product page — 180 hp constant to 11,000 ft (published observation).
  2. VRDE 2015 EOI — power bands at 0/10k/20k/30k ft and 210 g/kWh SFC
     (published REQUIREMENT, for a 200 hp programme: compared as lapse).
  3. Austro E4P / AE330 — fuel flow at 100 % and 60 % power
     (published, but a DIFFERENT engine: model-form check only).
  4. TEI PD180ST — altitude power (secondary comparable).
Sources: docs/research/VRDE-PUBLIC-DOSSIER.md.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from twin.atmosphere import isa
from twin.mvem import MVEM
from twin.profiles import load_engine_profile

KW_PER_HP = 0.745699872


def steady_outputs(cfg, altitude_ft, isa_offset_K=0.0, throttle_pct=100.0, seconds=180):
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
        plant.step(1.0, nominal, atmosphere, throttle_pct)
    return plant.get_outputs()


def hp(out):
    return out["brake_power_kW"] / KW_PER_HP


def bsfc_g_per_kWh(out):
    return out["fuel_flow_total"] * 3.6e6 / max(out["brake_power_kW"], 1e-6)


def band_mark(value, lo, hi):
    if lo <= value <= hi:
        return "inside"
    return f"{100.0 * (value - (lo if value < lo else hi)) / (lo if value < lo else hi):+.1f}% outside"


def report_drdo(cfg):
    anchors = cfg["validation_anchors"]
    target_hp = anchors["power_hold"]["target_hp"]
    print("1. DRDO product page — published observation")
    print(f"   {target_hp:.0f} hp constant through {max(anchors['power_hold']['altitude_ft']):,} ft")
    print("   altitude   model power   target     signed error")
    for altitude_ft in anchors["power_hold"]["altitude_ft"]:
        model_hp = hp(steady_outputs(cfg, altitude_ft))
        print(f"   {altitude_ft:>6,} ft   {model_hp:>8.1f} hp   {target_hp:>6.1f} hp   "
              f"{100.0 * (model_hp - target_hp) / target_hp:>+6.1f}%")

    trial = anchors["high_altitude_trial"]
    hot_high = cfg["environment_presets"]["leh_hot_high"]
    out = steady_outputs(cfg, trial["altitude_ft"], hot_high["isa_offset_K"])
    print(f"   Leh/Chang La {trial['altitude_ft']:,} ft, ISA+{hot_high['isa_offset_K']:.0f} K "
          f"(scenario assumption): {hp(out):.1f} hp, MAP {out['map_hPa'] / 1000.0:.2f} bar")
    print("   DRDO publishes no power there: operating point only.")


def report_eoi(cfg):
    eoi = cfg["validation_anchors"]["vrde_eoi_2015"]
    bands = eoi["power_bands_hp"]
    print()
    print("2. VRDE 2015 EOI — published REQUIREMENT (200 hp programme)")
    results = [(b, steady_outputs(cfg, b["altitude_ft"])) for b in bands]
    sl_band = bands[0]
    sl_model = hp(results[0][1])
    print("   altitude   model hp   EOI band      absolute       model lapse   EOI lapse band   lapse")
    for band, out in results:
        model_hp = hp(out)
        lapse = model_hp / sl_model
        lapse_lo = band["min"] / sl_band["max"]
        lapse_hi = band["max"] / sl_band["min"]
        print(f"   {band['altitude_ft']:>6,} ft   {model_hp:>6.1f}   "
              f"{band['min']:>5.0f}-{band['max']:<5.0f}   {band_mark(model_hp, band['min'], band['max']):>14}   "
              f"{lapse:>9.1%}   {lapse_lo:>6.1%}-{lapse_hi:<6.1%}   "
              f"{band_mark(lapse, lapse_lo, lapse_hi)}")
    sl_bsfc = bsfc_g_per_kWh(results[0][1])
    target = eoi["sfc_g_per_kWh_msl"]
    print(f"   sea-level BSFC: model {sl_bsfc:.0f} g/kWh vs requirement {target:.0f} g/kWh "
          f"({100.0 * (sl_bsfc - target) / target:+.1f}%)")


def report_ae330(cfg):
    ae = cfg["validation_anchors"]["comparable_engine_austro_e4p"]
    rated_kW = cfg["ratings"]["rated_power_kW"]
    rho = ae["fuel_density_kg_per_L"]
    print()
    print(f"3. {ae['name']} — published, DIFFERENT engine (form check only)")
    print("   power frac   AE330 BSFC (derived)   model BSFC @ same power frac")
    sl_full = steady_outputs(cfg, 0)
    full_kW = sl_full["brake_power_kW"]
    for point in ae["fuel_flow_L_per_h"]:
        ae_kW = ae["takeoff_power_kW"] * point["power_frac"]
        ae_bsfc = point["L_per_h"] * rho * 1000.0 / ae_kW
        target_kW = full_kW * point["power_frac"]
        out = _at_power(cfg, target_kW)
        print(f"   {point['power_frac']:>9.0%}   {ae_bsfc:>10.0f} g/kWh          "
              f"{bsfc_g_per_kWh(out):>6.0f} g/kWh ({out['brake_power_kW']:.1f} kW)")
    print(f"   (model sea-level full power {full_kW:.1f} kW vs VRDE rating {rated_kW:.1f} kW)")


def _at_power(cfg, target_kW):
    lo, hi = 20.0, 100.0
    out = steady_outputs(cfg, 0, throttle_pct=hi)
    for _ in range(12):
        mid = 0.5 * (lo + hi)
        out = steady_outputs(cfg, 0, throttle_pct=mid)
        if out["brake_power_kW"] < target_kW:
            lo = mid
        else:
            hi = mid
    return out


def report_tei(cfg):
    surrogate = cfg["validation_anchors"]["comparable_engine_surrogate"]
    print()
    print(f"4. {surrogate['name']} — secondary comparable (not VRDE data)")
    points = surrogate["altitude_power_hp"]
    results = [(p["altitude_ft"], hp(steady_outputs(cfg, p["altitude_ft"])), p["power_hp"])
               for p in points]
    model_base, reference_base = results[0][1:]
    print("   altitude   model hp   TEI hp   normalized lapse model / TEI")
    for altitude_ft, model_hp, reference_hp in results:
        print(f"   {altitude_ft:>6,} ft   {model_hp:>6.1f}   {reference_hp:>6.1f}   "
              f"{model_hp / model_base:>5.1%} / {reference_hp / reference_base:>5.1%}")


def main():
    cfg = load_engine_profile("engine_vrde_180.yaml")
    print("VRDE twin — public-anchor validation report")
    print("=" * 72)
    report_drdo(cfg)
    report_eoi(cfg)
    report_ae330(cfg)
    report_tei(cfg)


if __name__ == "__main__":
    main()
