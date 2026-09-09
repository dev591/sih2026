"""
Synthetic residual generator for training M2 and M3.

Produces windows of the 11-element parity residual vector ρ ∈ R^11:

  Healthy:  ρ ~ N(0, I) per component, with mild autocorrelation.
  Faulty:   ρ = α · F_col + N(0, σ_noise · I), where F_col is the
            expanded fault signature column (see incidence.py) and α
            is a severity scalar that ramps from 0 → fault_magnitude
            over an onset_steps window.

The generator does NOT touch raw sensor values — it works entirely in
residual space, which is the contract from docs/spec/residual-spec.md §0.

Usage
-----
    from ml.data.synthetic import SyntheticResidualDataset
    ds = SyntheticResidualDataset(n_healthy=500, n_fault_per_class=200)
    # Returns torch Datasets: ds.healthy, ds.faulty (with labels)
"""

from __future__ import annotations

import numpy as np
import torch
from torch.utils.data import TensorDataset

from ml.incidence import (
    N_RESIDUALS,
    FAULT_NAMES,
    build_fault_matrix_expanded,
)

RNG = np.random.default_rng(42)

# ── correlation kernel so healthy ρ is mildly autocorrelated ──────────────
def _ar1_window(length: int, n_feat: int, phi: float = 0.3,
                rng: np.random.Generator = RNG) -> np.ndarray:
    """
    Simulate an AR(1) window: x_t = phi * x_{t-1} + eps_t.
    Returns (length, n_feat) in normalised units.
    """
    out = np.zeros((length, n_feat))
    out[0] = rng.standard_normal(n_feat)
    for t in range(1, length):
        out[t] = phi * out[t - 1] + np.sqrt(1 - phi**2) * rng.standard_normal(n_feat)
    return out.astype(np.float32)


def generate_healthy_windows(
    n_windows: int = 500,
    window_len: int = 32,
    noise_std: float = 1.0,
    phi: float = 0.3,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Generate healthy residual windows. Shape: (n_windows, window_len, N_RESIDUALS).
    Healthy ρ ≈ 0, mildly autocorrelated, unit-normal distribution.
    """
    if rng is None:
        rng = np.random.default_rng(0)
    windows = np.stack(
        [_ar1_window(window_len, N_RESIDUALS, phi=phi, rng=rng) * noise_std
         for _ in range(n_windows)]
    )
    return windows.astype(np.float32)


def generate_fault_windows(
    n_per_class: int = 200,
    window_len: int = 32,
    noise_std: float = 1.0,
    fault_magnitude_range: tuple[float, float] = (2.0, 6.0),
    onset_fraction: float = 0.25,
    phi: float = 0.3,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Generate labelled fault windows.

    Returns
    -------
    X : (n_classes * n_per_class, window_len, N_RESIDUALS)
    y : (n_classes * n_per_class,)  — integer fault class index
    """
    if rng is None:
        rng = np.random.default_rng(1)

    F_exp, col_labels = build_fault_matrix_expanded()
    # Map each expanded column back to a base-fault class index.
    # We use FAULT_NAMES index, stripping the _cyl suffix.
    def _class_index(label: str) -> int:
        base = label.split("_cyl")[0]
        return FAULT_NAMES.index(base)

    n_cols = F_exp.shape[1]
    onset_steps = max(1, int(window_len * onset_fraction))

    all_X, all_y = [], []

    for col_idx in range(n_cols):
        fault_dir = F_exp[:, col_idx]          # (11,)
        norm = np.linalg.norm(fault_dir)
        if norm < 1e-9:
            continue
        fault_dir_unit = fault_dir / norm

        cls = _class_index(col_labels[col_idx])

        for _ in range(n_per_class):
            magnitude = rng.uniform(*fault_magnitude_range)
            noise = _ar1_window(window_len, N_RESIDUALS, phi=phi, rng=rng) * noise_std

            # Severity ramp: 0 → magnitude over onset_steps, then held.
            ramp = np.minimum(
                np.arange(window_len) / onset_steps, 1.0
            ) * magnitude                     # (window_len,)

            signal = ramp[:, None] * fault_dir_unit[None, :]   # (window_len, 11)
            window = (signal + noise).astype(np.float32)

            all_X.append(window)
            all_y.append(cls)

    X = np.stack(all_X)
    y = np.array(all_y, dtype=np.int64)
    return X, y


class SyntheticResidualDataset:
    """
    Convenience wrapper that holds ready-to-use torch TensorDatasets.

    Attributes
    ----------
    healthy         TensorDataset of (X,)  — reconstruction training for M2
    healthy_val     TensorDataset of (X,)  — held-out for threshold calibration
    faulty          TensorDataset of (X, y) — supervised training for M3
    n_fault_classes int — number of base fault classes (len(FAULT_NAMES))
    """

    def __init__(
        self,
        n_healthy: int = 800,
        n_healthy_val: int = 200,
        n_fault_per_class: int = 300,
        window_len: int = 32,
        noise_std: float = 1.0,
        fault_magnitude_range: tuple[float, float] = (2.0, 6.0),
    ):
        rng_h = np.random.default_rng(10)
        rng_hv = np.random.default_rng(11)
        rng_f = np.random.default_rng(12)

        X_h = generate_healthy_windows(n_healthy, window_len, noise_std, rng=rng_h)
        X_hv = generate_healthy_windows(n_healthy_val, window_len, noise_std, rng=rng_hv)
        X_f, y_f = generate_fault_windows(
            n_fault_per_class, window_len, noise_std,
            fault_magnitude_range=fault_magnitude_range, rng=rng_f
        )

        self.healthy = TensorDataset(torch.from_numpy(X_h))
        self.healthy_val = TensorDataset(torch.from_numpy(X_hv))
        self.faulty = TensorDataset(torch.from_numpy(X_f), torch.from_numpy(y_f))
        self.n_fault_classes = len(FAULT_NAMES)
        self.window_len = window_len

        print(
            f"[SyntheticResidualDataset] "
            f"healthy={len(self.healthy)}  val={len(self.healthy_val)}  "
            f"faulty={len(self.faulty)}  classes={self.n_fault_classes}"
        )
