"""
Health-parameter estimator θ — a Kalman filter on a MEASURED sensitivity.

What this replaces. The previous HealthUKF claimed to propagate sigma points
"through the ACTUAL nonlinear model". It did not: its observation function was
a hand-written linear map with invented coefficients (e.g. friction → ρ₅ +2,
while the engine model's friction actually moves oil pressure, ρ₁₀). Because
that map was linear, the unscented transform added nothing over a plain Kalman
filter either. So θ was not a physical estimate.

Now: h(θ) = J · (θ − θ_nom), where J is MEASURED by perturbing each health
parameter in the engine model and recording how every model input feature
(ml/features.py) moves at thermal steady state — `python -m ml.ukf.sensitivity`
writes ml/weights/v2/jacobian.json. With a linear h the Kalman filter is the
exact Bayesian update, so that is what runs, and it is named for what it is.

Sensor-fault discrimination falls out of the structure: a component fault is
explained by some admissible θ; a transducer fault moves one channel that no θ
can reproduce, which shows up as a large normalised innovation (NIS).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

JACOBIAN_PATH = Path(__file__).resolve().parents[1] / "weights" / "v2" / "jacobian.json"

# name, nominal, lower, upper — bounds are the fault model's own clamps
# (backend/twin/faults.py) with a little headroom. One cd_inj per cylinder.
def params_for(n_cyl: int) -> list[tuple]:
    return ([("eta_v_scale", 1.0, 0.5, 1.1), ("eta_c_scale", 1.0, 0.5, 1.1), ("hA_scale", 1.0, 0.4, 1.2)]
            + [(f"cd_inj_{c}", 1.0, 0.3, 1.1) for c in range(1, n_cyl + 1)]
            + [("f_fric_scale", 1.0, 0.9, 2.4), ("rad_eff_scale", 1.0, 0.4, 1.1),
               ("cool_pump_scale", 1.0, 0.3, 1.1), ("oil_pump_scale", 1.0, 0.1, 1.1),
               ("fuel_rail_scale", 1.0, 0.2, 1.1)])


N_FIXED_PARAMS = 8                      # every parameter that is not a per-cylinder injector
PARAMS = params_for(4)                  # the 4-cylinder list, kept for older callers
THETA_NAMES = [p[0] for p in PARAMS]
THETA_NOM = np.array([p[1] for p in PARAMS])
THETA_MIN = np.array([p[2] for p in PARAMS])
THETA_MAX = np.array([p[3] for p in PARAMS])


class HealthKF:
    def __init__(self, J: np.ndarray | None = None, r_std: np.ndarray | None = None,
                 q: float = 2e-6, p0: float = 0.01):
        if J is None:
            d = json.loads(JACOBIAN_PATH.read_text())
            J, r_std = np.array(d["J"]), np.array(d["r_std"])
        self.J = np.asarray(J, float)                       # (F, P)
        # the number of cylinders is the number of parameters minus the fixed ones
        self.n_cyl = self.J.shape[1] - N_FIXED_PARAMS
        params = params_for(self.n_cyl)
        self.names = [p[0] for p in params]
        self.nom = np.array([p[1] for p in params])
        self.lo = np.array([p[2] for p in params])
        self.hi = np.array([p[3] for p in params])
        self.R = np.diag(np.maximum(np.asarray(r_std, float), 0.05) ** 2)
        self.Q = np.eye(len(params)) * q
        self.p0 = p0
        self.reset()

    def reset(self) -> None:
        self.theta = self.nom.copy()
        self.P = np.eye(len(self.nom)) * self.p0
        self.nis: list[float] = []

    def step(self, z: np.ndarray) -> np.ndarray:
        """z: baseline-subtracted feature vector (ml/features.py order)."""
        self.P = self.P + self.Q
        x = self.theta - self.nom
        S = self.J @ self.P @ self.J.T + self.R
        Si = np.linalg.inv(S)
        K = self.P @ self.J.T @ Si
        innov = z - self.J @ x
        self.theta = np.clip(self.nom + x + K @ innov, self.lo, self.hi)
        self.P = (np.eye(len(self.nom)) - K @ self.J) @ self.P
        self.P = (self.P + self.P.T) / 2.0
        self.nis = (self.nis + [float(innov @ Si @ innov)])[-100:]
        return self.theta.copy()

    def sigma(self) -> np.ndarray:
        return np.sqrt(np.clip(np.diag(self.P), 0, None))

    def mean_nis(self, window: int = 20) -> float:
        return float(np.mean(self.nis[-window:])) if self.nis else 0.0

    def health_frame(self) -> dict:
        """The `theta` block of pramana.health.v1 — the original five keys the
        dashboard reads, plus the parameters the engine model gained since."""
        s = self.sigma()
        v = {n: (round(float(self.theta[i]), 4), round(float(s[i]), 4))
             for i, n in enumerate(self.names)}
        out = {k: {"value": v[k][0], "sigma": v[k][1]}
               for k in ("eta_v_scale", "eta_c_scale", "hA_scale", "f_fric_scale",
                         "rad_eff_scale", "cool_pump_scale", "oil_pump_scale",
                         "fuel_rail_scale")}
        out["cd_inj"] = {"value": [v[f"cd_inj_{c}"][0] for c in range(1, self.n_cyl + 1)],
                         "sigma": [v[f"cd_inj_{c}"][1] for c in range(1, self.n_cyl + 1)]}
        return out
