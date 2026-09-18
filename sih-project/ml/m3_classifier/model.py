"""
M3 — Fault Classifier.

1D-CNN over a residual window → softmax across fault classes.
Runs ALONGSIDE the incidence-matrix cosine-similarity match (not instead
of it). The two are NOT statistically independent — training samples are
generated around the incidence columns (ml/data/synthetic.py), so a
classifier that reproduced the matrix exactly would prove nothing.
Training samples are jittered off the nominal columns by a MEASURED
amount (see synthetic.py's direction_jitter), which is what makes the
classifier-vs-matrix agreement rate a real, measured quantity
(ml/weights/m3_classifier_report.json) rather than a guarantee. Quote
that measured rate; do not claim independence.

RUL heads live in ../m3_rul/model.py — separate artefact, separate weights.

See docs/backend/ML-DEEP-DIVE.md §5 for the full argument.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

from ml.incidence import FAULT_NAMES, N_RESIDUALS, INCIDENCE


# ── Shared CNN encoder ────────────────────────────────────────────────────

class ResidualCNNEncoder(nn.Module):
    """
    1D-CNN over a (T, 11) residual window → feature vector.
    Kept small: the task is physically structured so a large model buys
    nothing but overfitting on our limited labelled dataset.
    """

    def __init__(self, input_dim: int = N_RESIDUALS):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(input_dim, 48, kernel_size=3, padding=1),
            nn.BatchNorm1d(48),
            nn.ReLU(),
            nn.Conv1d(48, 96, kernel_size=3, padding=1),
            nn.BatchNorm1d(96),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(4),   # → (B, 96, 4)
        )
        self.feature_dim = 96 * 4      # 384

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, 13)
        #
        # SCALE-NORMALISE THE WINDOW FIRST. Which fault it is lives in the
        # DIRECTION of rho; magnitude carries severity, a nuisance variable
        # for classification (2-6 sigma range in training). Feeding raw
        # magnitudes made the network spend capacity learning scale-
        # invariance it should never have needed — residual-spec.md
        # identifies a fault by which relations depart and with what sign,
        # not by how far.
        scale = x.norm(dim=(1, 2), keepdim=True).clamp_min(1e-6)
        x = x / scale
        # → permute to (B, 11, T) for Conv1d
        return self.conv(x.permute(0, 2, 1)).flatten(1)


# ── Classifier head ───────────────────────────────────────────────────────

class FaultClassifier(nn.Module):
    def __init__(self, feature_dim: int, n_classes: int):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(feature_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(64, n_classes),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.head(features)   # (B, n_classes) raw logits


# ── Full M3 classifier model ──────────────────────────────────────────────

class M3ClassifierModel(nn.Module):
    """
    Shared encoder → classifier head only.
    Saved as m3_classifier_weights.pt.
    """

    def __init__(self, n_fault_classes: int, input_dim: int = N_RESIDUALS):
        super().__init__()
        self.encoder    = ResidualCNNEncoder(input_dim)
        self.classifier = FaultClassifier(self.encoder.feature_dim, n_fault_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.encoder(x))   # (B, n_classes) logits


# ── Incidence-matrix cosine match (zero-training-cost) ───────────────────

def _build_incidence_matrix() -> np.ndarray:
    cols = []
    for name in FAULT_NAMES:
        v = np.array(INCIDENCE[name], dtype=float)
        n = np.linalg.norm(v)
        cols.append(v / n if n > 1e-9 else v)
    return np.column_stack(cols)   # (13, n_faults)


_F_NORM = _build_incidence_matrix()


def incidence_match(rho: np.ndarray) -> np.ndarray:
    """
    Cosine similarity between live residual vector and each fault signature.
    rho : (13,)  in sigma units
    Returns (n_faults,) in [-1, 1]
    """
    rho_norm = rho / (np.linalg.norm(rho) + 1e-9)
    return _F_NORM.T @ rho_norm


def diagnose(
    rho: np.ndarray,
    classifier_probs: np.ndarray,
    alpha: float = 0.6,
) -> list[dict]:
    """
    Combine classifier probability and incidence-matrix cosine score.

    alpha blends the two scores: score = alpha * p_classifier + (1-alpha) * cosine.
    Agreement between two independent mechanisms (learned vs derived) is evidence.
    Returns list sorted by combined score, descending.
    """
    matrix_scores = incidence_match(rho)
    matrix_norm   = (matrix_scores + 1.0) / 2.0   # shift to [0,1]
    combined      = alpha * classifier_probs + (1.0 - alpha) * matrix_norm

    instr = {"map_sensor_drift", "egt_sensor_drift",
             "cht_sensor_drift", "lambda_sensor_drift"}

    return sorted([
        {
            "fault":           FAULT_NAMES[i],
            "p_combined":      float(combined[i]),
            "p_classifier":    float(classifier_probs[i]),
            "cosine_matrix":   float(matrix_scores[i]),
            "source":          (
                "classifier+matrix"
                if (classifier_probs[i] > 0.1 and matrix_scores[i] > 0.1)
                else "matrix"
            ),
        }
        for i in range(len(FAULT_NAMES))
    ], key=lambda d: d["p_combined"], reverse=True)


# ── Training helper ───────────────────────────────────────────────────────

def train_classifier(
    model: M3ClassifierModel,
    train_loader,
    epochs: int = 120,
    lr: float = 2e-3,
    device: str = "cpu",
) -> list[float]:
    model.to(device)
    optimiser = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=epochs)
    history   = []

    for epoch in range(1, epochs + 1):
        model.train()
        total = 0.0
        for x, y_cls in train_loader:
            x, y_cls = x.to(device), y_cls.to(device)
            loss = F.cross_entropy(model(x), y_cls)
            optimiser.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            total += loss.item() * x.size(0)
        scheduler.step()
        avg = total / len(train_loader.dataset)
        history.append(avg)
        if epoch % 10 == 0:
            print(f"[M3-cls] epoch {epoch:3d}/{epochs}  loss={avg:.5f}")

    return history


def train_rul_head(
    rul_model,
    train_loader,
    epochs: int = 60,
    lr: float = 5e-4,
    device: str = "cpu",
) -> list[float]:
    """Train the standalone RUL model from m3_rul."""
    from ml.m3_rul.model import quantile_loss

    rul_model.to(device)
    optimiser = torch.optim.Adam(rul_model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=epochs)
    history   = []

    for epoch in range(1, epochs + 1):
        rul_model.train()
        total = 0.0
        for x, y_cls in train_loader:
            x = x.to(device)
            # Synthetic RUL proxy until BE-1's damage integrator provides labels:
            # higher residual norm → lower RUL (closer to failure)
            rul_proxy = (10.0 - x.abs().mean(dim=(1, 2))).clamp(min=0.1)
            pred = rul_model(x)
            loss = quantile_loss(pred, rul_proxy.to(device))
            optimiser.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(rul_model.parameters(), 1.0)
            optimiser.step()
            total += loss.item() * x.size(0)
        scheduler.step()
        avg = total / len(train_loader.dataset)
        history.append(avg)
        if epoch % 10 == 0:
            print(f"[M3-rul] epoch {epoch:3d}/{epochs}  loss={avg:.5f}")

    return history
