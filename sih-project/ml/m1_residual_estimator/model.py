"""
M1 — Physics-guided residual estimator.

Architecture:
    ŷ = f_physics(u; θ) + g_NN(u)
        └─ MVEM stub ─┘    └─ small MLP correction ─┘

The physics term (f_physics) is a simplified mean-value engine model (MVEM)
that computes expected sensor readings from the operating-point vector u.
The neural correction g_NN learns only the DISCREPANCY between the MVEM
prediction and ground truth — a much smaller and smoother function than the
full sensor mapping.

Because the correction is always a small delta on top of physics:
  • The network trains on far less data.
  • At out-of-distribution operating points (high altitude, extreme loads) the
    MVEM still provides a physically sane baseline — no absurd extrapolation.

Soft physical constraints in the loss function:
    L = L_data
      + λ₁ · ‖ρ₄‖²                         energy balance
      + λ₂ · ‖ṁ_a + ṁ_f − ṁ_ex‖²           mass continuity
      + λ₃ · ‖min(0, ∂T_cht/∂ṁ_f)‖         CHT monotonicity w.r.t. fuel flow

See docs/backend/ML-DEEP-DIVE.md §3 for the full argument.

Input vector u (10 features):
    [rpm, map_hPa, iat_K, fuel_flow_kgps, lambda,
     turbo_rpm, comp_out_p_hPa, comp_out_T_K,
     altitude_ft, tas_mps]

Output ŷ (13 targets — the expected residual baseline, nominally zero):
    [rho1..rho13]  in normalised (sigma) units
"""

from __future__ import annotations

import torch
import torch.nn as nn
import numpy as np

# ── constants ──────────────────────────────────────────────────────────────
R_AIR = 287.05       # J/(kg·K)
GAMMA = 1.35
CP_EX = 1150.0       # J/(kg·K) exhaust specific heat
Q_LHV = 43_200_000.0 # J/kg  diesel lower heating value
AFR_ST = 14.5        # stoichiometric air/fuel ratio (approx diesel)

# Engine config — these are Rotax-914-ish defaults; overridden by YAML at runtime.
V_D = 1.352e-3       # m³  (1352 cc / 4 cylinders × 4 = total; per-cycle swept)
N_CYL = 4

# ── MVEM stub ───────────────────────────────────────────────────────────────
def _isa_pressure(altitude_ft: torch.Tensor) -> torch.Tensor:
    """ISA pressure at altitude [Pa]."""
    h_m = altitude_ft * 0.3048
    p = 101325.0 * (1.0 - 2.25577e-5 * h_m) ** 5.2559
    return p


def mvem_predict(u: torch.Tensor) -> torch.Tensor:
    """
    Simplified MVEM: given operating-point vector u, return expected sensor
    readings stacked as a (batch, 11) tensor in the residual-vector coordinate
    system (all outputs are nominally zero for a healthy engine).

    This is a physics stub — the actual MVEM lives in backend/twin/mvem.py
    (BE-1's domain). Here we replicate the core relations needed so M1 can
    produce a sensible baseline before BE-1's full model is available.
    """
    # u: (B, 10)
    rpm       = u[:, 0]          # crank speed [rpm]
    map_hpa   = u[:, 1]          # manifold absolute pressure [hPa]
    iat_K     = u[:, 2]          # intake air temp [K]
    mf        = u[:, 3]          # fuel mass flow [kg/s]
    lam       = u[:, 4]          # lambda (wideband UEGO)
    # turbo_rpm = u[:, 5]        # not used in MVEM stub
    # comp_out_p= u[:, 6]        # not used in MVEM stub
    # comp_out_T= u[:, 7]        # not used in MVEM stub
    alt_ft    = u[:, 8]
    # tas_mps   = u[:, 9]

    map_pa = map_hpa * 100.0    # hPa → Pa

    # ── Path 1 — speed-density air mass flow ─────────────────────────────
    # ṁ_SD = η_v · p_im · V_d · N / (R · T_im · 120)
    # Assume nominal η_v = 0.85 (healthy)
    ETA_V_NOM = 0.85
    N_rps = rpm / 60.0
    ma_sd = ETA_V_NOM * map_pa * V_D * N_rps / (R_AIR * iat_K * 2.0)

    # ── Path 3 — fuel-and-lambda air mass flow ────────────────────────────
    # ṁ_λ = λ · AFR_st · ṁ_f
    ma_lambda = lam * AFR_ST * mf

    # ── ρ₂ = ṁ_SD − ṁ_λ ─────────────────────────────────────────────────
    rho2 = ma_sd - ma_lambda

    # ── ρ₄ — energy closure (first-law residual) ─────────────────────────
    # Simplified: ignore brake torque and heat rejection (unknown without full twin).
    # ρ₄ / (ṁ_f · Q_LHV) ≈ 0 for healthy engine.
    # Stub outputs zero (physics is perfect before the correction term).
    rho4 = torch.zeros_like(rpm)

    # ── ρ₁, ρ₃ — placeholders (need compressor map, intake restriction) ──
    rho1 = torch.zeros_like(rpm)
    rho3 = torch.zeros_like(rpm)

    # ── ρ₅ power closure — stub ──────────────────────────────────────────
    rho5 = torch.zeros_like(rpm)

    # ── ρ₆–ρ₉ per-cylinder thermal deviation — stub ──────────────────────
    rho6 = torch.zeros_like(rpm)
    rho7 = torch.zeros_like(rpm)
    rho8 = torch.zeros_like(rpm)
    rho9 = torch.zeros_like(rpm)

    # ── ρ₁₀ oil pressure — stub ──────────────────────────────────────────
    rho10 = torch.zeros_like(rpm)

    # ── ρ₁₁ crank ripple — stub ──────────────────────────────────────────
    rho11 = torch.zeros_like(rpm)

    # rho12 (coolant closure) and rho13 (head-temperature closure) have no
    # counterpart in this stub — it carries no thermal model — so they are zero
    # and the neural correction carries them entirely. Stated rather than
    # hidden: this stub computes only rho2 and zeroes the rest.
    rho12 = torch.zeros_like(rpm)
    rho13 = torch.zeros_like(rpm)

    return torch.stack(
        [rho1, rho2, rho3, rho4, rho5,
         rho6, rho7, rho8, rho9, rho10, rho11, rho12, rho13],
        dim=1
    )   # (B, 13)


# ── Neural correction g_NN ─────────────────────────────────────────────────
class CorrectionMLP(nn.Module):
    """
    Small MLP that learns the residual between the MVEM prediction and reality.
    Input: operating-point vector u ∈ R^10
    Output: correction Δ ∈ R^11
    """

    def __init__(self, input_dim: int = 10, hidden: int = 64, output_dim: int = 13):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.SiLU(),
            nn.Linear(hidden, hidden),
            nn.SiLU(),
            nn.Linear(hidden, output_dim),
        )
        # Initialise small so the correction starts near zero.
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight, gain=0.1)
                nn.init.zeros_(m.bias)

    def forward(self, u: torch.Tensor) -> torch.Tensor:
        return self.net(u)


# ── Full M1 model ──────────────────────────────────────────────────────────
class M1ResidualEstimator(nn.Module):
    """
    ŷ = f_physics(u) + g_NN(u)

    At inference:
        rho = y_measured - ŷ

    Virtual sensors (no direct measurement):
        These are outputs of the physics model that have no sensor counterpart.
        Available as attributes after a forward pass.
    """

    def __init__(self, input_dim: int = 10, hidden: int = 64):
        super().__init__()
        self.correction = CorrectionMLP(input_dim, hidden, output_dim=13)

    def forward(self, u: torch.Tensor) -> torch.Tensor:
        """
        Parameters
        ----------
        u : (B, 10)  normalised operating-point features

        Returns
        -------
        y_hat : (B, 13)  predicted sensor outputs (in residual sigma units)
        """
        physics_pred = mvem_predict(u)          # (B, 11)  — MVEM baseline
        correction   = self.correction(u)       # (B, 11)  — NN delta
        return physics_pred + correction


# ── Physical loss terms ────────────────────────────────────────────────────

def physics_loss(
    y_hat: torch.Tensor,
    u: torch.Tensor,
    lambda1: float = 0.1,
    lambda2: float = 0.1,
    lambda3: float = 0.05,
) -> torch.Tensor:
    """
    Soft physical constraint penalties added to the data loss.

    L_phys = λ₁ · ‖rho4_hat‖²              energy balance
           + λ₂ · ‖mass_continuity‖²        ṁ_a + ṁ_f − ṁ_ex ≈ 0
           + λ₃ · ‖ReLU(−∂T_cht/∂ṁ_f)‖     CHT must rise with fuel flow

    Parameters
    ----------
    y_hat : (B, 13)  model predictions
    u     : (B, 10)  operating-point features (fuel flow at index 3)
    """
    # λ₁ — energy residual ρ₄ should be near zero.
    rho4_hat = y_hat[:, 3]
    loss_energy = lambda1 * (rho4_hat ** 2).mean()

    # λ₂ — mass continuity: ṁ_a + ṁ_f ≈ ṁ_exhaust.
    # Approximate with ρ₁ + ρ₂ imbalance (full relation needs the whole MVEM).
    rho1 = y_hat[:, 0]
    rho2 = y_hat[:, 1]
    imbalance = rho1 + rho2          # should be near zero for consistent air paths
    loss_mass = lambda2 * (imbalance ** 2).mean()

    # λ₃ — CHT monotonicity: CHT (rho6-9 block) should not fall as fuel rises.
    # We check the sign of d(mean_cht_pred) / d(mf) using finite differences over
    # the batch — only penalise negative gradient.
    mean_cht = y_hat[:, 5:9].mean(dim=1)         # mean per-cylinder thermal deviation
    mf = u[:, 3]
    # Sort by mf within batch for a crude local gradient estimate.
    idx = mf.argsort()
    dmf = mf[idx].diff()                         # (B-1,)
    dcht = mean_cht[idx].diff()                  # (B-1,)
    valid = dmf.abs() > 1e-8
    if valid.any():
        grad = dcht[valid] / dmf[valid]
        loss_mono = lambda3 * torch.clamp(-grad, min=0.0).mean()
    else:
        loss_mono = torch.tensor(0.0, device=y_hat.device)

    return loss_energy + loss_mass + loss_mono


# ── Feature normalisation ──────────────────────────────────────────────────

class OperatingPointScaler:
    """
    Fit μ and σ on a training set of u vectors; transform at inference.
    Saved alongside model weights.
    """

    def __init__(self):
        self.mean_: np.ndarray | None = None
        self.std_: np.ndarray | None = None

    def fit(self, X: np.ndarray) -> "OperatingPointScaler":
        self.mean_ = X.mean(axis=0)
        self.std_ = X.std(axis=0) + 1e-8
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return ((X - self.mean_) / self.std_).astype(np.float32)

    def state_dict(self) -> dict:
        return {"mean": self.mean_.tolist(), "std": self.std_.tolist()}

    def load_state_dict(self, d: dict) -> None:
        self.mean_ = np.array(d["mean"])
        self.std_ = np.array(d["std"])
