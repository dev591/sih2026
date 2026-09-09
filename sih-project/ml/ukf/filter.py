"""
UKF — Unscented Kalman Filter for joint state-parameter estimation.

Health parameters live INSIDE the estimator's state vector, modelled as
a slow random walk: θ_{k+1} = θ_k + w_k,  w_k ~ N(0, Q_θ).

State vector θ:
    [η_v_scale, η_c_scale, hA_scale, cd_inj_1..4, f_fric_scale]
    = 8 parameters

Why UKF over EKF (ML-DEEP-DIVE.md §6):
  • MVEM's induction and turbocharger relations are meaningfully nonlinear.
  • EKF requires Jacobians of those relations — error-prone to derive and
    maintain.
  • UKF propagates sigma points through the ACTUAL nonlinear model.
  • For our state dimension (8 parameters) the sigma-point count is cheap at
    1 Hz: 2n+1 = 17 evaluations per step.

Sensor-fault discrimination (free from the filter structure):
  • A COMPONENT fault: innovation absorbed by an adjustment of θ.
  • A TRANSDUCER fault: innovation on one channel that no physically admissible
    θ change can explain → NIS spike on that channel alone.

See docs/spec/residual-spec.md §6 and ML-DEEP-DIVE.md §6.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field


# ── State definition ───────────────────────────────────────────────────────

THETA_NAMES = [
    "eta_v_scale",    # volumetric efficiency scale factor
    "eta_c_scale",    # compressor efficiency scale factor
    "hA_scale",       # heat rejection scale factor
    "cd_inj_1",       # injector discharge coefficient, cyl 1
    "cd_inj_2",
    "cd_inj_3",
    "cd_inj_4",
    "f_fric_scale",   # friction torque scale factor
]
N_THETA = len(THETA_NAMES)

# Nominal (healthy) initial values
THETA_NOM = np.array([1.0, 1.0, 1.0,  1.0, 1.0, 1.0, 1.0,  1.0], dtype=float)

# Bounds on physically plausible values
THETA_MIN = np.array([0.5, 0.5, 0.5,  0.3, 0.3, 0.3, 0.3,  0.5], dtype=float)
THETA_MAX = np.array([1.2, 1.2, 1.5,  1.1, 1.1, 1.1, 1.1,  2.0], dtype=float)


# ── UKF parameters ─────────────────────────────────────────────────────────

@dataclass
class UKFConfig:
    alpha: float = 1e-3          # spread of sigma points
    beta: float  = 2.0           # prior knowledge (Gaussian → β=2 optimal)
    kappa: float = 0.0           # secondary scaling
    # Process noise — slow random walk: small to reflect slow degradation
    q_theta: float = 1e-6        # variance per step for each θ component
    # Measurement noise covariance diagonal — in sigma units (normalised residuals)
    r_diag: float = 1.0          # each residual is nominally N(0,1)


# ── UKF implementation ─────────────────────────────────────────────────────

class HealthUKF:
    """
    Unscented Kalman Filter estimating the degradation parameter vector θ.

    The observation function h(θ) maps health parameters to the expected
    residual vector ρ (via the MVEM). In this implementation, h(θ) is a
    linear approximation based on the incidence matrix scaled by the
    deviation of each parameter from its nominal value. This is replaced
    by the full MVEM-based h when BE-1's twin is available.

    Usage:
        ukf = HealthUKF()
        for each 1 Hz step:
            theta_est, sigma = ukf.step(rho_measured)
    """

    def __init__(self, config: UKFConfig | None = None):
        self.cfg = config or UKFConfig()
        n = N_THETA

        # Scaling
        lam = self.cfg.alpha**2 * (n + self.cfg.kappa) - n
        self.lambda_ = lam

        # Sigma-point weights
        self.Wm = np.zeros(2 * n + 1)
        self.Wc = np.zeros(2 * n + 1)
        self.Wm[0] = lam / (n + lam)
        self.Wc[0] = lam / (n + lam) + (1 - self.cfg.alpha**2 + self.cfg.beta)
        for i in range(1, 2 * n + 1):
            self.Wm[i] = 1.0 / (2.0 * (n + lam))
            self.Wc[i] = 1.0 / (2.0 * (n + lam))

        # State: mean and covariance
        self.theta = THETA_NOM.copy()
        self.P = np.eye(n) * 0.01           # initial uncertainty

        # Process noise covariance
        self.Q = np.eye(n) * self.cfg.q_theta

        # Observation noise covariance (11 residuals, each ≈ N(0,1))
        self.R = np.eye(11) * self.cfg.r_diag

        # Track NIS for sensor-fault discrimination
        self._nis_history: list[float] = []

    def _sigma_points(self) -> np.ndarray:
        """Generate 2n+1 sigma points around current estimate."""
        n = N_THETA
        scale = np.sqrt((n + self.lambda_) * np.diag(self.P))
        sp = np.zeros((2 * n + 1, n))
        sp[0] = self.theta
        for i in range(n):
            sp[i + 1]     = self.theta + scale
            sp[n + i + 1] = self.theta - scale
        # Clamp to physical bounds
        sp = np.clip(sp, THETA_MIN, THETA_MAX)
        return sp

    def _h(self, theta: np.ndarray) -> np.ndarray:
        """
        Observation function: θ → expected ρ (simplified).

        Full version calls the MVEM directly. This stub uses a linearisation
        around the nominal θ, sufficient until BE-1's model is wired in:
            ρ_expected ≈ H · (θ - θ_nom)
        where H is the sensitivity matrix (a scaled version of the incidence
        matrix). For a healthy engine (θ ≈ θ_nom) this correctly predicts ρ ≈ 0.
        """
        # Import incidence matrix lazily to avoid circular import at module level
        from ml.incidence import build_fault_matrix_raw, FAULT_NAMES, N_RESIDUALS

        # Sensitivity: each parameter maps to a row of residuals.
        # Simplified: η_v affects ρ₁ (speed-density path), η_c affects ρ₁ (via
        # compressor path), cd_inj_i affects ρ₂ and ρ₆₋₉ etc.
        # For the stub, use a hand-coded sensitivity that matches the physics.
        delta = theta - THETA_NOM
        rho = np.zeros(11)

        # η_v deviation → ρ₁ (speed-density over-reads when η_v drops)
        rho[0] += -3.0 * delta[0]           # rho1
        # η_c deviation → ρ₁ (compressor map under-reads when η_c drops)
        rho[0] += 2.0 * delta[1]            # rho1 (opposite sign to η_v)
        # hA → ρ₄ energy closure
        rho[3] += 1.5 * delta[2]            # rho4
        # cd_inj_i → ρ₂ and per-cylinder ρ₆-9
        for c, idx in enumerate([3, 4, 5, 6]):
            rho[1]    += -2.0 * delta[idx]  # rho2
            rho[5 + c] += 2.0 * delta[idx]  # rho6..rho9
        # f_fric → ρ₅
        rho[4] += 2.0 * delta[7]            # rho5

        return rho

    def step(self, rho_measured: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        One UKF predict-update cycle.

        Parameters
        ----------
        rho_measured : (11,) — live parity residual vector in sigma units.
                       NaN for unavailable channels (treated as missing).

        Returns
        -------
        theta_est : (N_THETA,) — posterior mean
        P_diag    : (N_THETA,) — posterior variance diagonal (= σ²)
        """
        n = N_THETA
        sp = self._sigma_points()              # (2n+1, n)

        # ── PREDICT ──────────────────────────────────────────────────────
        # θ_{k+1} = θ_k + w   (slow random walk, w ~ N(0,Q))
        theta_pred = sp.copy()                 # process model is identity
        mean_pred = (self.Wm[:, None] * theta_pred).sum(axis=0)
        P_pred = self.Q.copy()
        for i in range(2 * n + 1):
            d = theta_pred[i] - mean_pred
            P_pred += self.Wc[i] * np.outer(d, d)

        # ── UPDATE ────────────────────────────────────────────────────────
        # Map sigma points through h
        z_sp = np.array([self._h(sp[i]) for i in range(2 * n + 1)])  # (2n+1, 11)

        # Handle missing channels
        obs_mask = ~np.isnan(rho_measured)
        z_meas = np.where(obs_mask, rho_measured, 0.0)

        z_pred = (self.Wm[:, None] * z_sp).sum(axis=0)               # (11,)

        Pzz = self.R.copy()
        Pxz = np.zeros((n, 11))
        for i in range(2 * n + 1):
            dz = z_sp[i] - z_pred
            dx = theta_pred[i] - mean_pred
            Pzz += self.Wc[i] * np.outer(dz, dz)
            Pxz += self.Wc[i] * np.outer(dx, dz)

        # Zero out rows/columns for missing channels
        Pzz_used = Pzz.copy()
        Pzz_used[~obs_mask, :] = 0.0
        Pzz_used[:, ~obs_mask] = 0.0
        for i in np.where(~obs_mask)[0]:
            Pzz_used[i, i] = 1.0            # avoid singular matrix

        try:
            K = Pxz @ np.linalg.inv(Pzz_used)  # Kalman gain (n, 11)
        except np.linalg.LinAlgError:
            K = np.zeros((n, 11))

        innovation = z_meas - z_pred
        innovation[~obs_mask] = 0.0         # mask missing channels

        self.theta = np.clip(mean_pred + K @ innovation, THETA_MIN, THETA_MAX)
        self.P = P_pred - K @ Pzz_used @ K.T
        # Ensure positive semi-definite (numerical symmetrisation)
        self.P = (self.P + self.P.T) / 2.0

        # ── Normalised Innovation Squared (NIS) ───────────────────────────
        # High NIS on one channel = transducer fault (nothing in θ explains it)
        try:
            nis = float(innovation @ np.linalg.inv(Pzz_used) @ innovation)
        except np.linalg.LinAlgError:
            nis = 0.0
        self._nis_history.append(nis)
        if len(self._nis_history) > 100:
            self._nis_history.pop(0)

        return self.theta.copy(), np.diag(self.P).copy()

    def health_frame(self) -> dict:
        """
        Serialise the current θ estimate to the `theta` block of pramana.health.v1.
        """
        return {
            "eta_v_scale":  {"value": round(float(self.theta[0]), 4),
                             "sigma": round(float(np.sqrt(self.P[0, 0])), 4)},
            "eta_c_scale":  {"value": round(float(self.theta[1]), 4),
                             "sigma": round(float(np.sqrt(self.P[1, 1])), 4)},
            "hA_scale":     {"value": round(float(self.theta[2]), 4),
                             "sigma": round(float(np.sqrt(self.P[2, 2])), 4)},
            "cd_inj":       {
                "value": [round(float(self.theta[3 + c]), 4) for c in range(4)],
                "sigma": [round(float(np.sqrt(self.P[3 + c, 3 + c])), 4) for c in range(4)],
            },
            "f_fric_scale": {"value": round(float(self.theta[7]), 4),
                             "sigma": round(float(np.sqrt(self.P[7, 7])), 4)},
        }

    def mean_nis(self, window: int = 20) -> float:
        """Recent mean NIS — elevated if a transducer fault is present."""
        recent = self._nis_history[-window:]
        return float(np.mean(recent)) if recent else 0.0
