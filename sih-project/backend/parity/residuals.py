"""
PRAMANA — parity residual vector ρ ∈ ℝ¹¹.

ρ = measured − predicted (what physics says it should read).
Nominal value is zero. Departures identify fault location.

All eleven residuals are computed here. ρ₃ is returned as None when
parity_paths.intake_restriction is False (VRDE is unthrottled — no Path 4
sensor). Everything downstream handles None gracefully.

If sigma_vec is supplied, each non-None element is divided by its healthy
σ to produce a dimensionless Z-score. The sigma floor of 0.05 prevents
channels that are structurally near-zero at healthy steady state (ρ₁, ρ₅)
from amplifying noise into very large numbers under small deviations.
"""

import math


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
    # Both in kg/s from the MVEM state; ratio normalised to predicted flow.
    # Healthy value: 0.  Turbo/compressor fault: departs.
    m_sd   = measured["air_mass_flow"]
    m_comp = predicted["air_mass_flow"]
    p_m_a  = max(m_comp, 1e-6)
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
    # Combines fuel-flow gap and mean CHT gap.  Catches anything that changes
    # where fuel energy goes (cooling degradation, combustion efficiency).
    fuel_gap = (
        (measured["fuel_flow_kgps"] - predicted["fuel_flow_kgps"])
        / max(predicted["fuel_flow_kgps"], 1e-9)
    )
    cht_gap  = (sum(measured["cht_C"]) - sum(predicted["cht_C"])) / N_cyl
    rho4     = -fuel_gap * 10.0 + cht_gap * 0.42

    # ── ρ₅ — power closure via propeller dynamometer ─────────────────────
    # Fractional brake-power gap.  Only departs under friction faults;
    # healthy value is structurally zero.
    p_pwr = max(predicted["brake_power_kW"], 1e-6)
    rho5  = (measured["brake_power_kW"] - predicted["brake_power_kW"]) / p_pwr

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

    # ── Sigma normalisation ───────────────────────────────────────────────
    # Divides each element by its healthy σ to produce a Z-score.
    # Floor of 0.05 prevents structurally near-zero channels (ρ₁, ρ₅) from
    # amplifying tiny healthy-noise differences into enormous Z-scores.
    # These channels carry real fault signal; the floor only guards the
    # healthy regime, not the fault regime.
    if sigma_vec is not None:
        for i in range(len(raw_rho)):
            if raw_rho[i] is not None and i < len(sigma_vec):
                denom = max(float(sigma_vec[i]), 0.05)
                raw_rho[i] = float(raw_rho[i] / denom)

    return raw_rho
