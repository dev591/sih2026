"""
PRAMANA — parity residual vector ρ ∈ ℝ¹¹.

ρ = measured − predicted (what physics says it should read).
Nominal value is zero. Departures identify fault location.

All eleven residuals are computed here. ρ₃ is returned as None when
parity_paths.intake_restriction is False (the VRDE is unthrottled; the current
Rotax telemetry also lacks the separate restriction sensors). Everything
downstream handles None gracefully.

If sigma_vec is supplied, each non-None element is divided by its healthy
σ to produce a dimensionless Z-score. The sigma floor of 0.05 prevents
channels that are structurally near-zero at healthy steady state (ρ₁, ρ₅)
from amplifying noise into very large numbers under small deviations.
"""

import math

from twin.airpath import (
    energy_closure_kw,
    indicated_power_kw,
    path1_speed_density,
    path2_compressor_map,
    propeller_power_kw,
)


def compute_residuals(
    measured: dict,
    predicted: dict,
    cfg: dict,
    sigma_vec: list | None = None,
) -> list:
    """
    Return the 11-element parity residual list.

    Elements that are structurally unavailable (ρ₃ on an unthrottled engine)
    are returned as None and are skipped during sigma normalisation.

    Parameters
    ----------
    measured   : output of MeasurementModel.measure() on the plant
    predicted  : output of MeasurementModel.measure() on the twin (noise-free)
    cfg        : engine profile dict
    sigma_vec  : 11-element list from sigma_vector.json, or None for raw units
    """
    N_cyl  = cfg["geometry"]["cylinders"]
    AFR_st = cfg["fuel"]["AFR_stoich"]

    # ── ρ₁ — speed-density vs compressor map ─────────────────────────────
    # A GENUINE parity relation: two independent estimates of one quantity,
    # from two different sensor sets, both computed from the MEASURED stream.
    #
    #   Path 1  MAP, IAT, crank speed      -> induction-side flow
    #   Path 2  turbo speed, MAP, p_amb, OAT -> compressor-side flow
    #
    # This used to be `measured["air_mass_flow"] - predicted["air_mass_flow"]`,
    # i.e. the plant's flow minus the twin's — the SAME estimator on two models.
    # With plant and twin on identical nominal params that is zero by
    # construction, which is why rho1 was a dead channel in jitter_report.json.
    # The design PDF (§5.1) specifies rho1 = m_aSD - m_aC and warns that "an
    # evaluator familiar with parity-space methods will check"; this is that.
    #
    # Both paths use NOMINAL model constants, so a degradation shows up as
    # disagreement instead of being absorbed.
    m_sd   = path1_speed_density(
        cfg, measured["map_hPa"], measured["iat_K"], measured["rpm"]
    )
    # Compressor delivery pressure is a real, separate sensor ONLY where the
    # installation has an intercooler between the compressor and the manifold.
    # Without one (the Rotax transfer profile) delivery pressure IS manifold
    # pressure, and feeding that channel in would inject its transducer's own
    # offset into rho1 for no physical reason — measured: it moved Rotax's
    # healthy parity transfer from 4.4e-11 to 7.2e-03.
    has_intercooler = bool(cfg.get("mvem", {}).get("cooling", {}).get("intercooler"))
    m_comp = path2_compressor_map(
        cfg, measured["turbo_rpm"], measured["map_hPa"],
        measured["p_amb_hPa"], measured["oat_K"],
        measured.get("comp_out_p_hPa") if has_intercooler else None,
    )
    # Normalise on the induction-side estimate: it is the better-conditioned of
    # the two (the compressor estimate collapses toward zero past the ellipse,
    # and dividing by it would blow the residual up at exactly the surge
    # condition we most want to read).
    p_m_a  = max(m_sd, 1e-6)
    rho1   = (m_sd - m_comp) / p_m_a          # dimensionless fraction

    # ── ρ₂ — speed-density vs fuel/λ path ────────────────────────────────
    # ṁ_λ = λ · AFR_st · ṁ_f  (Path 3 in the spec)
    m_f_total = measured["fuel_cmd_per_cyl"] * N_cyl
    m_lambda  = measured["lambda_val"] * AFR_st * m_f_total
    rho2      = (m_sd - m_lambda) / p_m_a     # dimensionless fraction

    # ── ρ₃ — speed-density vs intake restriction ──────────────────────────
    # Unavailable on the VRDE (unthrottled FADEC diesel, no Path 4 sensor).
    # Returning None rather than fabricating a number that would look like
    # signal but carry no independent information.
    if cfg.get("parity_paths", {}).get("intake_restriction", False):
        raise NotImplementedError(
            "parity_paths.intake_restriction is set but Path 4 (compressible-"
            "orifice relation) is not implemented.  Do not fabricate ρ₃ by "
            "reusing another path's estimate — see residual-spec.md §1."
        )
    rho3 = None

    # ── ρ₄ — energy closure (first law) ──────────────────────────────────
    # A GENUINE conservation-law check, per design PDF Eq. 12: chemical power
    # in equals useful work out plus every loss path. UNLIKE rho1/rho5, this
    # is not two independent estimates of one quantity — it is a single
    # physical law checked against ONE stream of MEASURED data. No twin
    # comparison needed or wanted: "In a correct model rho4/(mdot_f*Q_LHV)
    # sits within a few per cent across the envelope" is a statement about the
    # measurement closing on itself.
    #
    # This was instead `-fuel_gap*10.0 + cht_gap*0.42` — a hand-weighted blend
    # of two PLANT-VS-TWIN gaps, which is neither the first law nor checkable
    # against the design's own "within a few percent" criterion (a plant/twin
    # gap on identical nominal params is zero by construction — same disease
    # rho1 and rho5 had).
    # Propeller-side inputs. A constant-speed profile senses TAS (air data),
    # prop speed and blade angle; the fixed-pitch Rotax transfer profile keeps
    # its commissioned configuration (assumed TAS, crank speed / gear ratio) so
    # its committed healthy baseline stays valid.
    constant_speed = cfg["propeller"].get("type") == "constant_speed"
    tas_in   = measured["tas_mps"] if constant_speed else cfg["propeller"]["assumed_tas_mps"]
    beta_in  = measured["blade_angle_deg"] if constant_speed else None
    nprop_in = measured["prop_rpm"] if constant_speed else None
    eta_gb   = cfg.get("mvem", {}).get("gearbox", {}).get("efficiency", 1.0)

    imbalance_kw = energy_closure_kw(
        cfg, measured["fuel_flow_kgps"], measured["rpm"],
        measured["map_hPa"], measured["iat_K"],
        measured["egt_C"], measured["cht_C"],
        measured["p_amb_hPa"], measured["oat_K"],
        tas_in, beta_in, nprop_in,
        measured.get("coolant_temp_C"),
    )
    chem_power_kw = measured["fuel_flow_kgps"] * cfg["fuel"]["Q_LHV_J_per_kg"] / 1000.0
    rho4 = imbalance_kw / max(chem_power_kw, 1e-6)

    # ── ρ₅ — power closure via propeller dynamometer ─────────────────────
    # A GENUINE parity relation, same fix as ρ₁: two independent estimates of
    # shaft power, both from the MEASURED stream.
    #
    #   Path A  fuel flow, crank speed          -> indicated power (combustion)
    #   Path B  crank speed, p_amb, OAT, TAS    -> propeller dynamometer
    #
    # This used to be `measured["brake_power_kW"] - predicted["brake_power_kW"]`
    # — the plant's torque-balance power minus the twin's. At steady state the
    # crank's own equilibrium makes T_ind - T_fric - T_pump equal T_load =
    # P_prop/w BY DEFINITION, so brake_power_kW already secretly equals the
    # propeller-absorbed power and comparing plant to twin there measures
    # nothing about which SIDE disagrees. config/engine_vrde_180.yaml's
    # propeller block has always said this residual is meant to be
    # "P_indicated - P_prop" — it names the two paths that were never built.
    #
    # Both use NOMINAL model constants (eta_i, friction coefficient), so a
    # bearing-wear fault (raised f_fric_scale in the PLANT only) makes Path A
    # overestimate power rather than being absorbed into the estimate.
    #
    # Path B is referred to the crank through the nominal gearbox efficiency.
    # On the constant-speed VRDE profile it runs on its own sensors — air-data
    # TAS, the prop-speed pickup and blade-angle feedback — none of which Path A
    # uses, so a friction fault (crank side) and a propeller-side disagreement
    # pull the two paths apart in opposite directions.
    p_ind  = indicated_power_kw(cfg, measured["fuel_flow_kgps"], measured["rpm"])
    p_prop = propeller_power_kw(
        cfg, measured["rpm"], measured["p_amb_hPa"], measured["oat_K"],
        tas_in, beta_in, nprop_in,
    ) / eta_gb
    p_pwr = max(p_ind, 1e-6)
    rho5  = (p_ind - p_prop) / p_pwr

    # ── ρ₆–ρ₉ — per-cylinder thermal deviation (conditional mean) ────────
    # Each is the deviation of cylinder i from the cross-cylinder mean.
    # Sum-to-zero is a mathematical identity of the definition.
    cht_mean = sum(measured["cht_C"]) / N_cyl
    egt_mean = sum(measured["egt_C"]) / N_cyl
    rho6_9   = []
    for i in range(N_cyl):
        d_cht = (measured["cht_C"][i] - cht_mean) / max(cht_mean, 1.0) * 100.0
        d_egt = (measured["egt_C"][i] - egt_mean) / max(egt_mean + 273.15, 1.0) * 100.0
        rho6_9.append(0.45 * d_cht + 0.55 * d_egt)

    # ── ρ₁₀ — oil pressure model ─────────────────────────────────────────
    rho10 = (
        (measured["oil_press_bar"] - predicted["oil_press_bar"])
        / max(predicted["oil_press_bar"], 1e-6)
    ) * 10.0

    # ── ρ₁₁ — 0.5-order crank ripple ─────────────────────────────────────
    # Zero for a balanced engine; rises with single-cylinder defects.
    rho11 = (measured["ripple"] - 0.004) / 0.0125

    # ── Assemble ──────────────────────────────────────────────────────────
    raw_rho: list = [
        float(rho1),
        float(rho2),
        None,                           # rho3 — unavailable on VRDE
        float(rho4),
        float(rho5),
        *[float(x) for x in rho6_9],
        float(rho10),
        float(rho11),
    ]

    # A profile may carry a FIXED healthy baseline measured during its own
    # commissioning run. This is required for a cross-engine installation:
    # probe tolerances and a repeatable model-form closure offset otherwise
    # appear as a permanent fault on day one. It is deliberately static — a
    # drifting sensor or degrading engine changes the residual away from this
    # value and remains observable. Profiles without a commissioned baseline
    # (including VRDE) retain the raw residual unchanged.
    baseline = cfg.get("parity_calibration", {}).get("baseline_rho")
    if baseline is not None:
        if len(baseline) != len(raw_rho):
            raise ValueError("parity_calibration.baseline_rho must contain 11 elements")
        for i, reference in enumerate(baseline):
            if raw_rho[i] is not None and reference is not None:
                raw_rho[i] = float(raw_rho[i] - float(reference))

    # ── Sigma normalisation ───────────────────────────────────────────────
    # Divides each element by its healthy σ to produce a Z-score. The floor
    # matches sigma_generator's own 1e-3 floor on σ. It used to be 0.05, which
    # sat ABOVE the measured healthy σ of ρ₁ (0.0054), ρ₂ (0.0062), ρ₄ (0.019)
    # and ρ₅ (0.035) — so those four channels were divided by up to 9× their
    # real σ, and a turbo fault moving ρ₁ by ~17σ read as ~2σ and was never
    # detected in the live end-to-end test.
    if sigma_vec is not None:
        for i in range(len(raw_rho)):
            if raw_rho[i] is not None and i < len(sigma_vec):
                denom = max(float(sigma_vec[i]), 1e-3)
                raw_rho[i] = float(raw_rho[i] / denom)

    return raw_rho


# ─────────────────────────────────────────────────────────────────────────────
# Extended residuals
# ─────────────────────────────────────────────────────────────────────────────
#
# The eleven relations above cannot tell several fault pairs apart, measured by
# injecting every fault through the live backend and comparing the served
# residual directions (cosine similarity of the fault signatures):
#
#   injector fouling  vs  misfire           0.998   same cold cylinder + ripple
#   CHT sensor drift  vs  EGT sensor drift  0.999   ρ₆–ρ₉ blend EGT and CHT
#   bearing wear      vs  oil leak          0.998   both move only ρ₁₀
#   fuel-filter clog  vs  λ-sensor drift    0.965   both move only ρ₂
#   radiator fouling  vs  coolant pump        —     both invisible: no channel
#                                                   reads the coolant loop
#
# No classifier can separate what its inputs do not contain, so each channel
# below adds the one measurement that physically splits one of those pairs.
# All are built from sensors in config/sensors.yaml; none reads a model state
# the real engine would not report.

COMMON_EXTRA = [
    "coolant",        # coolant temperature vs twin — radiator fouling
    "head_mean",      # mean CHT vs twin — ρ₆₋₉ discard this by definition
    "egt_mean",       # mean EGT vs twin — combustion-wide faults
    "oil_temp",       # oil temperature vs twin — friction heat (bearing)
    "fuel_delivery",  # metered fuel vs command — injector/filter
    "boost_path",     # (compressor-out − MAP) vs twin — MAP-sensor bias
]


def extra_names(n_cyl: int) -> list[str]:
    """Extended residual names for an engine with n_cyl cylinders. Channels an
    installation has no sensor for are still named and served as None."""
    return ([f"egt_dev_{i + 1}" for i in range(n_cyl)]
            + [f"cht_dev_{i + 1}" for i in range(n_cyl)] + COMMON_EXTRA)


EXTRA_NAMES = extra_names(4)   # kept for older callers; use extra_names(n_cyl)


def compute_extra_residuals(measured: dict, predicted: dict, cfg: dict,
                            sigma_ext: list | None = None) -> list:
    """
    Return the extended residuals in EXTRA_NAMES order (raw units unless
    `sigma_ext` is given). An entry is None when the installation has no
    sensor for it, the same convention as ρ₃.

    egt_dev / cht_dev: each cylinder's deviation from the cross-cylinder mean,
        kept SEPARATE for EGT and CHT. A drifting thermocouple moves only its
        own channel; a real combustion change moves both.
    coolant, head_mean, egt_mean, oil_temp: measured minus the healthy twin's
        prediction — the same family as ρ₁₀. They carry information because
        the twin integrates NOMINAL parameters while the engine runs faulted.
    fuel_delivery: (metered flow − commanded flow) / commanded. A fouled
        injector or clogged filter delivers less than the FADEC asked for; a
        misfiring cylinder still receives its fuel and does not move this.
    boost_path: the intercooler pressure drop implied by the compressor-outlet
        and manifold transducers, against the twin's. A biased MAP sensor
        breaks this relation; real breathing losses (ring wear) do not.
    """
    n_cyl = cfg["geometry"]["cylinders"]
    egt, cht = measured["egt_C"], measured["cht_C"]
    egt_m, cht_m = sum(egt) / n_cyl, sum(cht) / n_cyl
    pegt_m = sum(predicted["egt_C"]) / n_cyl
    pcht_m = sum(predicted["cht_C"]) / n_cyl

    egt_dev = [egt[i] - egt_m for i in range(n_cyl)]
    cht_dev = [cht[i] - cht_m for i in range(n_cyl)]

    def _diff(key):
        a, b = measured.get(key), predicted.get(key)
        return None if a is None or b is None else float(a - b)

    cmd = measured["fuel_cmd_per_cyl"] * n_cyl
    fuel_delivery = (measured["fuel_flow_kgps"] - cmd) / max(cmd, 1e-9)

    has_ic = bool(cfg.get("mvem", {}).get("cooling", {}).get("intercooler"))
    boost_path = None
    if has_ic:
        boost_path = ((measured["comp_out_p_hPa"] - measured["map_hPa"])
                      - (predicted["comp_out_p_hPa"] - predicted["map_hPa"]))

    raw = [*egt_dev, *cht_dev,
           _diff("coolant_temp_C"), cht_m - pcht_m, egt_m - pegt_m,
           _diff("oil_temp_C"), fuel_delivery, boost_path]
    raw = [None if v is None else float(v) for v in raw]

    if sigma_ext is not None:
        raw = [None if v is None else float(v / max(float(s), 1e-6))
               for v, s in zip(raw, sigma_ext)]
    return raw
