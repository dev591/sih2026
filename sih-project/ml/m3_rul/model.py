"""
M3 — RUL heads (two-headed remaining useful life).

Split into its own module per work-breakdown.md directory spec.
The classifier (m3_classifier/) and RUL heads share the same CNN encoder
but are saved and loaded as separate artefacts so either can be retrained
independently when better labelled data arrives from BE-1.

Two heads, always both:
    1. Quantile-regression network — p10 / p50 / p90
       Learned from data; captures distribution uncertainty; degrades
       outside training distribution as any learned model does.
    2. Physics head — integrates identified damage rate forward from
       UKF-estimated health parameters. More graceful extrapolation at
       the cost of whatever the physics model doesn't capture.

Report both. Advise on the conservative. Disagreement beyond the
predictive interval is surfaced as a warning.

See docs/backend/ML-DEEP-DIVE.md §5 and docs/spec/residual-spec.md §6.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import numpy as np


# Quantile levels — always three, always these values.
QUANTILES = [0.1, 0.5, 0.9]


# ── Quantile head ─────────────────────────────────────────────────────────

class QuantileRULHead(nn.Module):
    """
    Multi-quantile regression for RUL. Outputs p10, p50, p90 in hours.
    Receives a pre-computed feature vector from the shared CNN encoder.
    """

    def __init__(self, feature_dim: int):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(feature_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, len(QUANTILES)),
            nn.Softplus(),   # RUL must be non-negative
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.head(features)   # (B, 3) — [p10, p50, p90]


def quantile_loss(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """
    Pinball loss for multi-quantile regression.
    pred   : (B, 3)
    target : (B,)  actual RUL values
    """
    target_exp = target.unsqueeze(1).expand_as(pred)   # (B, 3)
    q = torch.tensor(QUANTILES, device=pred.device)    # (3,)
    diff = target_exp - pred
    return torch.where(diff >= 0, q * diff, (q - 1.0) * diff).mean()


# ── Physics head ──────────────────────────────────────────────────────────

def physics_rul(
    damage_state: float,
    damage_rate_per_hr: float,
    min_rul_h: float = 0.1,
) -> float:
    """
    Integrate the current damage rate forward to predict RUL.

        rul = (1 − D) / (dD/dt)   in hours

    Parameters
    ----------
    damage_state       D ∈ [0, 1] — current accumulated damage (from UKF / BE-1)
    damage_rate_per_hr dD/dt in 1/hr — current rate (from BE-1's damage model)
    """
    if damage_rate_per_hr <= 0:
        return float("inf")
    remaining = max(0.0, 1.0 - damage_state)
    return max(remaining / damage_rate_per_hr, min_rul_h)


# ── RUL report builder ─────────────────────────────────────────────────────

def rul_report(
    p10: float,
    p50: float,
    p90: float,
    physics_h: float,
    component: str = "unknown",
    threshold_disagree: float = 2.0,
) -> dict:
    """
    Build the `rul` block of pramana.health.v1 (telemetry-schema.md).

    Advises on the conservative head (usually network p50 when < physics_h).
    Flags heads_disagree when the gap exceeds the predictive interval.
    """
    reported_h = min(p50, physics_h)
    interval_half = (p90 - p10) / 2.0
    heads_disagree = abs(p50 - physics_h) > max(interval_half * threshold_disagree, 0.5)

    return {
        "component":      component,
        "physics_h":      round(physics_h, 2),
        "network_h":      round(p50, 2),
        "p10_h":          round(p10, 2),
        "p50_h":          round(p50, 2),
        "p90_h":          round(p90, 2),
        "reported_h":     round(reported_h, 2),
        "heads_disagree": heads_disagree,
    }


# ── Standalone RUL model (wraps shared encoder + quantile head) ──────────

class RULModel(nn.Module):
    """
    Standalone model: shares the CNN encoder architecture from M3 classifier
    but is saved/loaded as a separate file (m3_rul_weights.pt).

    Usage:
        rul_model = RULModel(feature_dim=256)
        rul_model.load_state_dict(torch.load('m3_rul_weights.pt'))
        p10, p50, p90 = rul_model.predict(window_tensor)
    """

    def __init__(self, input_dim: int = 11, seq_len: int = 32, feature_dim: int = 256):
        super().__init__()
        # Mirror of ResidualCNNEncoder in m3_classifier
        self.conv = nn.Sequential(
            nn.Conv1d(input_dim, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(4),    # → (B, 64, 4)
        )
        self.rul_head = QuantileRULHead(feature_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, 11) → permute for Conv1d
        h = self.conv(x.permute(0, 2, 1)).flatten(1)   # (B, 256)
        return self.rul_head(h)                          # (B, 3)

    @torch.no_grad()
    def predict(self, x: torch.Tensor) -> tuple[float, float, float]:
        """Single-window inference. Returns (p10, p50, p90) in hours."""
        self.eval()
        if x.dim() == 2:
            x = x.unsqueeze(0)
        out = self.forward(x).squeeze(0).cpu().numpy()
        return float(out[0]), float(out[1]), float(out[2])
