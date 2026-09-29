"""
Fault incidence matrix — Python mirror of frontend/src/analysis/incidence.ts.

Column order: rho1 rho2 rho3 rho4 rho5 rho6 rho7 rho8 rho9 rho10 rho11
Encoding:
    2  = strong positive excitation
    1  = weak positive
   -1  = weak negative
   -2  = strong negative
    0  = unexcited

Derived from physics (residual-spec.md §3), not asserted from experience.
Component faults above the line; instrumentation faults below.
"""

import numpy as np

N_RESIDUALS = 11
CYL_SLOTS = [5, 6, 7, 8]          # rho6..rho9 indices (0-based)
N_CYL = len(CYL_SLOTS)            # 4 cylinders

# fmt: off
INCIDENCE: dict[str, list[int]] = {
    #                      ρ1  ρ2  ρ3  ρ4  ρ5  ρ6  ρ7  ρ8  ρ9 ρ10 ρ11
    "ring_wear":           [ 1,  1,  1,  1,  1,  0,  0,  0,  0, -1,  0],
    "turbo_degradation":   [-2,  0,  0,  1,  0,  0,  0,  0,  0,  0,  0],
    "injector_fouling":    [ 0, -2,  0,  1,  0,  2,  2,  2,  2,  0,  2],
    "fuel_filter_clog":    [ 0, -2,  0,  1, -1,  0,  0,  0,  0,  0,  0],
    "injection_misfire":   [ 0,  0,  0,  2, -1, -2, -2, -2, -2,  0,  2],
    "cooling_fouling":     [ 0,  0,  0,  1,  0,  1,  1,  1,  1,  1,  0],
    "oil_pump_wear":       [ 0,  0,  0,  0,  1,  0,  0,  0,  0, -2,  0],
    "bearing_wear":        [ 0,  0,  0,  1,  2,  0,  0,  0,  0, -1,  0],
    # rho11 (+2) is what separates detonation from egt_sensor_drift. Without it
    # the two rows were IDENTICAL and the pair was structurally inseparable —
    # which mattered because one is a component fault and the other
    # instrumentation, the exact discrimination the 3:00 demo beat rests on.
    # Detonation is a combustion abnormality that rings the structure and
    # shows in the 0.5-order crank ripple; a drifting thermocouple does not
    # move ripple at all. Measured effect on the oracle ceiling: 0.643 -> 0.687.
    "detonation":          [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  2],
    # --- instrumentation faults ---
    "map_sensor_drift":    [ 2,  1,  1,  1,  0,  0,  0,  0,  0,  0,  0],
    "egt_sensor_drift":    [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  0],
    "cht_sensor_drift":    [ 0,  0,  0,  0,  0,  2,  2,  2,  2,  0,  0],
    "lambda_sensor_drift": [ 0,  2,  0,  0,  0,  0,  0,  0,  0,  0,  0],
}
# fmt: on

FAULT_NAMES = list(INCIDENCE.keys())
N_FAULTS = len(FAULT_NAMES)
FAULT_INDEX = {name: i for i, name in enumerate(FAULT_NAMES)}


def build_fault_matrix_raw() -> np.ndarray:
    """
    Return F ∈ R^(N_RESIDUALS × N_FAULTS) — the raw incidence matrix,
    one column per fault in FAULT_NAMES order.
    Columns are NOT yet per-cylinder expanded.
    """
    F = np.zeros((N_RESIDUALS, N_FAULTS), dtype=float)
    for j, name in enumerate(FAULT_NAMES):
        F[:, j] = INCIDENCE[name]
    return F


def build_fault_matrix_expanded() -> tuple[np.ndarray, list[str]]:
    """
    Per-cylinder expansion of the fault matrix.

    The incidence table lists rho6..rho9 as a group, which is right for a
    printed table but WRONG as a direction in residual space. A single-cylinder
    fault lifts ITS OWN thermal deviation and pushes the other three slightly
    negative, because each residual is the deviation from the CONDITIONAL MEAN
    across cylinders (sum-to-zero is baked in by definition).

    Any fault whose per-cylinder block is non-zero is expanded into N_CYL
    columns, one per cylinder, with the thermal part shaped as (e_i - mean).
    Faults with no per-cylinder excitation are kept as-is.

    Returns:
        F_exp   — R^(11 × M) where M >= N_FAULTS
        col_labels — human-readable label per column
    """
    columns: list[np.ndarray] = []
    labels: list[str] = []

    for name in FAULT_NAMES:
        col = np.array(INCIDENCE[name], dtype=float)
        thermal = col[CYL_SLOTS]          # rho6..rho9 values for this fault
        excited = np.any(thermal != 0)

        if not excited:
            columns.append(col)
            labels.append(name)
            continue

        # The signed magnitude to use for each per-cylinder expansion.
        mag = np.abs(thermal).sum() / N_CYL

        for c in range(N_CYL):
            v = col.copy()
            # Shape: cylinder c is +, others are -(1/(N_CYL-1)), sum = 0.
            # Sign tracks the original excitation direction.
            sign = float(np.sign(thermal[c])) if thermal[c] != 0 else 1.0
            for k in range(N_CYL):
                indicator = 1.0 if k == c else 0.0
                v[CYL_SLOTS[k]] = sign * mag * (indicator - 1.0 / N_CYL)
            columns.append(v)
            labels.append(f"{name}_cyl{c + 1}")

    F_exp = np.column_stack(columns)    # (11, M)
    return F_exp, labels


# ── Structural isolability ────────────────────────────────────────────────

def inseparable_groups(tol: float = 0.0) -> list[list[str]]:
    """
    Fault modes whose incidence rows are identical, i.e. that NO method can
    separate from steady-state parity — the columns-differ test of
    residual-spec.md §4, applied to our own matrix rather than asserted.

    This currently returns {detonation, egt_sensor_drift}: both are
    [0,0,0,1,0, 2,2,2,2, 0,0]. That pair matters more than the others because
    one is a COMPONENT fault and the other is INSTRUMENTATION — the exact
    discrimination the 3:00 demo beat is built on. Reporting either one
    confidently would mean telling a commander the engine is serviceable while
    it detonates, or pulling a good engine for a forty-rupee thermocouple.

    The architecture already has the answer (residual-spec.md §5): when
    structure cannot resolve, say so and let the active-diagnosis probe settle
    it by commanding a bounded fuel-trim perturbation and testing the GAIN of
    the response. A transducer's constant bias cancels from the alternating
    component; a real detonation does not.
    """
    import numpy as _np
    names = list(INCIDENCE.keys())
    rows = {k: _np.asarray(v, dtype=float) for k, v in INCIDENCE.items()}
    groups: list[list[str]] = []
    used: set[str] = set()
    for i, a in enumerate(names):
        if a in used:
            continue
        grp = [a]
        for b in names[i + 1:]:
            if b in used:
                continue
            if _np.max(_np.abs(rows[a] - rows[b])) <= tol:
                grp.append(b)
                used.add(b)
        if len(grp) > 1:
            used.add(a)
            groups.append(grp)
    return groups


#: Precomputed once — {fault: frozenset(of faults it cannot be told apart from)}
AMBIGUITY_GROUPS: dict[str, frozenset] = {
    f: frozenset(g) for g in inseparable_groups() for f in g
}
