"""
Novelty detection — subspace projection via SVD.

Ported from frontend/src/analysis/novelty.ts, with the CRITICAL difference that
this implementation uses a proper truncated SVD (scipy.linalg.svd) for the rank
computation and pseudoinverse, NOT the rank-revealing modified Gram-Schmidt that
the frontend reference uses.

The SVD is REQUIRED here (not optional) because:
  1. The docs explicitly ask BE-2 to "port this, but use a proper SVD for the
     rank check and report the singular values on the validation slide."
  2. The rank computation on the sensitivity matrix (continuous Jacobian entries)
     requires numerical SVD — the coarse glyph matrix may give rank 10, the
     sensitivity matrix may differ and must be checked explicitly.

Algorithm
---------
Each fault mode defines a DIRECTION in the 11-dimensional residual space.
Stack them into F ∈ R^(11 × M) (per-cylinder expanded).
Split any live residual:

    ρ̂  = F F⁺ ρ             explained — lies in span(F)
    ρ⊥ = ρ − ρ̂              UNEXPLAINED — orthogonal to all known faults
    ν  = ‖ρ⊥‖ / ‖ρ‖         novelty index ∈ [0, 1]

High ‖ρ‖ with high ν means something real is happening that the fault library
cannot express. Correct output: LOW CONFIDENCE, not a confident wrong answer.

Significance weighting (MANDATORY — see novelty-detection.md §SIGNIFICANCE):
    significance = clamp((‖ρ‖ − ρ_floor) / (ρ_full − ρ_floor), 0, 1)
    confidence   = 1 − ν · significance
    exceeded     = ν > threshold AND significance > 0.5

See docs/spec/novelty-detection.md for the full spec.
See docs/backend/ML-DEEP-DIVE.md §7 for the judge-facing argument.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import svd as scipy_svd
from dataclasses import dataclass

from ml.incidence import build_fault_matrix_expanded, N_RESIDUALS


# ── SVD-based pseudoinverse and rank ──────────────────────────────────────

class NoveltyProjector:
    """
    Computes and stores the SVD decomposition of the fault matrix F.
    Exposes the projection, rank, and null-space dimension.

    Build once at startup; call project() at 1 Hz.
    """

    RANK_TOL = 0.05   # σᵢ/σ₁ > this → considered independent direction

    def __init__(self, rank_tol: float = RANK_TOL):
        self.rank_tol = rank_tol
        F_exp, self.col_labels = build_fault_matrix_expanded()

        # Normalise each column so magnitudes don't bias the subspace.
        norms = np.linalg.norm(F_exp, axis=0, keepdims=True)
        norms[norms < 1e-12] = 1.0
        self.F_normalised = F_exp / norms           # (11, M)

        # Full SVD: F = U S Vt
        U, S, Vt = scipy_svd(self.F_normalised, full_matrices=False)
        self.U = U        # (11, min(11,M))
        self.S = S        # (min(11,M),)
        self.Vt = Vt      # (min(11,M), M)

        # Effective rank and basis for span(F)
        threshold = S[0] * rank_tol if S[0] > 1e-12 else rank_tol
        mask = S > threshold
        self.effective_rank: int = int(mask.sum())
        self.null_space_dim: int = N_RESIDUALS - self.effective_rank

        # Projection matrix onto span(F): P = U_r U_r^T
        U_r = U[:, mask]                            # (11, r)
        self.P = U_r @ U_r.T                        # (11, 11) — idempotent

        print(
            f"[NoveltyProjector] F shape={self.F_normalised.shape}  "
            f"singular values={np.round(S, 3).tolist()}\n"
            f"  effective_rank={self.effective_rank}  "
            f"null_space_dim={self.null_space_dim}  "
            f"(tol={rank_tol})"
        )
        if self.null_space_dim == 0:
            print(
                "  WARNING: null_space_dim=0 — fault signatures span all of R^11. "
                "The novelty claim collapses; drop it from any slide and say why. "
                "This is the honesty policy working as intended."
            )

    def project(self, rho: np.ndarray) -> "NoveltyResult":
        """
        Decompose a single residual vector into explained + unexplained.

        Parameters
        ----------
        rho : (11,) — null entries (unavailable parity paths) passed as np.nan
              or zero; NaN is replaced with 0.0 (unavailable channel = no evidence).
        """
        v = np.where(np.isnan(rho), 0.0, rho).astype(float)
        rho_norm = float(np.linalg.norm(v))

        rho_hat  = self.P @ v
        rho_perp = v - rho_hat
        unex_norm = float(np.linalg.norm(rho_perp))

        # Guard divide-by-zero at rest (both norms ≈ 0 → no evidence → index = 0)
        index = unex_norm / rho_norm if rho_norm > 1e-6 else 0.0

        return NoveltyResult(
            index=float(index),
            residual_norm=rho_norm,
            unexplained_norm=unex_norm,
            unexplained=rho_perp.tolist(),
            effective_rank=self.effective_rank,
            null_space_dim=self.null_space_dim,
            singular_values=self.S.tolist(),
        )

    def calibrate_threshold(
        self,
        healthy_rho: np.ndarray,
        percentile: float = 99.5,
    ) -> float:
        """
        Calibrate the novelty threshold as the `percentile`-th percentile of ν
        on held-out healthy residuals.

        Under the healthy null hypothesis, ‖ρ⊥‖² is chi-square distributed with
        null_space_dim degrees of freedom — so this percentile has a principled
        interpretation and is not hand-picked.

        Parameters
        ----------
        healthy_rho : (N, 11) — array of healthy residual vectors
        """
        indices = []
        for row in healthy_rho:
            res = self.project(row)
            indices.append(res.index)
        threshold = float(np.percentile(indices, percentile))
        print(
            f"[NoveltyProjector] threshold={threshold:.4f} "
            f"({percentile}th pct, n={len(indices)})"
        )
        return threshold


# ── Result dataclass ───────────────────────────────────────────────────────

@dataclass
class NoveltyResult:
    """
    Output of NoveltyProjector.project(). Maps directly to the `novelty`
    block in pramana.health.v1 (see telemetry-schema.md).
    """
    index: float                # ν = ‖ρ⊥‖ / ‖ρ‖, in [0, 1]
    residual_norm: float        # ‖ρ‖ in sigma units
    unexplained_norm: float     # ‖ρ⊥‖ in sigma units
    unexplained: list[float]    # per-residual unexplained component (for explain drawer)
    effective_rank: int         # rank(F) at stated tolerance
    null_space_dim: int         # 11 - effective_rank
    singular_values: list[float]

    def to_health_frame(
        self,
        threshold: float,
        rho_floor: float,
        rho_full: float,
    ) -> dict:
        """
        Serialise to the `novelty` + `twin_confidence` blocks of pramana.health.v1.

        Significance weighting ensures the confidence channel is not misleading
        at rest (when ‖ρ‖ is just noise, ν is meaningless and confidence = 1.0).
        """
        # Significance: how much of the residual norm is above the noise floor
        significance = float(np.clip(
            (self.residual_norm - rho_floor) / max(rho_full - rho_floor, 1e-9),
            0.0, 1.0
        ))
        confidence = 1.0 - self.index * significance
        exceeded = (self.index > threshold) and (significance > 0.5)

        return {
            "novelty": {
                "index": round(self.index, 4),
                "residual_norm": round(self.residual_norm, 4),
                "unexplained_norm": round(self.unexplained_norm, 4),
                "threshold": round(threshold, 4),
                "exceeded": exceeded,
                "effective_rank": self.effective_rank,
                "null_space_dim": self.null_space_dim,
            },
            "twin_confidence": {
                "value": round(confidence, 4),
                "basis": "residual_projection",
                "note": (
                    "low when the excitation pattern is not in the fault library"
                    if confidence < 0.7 else "within fault library"
                ),
            },
        }


# ── Null-space direction (for test injection) ──────────────────────────────

def null_space_direction(projector: NoveltyProjector, seed: int = 0) -> np.ndarray:
    """
    Return a unit vector in the null space of F — used by mission generator to
    inject an excitation the fault library provably cannot explain.

    If null_space_dim == 0 the null space is empty; returns a zero vector.
    """
    if projector.null_space_dim == 0:
        return np.zeros(N_RESIDUALS)

    # Start from a deterministic vector and remove span(F).
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(N_RESIDUALS)
    perp = v - projector.P @ v
    n = np.linalg.norm(perp)
    return (perp / n) if n > 1e-9 else np.zeros(N_RESIDUALS)


# ── Rank validation report ─────────────────────────────────────────────────

def print_rank_report(projector: NoveltyProjector) -> None:
    """
    Print the validation table that goes on the slide.
    "We checked: the effective rank is R, so there are D dimensions of
    unexplainable residual."
    """
    print("\n━━━━ Novelty Rank Report ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  F shape (rows=residuals, cols=expanded_faults): "
          f"{projector.F_normalised.shape}")
    print(f"  Singular values (σ₁ normalised to 1.0):")
    S = projector.S
    S_norm = S / (S[0] + 1e-12)
    for i, (s, sn) in enumerate(zip(S, S_norm)):
        flag = "✓" if sn > projector.rank_tol else "✗ (below tol)"
        print(f"    σ_{i+1:02d} = {s:.4f}  (rel {sn:.3f}) {flag}")
    print(f"\n  effective_rank  = {projector.effective_rank}")
    print(f"  null_space_dim  = {projector.null_space_dim}")
    if projector.null_space_dim == 0:
        print("  ⚠  NULL SPACE IS EMPTY — drop the novelty claim from slides.")
    elif projector.null_space_dim == 1:
        print("  ✓  One null-space dimension — narrow margin, state it honestly.")
    else:
        print(f"  ✓  {projector.null_space_dim} null-space dimensions.")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
