"""
AIR-PATH ESTIMATORS — the two independent estimates of air mass flow.

Why this module exists
----------------------
The design's central claim is over-determination: several INDEPENDENT estimates
of the same physical quantity, computed from DIFFERENT sensor sets, which agree
on a healthy engine and disagree in a fault-specific way when something breaks.
ρ₁ is the first of those comparisons.

It was not implemented that way. `residuals.py` computed

    rho1 = measured["air_mass_flow"] - predicted["air_mass_flow"]

which is the plant's air mass flow minus the twin's — the same estimator on two
different models, not two estimators on one data stream. That is an ordinary
model-mismatch residual, and with plant and twin on identical nominal params it
is zero by construction, which is exactly why ml/data/jitter_report.json lists
rho1 as a dead channel. The design PDF warns that "an evaluator familiar with
parity-space methods will check", and this is what they would find.

The two estimators below are the real thing. Both run on MEASURED channels:

  PATH 1 — speed-density.    ṁ = η_v · p_im · V_d · N / (R · T_im · 120)
           sensors: MAP, IAT, crank speed.        model: η_v correlation.

  PATH 2 — compressor map.   ṁ = φ · ρ₀₁ · A · U_c  via the Ellipse model
           sensors: turbo speed, MAP, ambient pressure, OAT.
           model: the compressor map.

They share MAP, which is unavoidable and physically honest — both paths
legitimately depend on boost. Everything else differs: Path 1 leans on intake
temperature and crank speed, Path 2 on turbo speed and ambient conditions. A
compressor fault changes the relationship between shaft speed and delivered
pressure ratio, so Path 2 departs while Path 1 does not. That is the
information ρ₁ is supposed to carry.

Both estimators deliberately use NOMINAL model constants. An onboard estimator
does not know the engine's current degradation — if it did, the residual would
absorb the fault and report nothing.

The Ellipse equations live here rather than in mvem.py so the plant and the
residual generator call the SAME code. Duplicating them would let the two
drift, and a parity residual computed against a stale copy of the compressor
map is worse than no parity residual at all.
"""

from __future__ import annotations

import math

# Standard reference temperature for corrected speed.
T_REF = 288.15


def _gas(cfg: dict) -> tuple[float, float, float]:
    """(R, gamma, cp_air) with the same defaults MVEM uses."""
    m = cfg["mvem"] if "mvem" in cfg else cfg
    return (
        m.get("R_air_J_per_kgK", 287.05),
        m.get("gamma_air", 1.4),
        m.get("cp_air_J_per_kgK", 1005.0),
    )


def _comp(cfg: dict) -> dict:
    return (cfg["mvem"] if "mvem" in cfg else cfg)["compressor"]


def _ve(cfg: dict) -> dict:
    return (cfg["mvem"] if "mvem" in cfg else cfg)["volumetric_efficiency"]


def volumetric_efficiency(cfg: dict, map_hPa: float, rpm: float) -> float:
    """
    Nominal η_v from the (boost, speed) correlation.

    NOMINAL on purpose — no eta_v_scale. This is what an onboard estimator can
    know, and using the true degraded value would make Path 1 track the fault
    instead of revealing it.
    """
    ve = _ve(cfg)
    p_im_bar = max(map_hPa / 1000.0, 0.0)
    n_krpm = rpm / 1000.0
    eta_v = (
        ve["correlation_c0"]
        + ve["correlation_c1"] * math.sqrt(p_im_bar)
        + ve["correlation_c2"] * n_krpm
        + ve["correlation_c3"] * n_krpm ** 2
    )
    return min(max(eta_v, 0.5), 1.05)


def compressor_ellipse(cfg: dict, w_tc: float, Pi_c: float,
                       p01: float, T01: float,
                       phi_scale: float = 1.0) -> tuple[float, float]:
    """
    Leufven & Eriksson Ellipse model — Control Engineering Practice 21 (2013)
    1871-1883, validated by the authors against 236 real compressor maps to
    under 2.5% mean error in the normal operating region.

    Given shaft speed and the pressure ratio currently being demanded, returns
    (mdot_c, eta_c). NOT slaved to the induction flow, which is what makes it
    an independent estimate rather than a restatement of Path 1.

    p01/T01 are compressor INLET conditions (ambient), in Pa and K.
    """
    R, gamma, cp_air = _gas(cfg)
    c = _comp(cfg)
    d_c = c["impeller_diameter_m"]

    u_c = max(w_tc * d_c / 2.0, 1.0)

    n_corr = (w_tc * 60.0 / (2 * math.pi)) / math.sqrt(max(T01, 1.0) / T_REF)
    speed_ratio = max(n_corr, 1.0) / c["n_corr_design_rpm"]

    psi_max = c["psi_max_design"] * speed_ratio ** c["psi_speed_exponent"]
    # phi_scale is FLOW-CAPACITY degradation. Real compressor fouling and
    # erosion move the map down AND to the left: they cost isentropic
    # efficiency and swallowing capacity together. Modelling efficiency alone
    # leaves the fault invisible to Path 2, because the flow map does not
    # depend on efficiency — see the note in mvem.py where the two are coupled.
    # The ESTIMATOR always passes 1.0: it evaluates the nominal map, and the
    # gap between nominal and degraded is what rho1 is for.
    phi_max = c["phi_max_design"] * speed_ratio ** c["phi_speed_exponent"] * phi_scale

    # Pi_c < 1 is a legitimate restriction/choke state, not an error.
    pi_eff = max(Pi_c, 1e-3)
    psi = 2.0 * cp_air * T01 * (pi_eff ** ((gamma - 1) / gamma) - 1.0) / u_c ** 2

    # Past the ellipse the compressor cannot support this ratio at this speed;
    # cap psi just inside psi_max so phi collapses rather than clamping Pi_c.
    psi_ratio = min(max(psi / max(psi_max, 1e-6), 0.0), 0.999)
    phi = max(phi_max * (1.0 - psi_ratio ** c["c_psi"]) ** (1.0 / c["c_phi"]), 0.0)

    rho01 = p01 / (R * max(T01, 1.0))
    area = math.pi * d_c ** 2 / 4.0
    mdot_c = phi * rho01 * area * u_c

    phi_peak = 0.55 * phi_max
    n_dev = (n_corr - c["n_corr_design_rpm"]) / max(c["n_corr_design_rpm"], 1.0)
    eta_c = c["efficiency_nominal"] - 8.0 * (phi - phi_peak) ** 2 - 0.5 * n_dev ** 2
    eta_c = min(max(eta_c, 0.35), c["efficiency_nominal"])

    return float(mdot_c), float(eta_c)


# ---------------------------------------------------------------------------
# The two parity paths, on MEASURED channels
# ---------------------------------------------------------------------------

def path1_speed_density(cfg: dict, map_hPa: float, iat_K: float,
                        rpm: float) -> float:
    """
    Sensors: MAP, IAT, crank speed. Model: the η_v correlation.

    ṁ = η_v · p_im · V_d · N / (R · T_im · 120), with 120 = 2 rev/cycle × 60 s.
    """
    R, _, _ = _gas(cfg)
    v_d = cfg["geometry"]["displacement_m3"]
    eta_v = volumetric_efficiency(cfg, map_hPa, rpm)
    p_im = map_hPa * 100.0
    return float(eta_v * p_im * v_d * rpm / (R * max(iat_K, 1.0) * 120.0))


def path2_compressor_map(cfg: dict, turbo_rpm: float, map_hPa: float,
                         p_amb_hPa: float, oat_K: float) -> float:
    """
    Sensors: turbo shaft speed, MAP, ambient pressure, OAT.
    Model: the compressor map.

    The plant lumps compressor delivery into manifold pressure (mvem.py uses
    Pi_c = p_im / p_atm and models no intercooler pressure drop), so MAP is the
    consistent measure of compressor outlet here. It is a shared sensor with
    Path 1, but the rest of the inputs — shaft speed and ambient conditions —
    are Path 2's alone, and they are what a compressor fault moves.
    """
    w_tc = turbo_rpm * 2.0 * math.pi / 60.0
    p01 = max(p_amb_hPa, 1.0) * 100.0
    pi_c = (map_hPa * 100.0) / p01
    mdot_c, _ = compressor_ellipse(cfg, w_tc, pi_c, p01, max(oat_K, 1.0))
    return mdot_c


# ---------------------------------------------------------------------------
# ρ₅ — shaft power without a torque sensor: fuel path vs propeller dynamometer
# ---------------------------------------------------------------------------
# Same disease, same cure as ρ₁. `brake_power_kW` in mvem.py is computed from
# torque balance — (T_ind - T_fric - T_pump) * w — which is the FUEL-side
# estimate. `rho5 = measured["brake_power_kW"] - predicted["brake_power_kW"]`
# therefore compared that one estimator on two models (plant vs twin), not two
# estimators on one data stream. At steady state the crank's own equilibrium
# (dw/dt = 0) makes T_ind - T_fric - T_pump equal T_load = P_prop/w BY
# DEFINITION — so the plant's own reported "brake power" already secretly
# equals the propeller-absorbed power, and comparing it to the twin's version
# of the same identity carries no information about which of the two sides
# disagrees.
#
# config/engine_vrde_180.yaml's propeller block says this outright: "This is
# what makes rho5 (= P_indicated - P_prop) a genuine second, independent
# estimate of shaft power instead of a made-up quadratic" — it names the two
# paths but the residual was never built that way.
#
#   Path A  fuel flow, crank speed         -> indicated power via combustion
#   Path B  crank speed, p_amb, OAT, TAS   -> propeller-absorbed power
#
# Both on NOMINAL constants (eta_i, friction coefficient), so a bearing-wear
# fault (which raises the TRUE friction coefficient in the plant) shows up as
# Path A overestimating power rather than being absorbed into the estimate.
#
# TAS is the one input both paths still share, and honestly so: there is no
# airspeed sensor in this model (tas_mps is UNMODELLED — main.py's UNMODELLED
# dict — and the plant's own P_prop uses the identical assumed constant, see
# propeller.assumed_tas_mps in the profile). A true airspeed sensor is future
# work; until then this is a real shared limitation, not a hidden one.

def indicated_power_kw(cfg: dict, fuel_flow_kgps: float, rpm: float) -> float:
    """
    Sensors: fuel flow, crank speed. Model: nominal indicated efficiency and
    nominal friction torque.

    P_ind = eta_i * mdot_f * Q_LHV - T_fric(N) * w, mirroring mvem.py's
    combustion and friction terms exactly, but with f_fric_scale PINNED AT
    NOMINAL — the estimator must not know the plant's true wear state, or a
    bearing fault would be absorbed rather than revealed.
    """
    eta_i = cfg.get("mvem", cfg).get("combustion", {}).get("eta_i_nominal", 0.50)
    f_fric_nom = cfg.get("mvem", cfg)["crankshaft"].get("friction_coeff_nominal", 1.0)
    q_lhv = cfg["fuel"]["Q_LHV_J_per_kg"]

    w = rpm * 2.0 * math.pi / 60.0
    p_ind_w = eta_i * fuel_flow_kgps * q_lhv
    t_fric = 6.2 * f_fric_nom * w / 100.0   # same coefficient as mvem.py:266
    p_fric_w = t_fric * w
    return (p_ind_w - p_fric_w) / 1000.0


def propeller_power_kw(cfg: dict, rpm: float, p_amb_hPa: float,
                       oat_K: float, tas_mps: float) -> float:
    """
    Sensors: crank speed, ambient pressure, OAT — plus TAS, the one input
    this path still shares with the plant's own load model (see module note:
    there is no airspeed sensor in this system yet).

    P_prop = Cp(J) * rho_air * n^3 * D^5, the same fixed-pitch propeller law
    mvem.py uses for the load itself, evaluated here as an independent
    ESTIMATE from measured shaft speed and measured ambient conditions.
    """
    prop = cfg["propeller"]
    R, _, _ = _gas(cfg)

    gear_ratio = prop.get("gear_ratio", 1.0)
    n_prop_rps = max(rpm / gear_ratio, 1.0) / 60.0
    j = tas_mps / max(n_prop_rps * prop["diameter_m"], 1e-6)
    cp = prop["cp0"] * max(1.0 - (j / prop["j_max"]) ** 2, 0.0)

    rho_air = (p_amb_hPa * 100.0) / (R * max(oat_K, 1.0))
    p_prop_w = cp * rho_air * n_prop_rps ** 3 * prop["diameter_m"] ** 5
    return p_prop_w / 1000.0
